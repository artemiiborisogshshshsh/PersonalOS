"""TPU group-page schedule source.

TPU exposes a stable public page for a group and creates an iCal feed URL from
that page.  The feed URL contains a short-lived/shareable export key, therefore
it must never be used as the user's configuration or be printed in logs.  This
module keeps the stable group page as configuration and discovers the current
feed only in memory immediately before downloading it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse
import re
from zoneinfo import ZoneInfo

import requests

from services.university_schedule_source import (
    UniversityScheduleFetchError,
    UniversityScheduleFetchResult,
    fetch_university_schedule,
)


TPU_HOST = 'ro-rasp.tpu.ru'
_GROUP_PAGE_PATTERN = re.compile(r'^/gruppa_\d+/\d+/\d+/view\.html$')
_MARKDOWN_LINK_PATTERN = re.compile(r'^\[[^\]]*\]\((https?://[^)]+)\)$')
_HTTP_URL_PATTERN = re.compile(r'https?://[^\s\"\'<>]+')


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes: list[dict[str, str]] = []
        self.text_parts: list[str] = []
        self.raw_text = ''

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.attributes.append({key: value or '' for key, value in attrs})

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)


def normalize_tpu_group_page_url(view_url: str) -> str:
    """Accept a URL pasted from Markdown without storing the Markdown wrapper."""
    candidate = view_url.strip()
    match = _MARKDOWN_LINK_PATTERN.fullmatch(candidate)
    return match.group(1) if match else candidate


def validate_tpu_group_page_url(view_url: str) -> str:
    """Accept only the public TPU group page, never an arbitrary redirect."""
    normalized = normalize_tpu_group_page_url(view_url)
    parsed = urlparse(normalized)
    if (
        parsed.scheme != 'https'
        or parsed.hostname != TPU_HOST
        or not _GROUP_PAGE_PATTERN.fullmatch(parsed.path)
    ):
        raise ValueError(
            'TPU source must be an HTTPS group page like '
            'https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html'
        )
    return normalized


def alternate_tpu_group_page_url(view_url: str) -> str:
    """Compatibility helper: return the following academic week."""
    normalized = validate_tpu_group_page_url(view_url)
    parsed = urlparse(normalized)
    parts = parsed.path.strip('/').split('/')
    parts[-2] = str(int(parts[-2]) + 1)
    return parsed._replace(path='/' + '/'.join(parts)).geturl()


def current_tpu_group_page_url(view_url: str, now: datetime,
                               week_one_start: date | None = None) -> str:
    """Resolve the academic week, never increment once per process run.

    Default anchor is the Monday of September 1's week in the configured
    academic year. An explicit anchor supports other academic calendars.
    The configured year remains unchanged across New Year.
    """
    parsed = urlparse(validate_tpu_group_page_url(view_url))
    parts = parsed.path.strip('/').split('/')
    anchor = week_one_start or date(int(parts[-3]), 9, 1)
    anchor -= timedelta(days=anchor.weekday())
    local_date = now.astimezone(ZoneInfo('Asia/Tomsk')).date() if now.tzinfo else now.date()
    parts[-2] = str(max(1, (local_date - anchor).days // 7 + 1))
    return parsed._replace(path='/' + '/'.join(parts)).geturl()


def _has_nearby_events(ics_path: Path, now: datetime) -> bool:
    """Whether an exported calendar is useful for the current period."""
    from icalendar import Calendar

    calendar = Calendar.from_ical(ics_path.read_bytes())
    threshold = now.astimezone(timezone.utc) - timedelta(days=1)
    for component in calendar.walk():
        if component.name != 'VEVENT' or component.get('DTSTART') is None:
            continue
        value = component.decoded('DTSTART')
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, time.min, tzinfo=timezone.utc)
        elif value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        if value >= threshold:
            return True
    return False


def _parse_html(content: bytes) -> _LinkCollector:
    parser = _LinkCollector()
    parser.raw_text = content.decode('utf-8', errors='replace')
    parser.feed(parser.raw_text)
    return parser


def _same_tpu_ical_url(candidate: str, base_url: str) -> str | None:
    # JSON API responses commonly escape forward slashes and ampersands.
    decoded = unescape(candidate).replace(r'\/', '/').replace(r'\u0026', '&')
    absolute = urljoin(base_url, decoded.strip())
    parsed = urlparse(absolute)
    if (
        parsed.scheme != 'https'
        or parsed.hostname != TPU_HOST
        or parsed.path != '/export/ical.html'
        or not parse_qs(parsed.query).get('key')
    ):
        return None
    return absolute


def _ical_candidates(page: _LinkCollector, base_url: str) -> Iterable[str]:
    for attrs in page.attributes:
        for value in attrs.values():
            candidate = _same_tpu_ical_url(value, base_url)
            if candidate:
                yield candidate
    # Some versions of the TPU modal render the link as text rather than href.
    raw_content = page.raw_text.replace(r'\/', '/').replace(r'\u0026', '&')
    for candidate in re.findall(
        r'(?:https?://[^\s\"\'<>]+|/export/ical\.html\?[^\s\"\'<>]+)',
        raw_content + ''.join(page.text_parts),
    ):
        safe = _same_tpu_ical_url(candidate, base_url)
        if safe:
            yield safe


def _safe_export_response_message(content: bytes) -> str:
    """Return a short TPU validation message without leaking export links."""
    page = _parse_html(content)
    message = ' '.join((page.raw_text + ' '.join(page.text_parts)).split())
    message = _HTTP_URL_PATTERN.sub('[скрытая ссылка]', message)
    # JSON can contain the generated URL without a literal http prefix.
    message = re.sub(
        r'(?P<name>key|sig|token)\\?"?\s*[:=]\s*"?[^,}\s]+',
        r'\g<name>=[скрыто]', message,
    )
    return message[:500] or 'пустой ответ сервера'


def _modal_urls(page: _LinkCollector, view_url: str) -> tuple[str, str | None, str]:
    for attrs in page.attributes:
        source = attrs.get('data-source', '')
        if '/app/modal/source.html' not in source:
            continue
        action = attrs.get('data-action') or None
        key = parse_qs(urlparse(unescape(source)).query).get('kalendar', [''])[0]
        if not key:
            continue
        return (
            urljoin(view_url, unescape(source)),
            urljoin(view_url, unescape(action)) if action else None,
            key,
        )
    raise UniversityScheduleFetchError(
        'TPU group page does not expose an iCal export control'
    )


def discover_tpu_ical_url(
    view_url: str,
    timeout_seconds: float = 20.0,
    session: requests.Session | None = None,
    export_variant_id: int = 2,
) -> str:
    """Find the current iCal URL without persisting or displaying its key.

    TPU's form creates the link after the export variant has been selected and
    the schedule-change acknowledgement is submitted. ``2`` is TPU's “На две
    недели” option, which covers the current and following planning week.
    Older TPU pages can still return an existing link from the source modal.
    Both endpoints are constrained to the same TPU origin.
    """
    view_url = validate_tpu_group_page_url(view_url)
    if timeout_seconds <= 0:
        raise ValueError('timeout_seconds must be positive')
    if export_variant_id not in {1, 2, 3}:
        raise ValueError('TPU export_variant_id must be 1, 2 or 3')
    client = session or requests.Session()
    headers = {'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.1'}
    diagnostic = ''
    try:
        page_response = client.get(view_url, timeout=timeout_seconds, headers=headers)
        page_response.raise_for_status()
        page = _parse_html(page_response.content)
        source_url, action_url, calendar_key = _modal_urls(page, view_url)
        source_response = client.get(
            source_url, timeout=timeout_seconds, headers=headers,
        )
        source_response.raise_for_status()
        candidate = next(_ical_candidates(
            _parse_html(source_response.content), source_url,
        ), None)
        if candidate and not action_url:
            return candidate
        if action_url:
            # This is the same save action as the public TPU export dialog.
            # The session carries any ordinary anti-CSRF cookies set by the
            # group page.  The generated key stays in memory only.
            action_response = client.post(
                action_url,
                data={
                    'GroupExportForm[kalendar]': calendar_key,
                    'GroupExportForm[variant_id]': str(export_variant_id),
                    'GroupExportForm[agree]': '1',
                },
                timeout=timeout_seconds,
                headers={
                    **headers,
                    'Accept': 'application/json,text/html;q=0.9,*/*;q=0.1',
                    'Referer': view_url,
                    'X-Requested-With': 'XMLHttpRequest',
                },
            )
            action_response.raise_for_status()
            candidate = next(_ical_candidates(
                _parse_html(action_response.content), action_url,
            ), None)
            if candidate:
                return candidate
            diagnostic = _safe_export_response_message(action_response.content)
    except requests.RequestException as error:
        raise UniversityScheduleFetchError(
            'Could not obtain the current TPU iCal export link'
        ) from error
    suffix = f' Ответ TPU: {diagnostic}' if diagnostic else ''
    raise UniversityScheduleFetchError(
        'TPU did not return an iCal link after submitting its export form.' + suffix
    )


def fetch_tpu_group_schedule(
    view_url: str,
    output_path: str | Path,
    timeout_seconds: float = 20.0,
    session: requests.Session | None = None,
    export_variant_id: int = 2,
    auto_period: bool = False,
    now: datetime | None = None,
    week_one_start: date | None = None,
) -> UniversityScheduleFetchResult:
    """Refresh a schedule from a TPU group page and keep only that page as ID.

    ``auto_period`` is the legacy option name for automatic academic weeks.
    Select the current week directly, including when a feed is empty due to
    holidays. Export range is controlled separately by export_variant_id.
    """
    client = session or requests.Session()
    normalized_view_url = validate_tpu_group_page_url(view_url)
    reference_time = now or datetime.now(timezone.utc)
    candidate = normalized_view_url
    if auto_period:
        candidate = current_tpu_group_page_url(normalized_view_url, reference_time, week_one_start)
    ical_url = discover_tpu_ical_url(candidate, timeout_seconds, client, export_variant_id)
    result = fetch_university_schedule(ical_url, output_path, timeout_seconds, session=client)
    # Return only the public page, never the temporary export credential.
    return replace(result, source_url=candidate)
