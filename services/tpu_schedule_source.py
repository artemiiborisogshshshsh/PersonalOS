"""TPU group-page schedule source.

TPU exposes a stable public page for a group and creates an iCal feed URL from
that page.  The feed URL contains a short-lived/shareable export key, therefore
it must never be used as the user's configuration or be printed in logs.  This
module keeps the stable group page as configuration and discovers the current
feed only in memory immediately before downloading it.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse
import re
from hashlib import sha256
import json
import logging
import socket
import time
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

from services.university_schedule_source import (
    UniversityScheduleFetchError,
    UniversityScheduleFetchResult,
    _get_with_timeout_retry,
    fetch_university_schedule,
    save_university_schedule,
)


TPU_HOST = 'ro-rasp.tpu.ru'
_GROUP_PAGE_PATTERN = re.compile(r'^/gruppa_\d+/\d+/\d+/view\.html$')
_MARKDOWN_LINK_PATTERN = re.compile(r'^\[[^\]]*\]\((https?://[^)]+)\)$')
_TPU_PROXIES = json.loads(Path(__file__).with_name('tpu_proxies.json').read_text())
_HTTP_URL_PATTERN = re.compile(r'https?://[^\s\"\'<>]+')
logger = logging.getLogger(__name__)


def _error_chain(error):
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend((current.__cause__, current.__context__))
        reason = getattr(current, 'reason', None)
        if isinstance(reason, BaseException):
            pending.append(reason)
        pending.extend(arg for arg in current.args if isinstance(arg, BaseException))


def _safe_error_label(error):
    chain = tuple(_error_chain(error))
    if any(isinstance(item, socket.gaierror) for item in chain):
        return 'dns_error'
    if any(isinstance(item, requests.exceptions.SSLError) for item in chain):
        return 'tls_error'
    if any(isinstance(item, requests.exceptions.ConnectTimeout) for item in chain):
        return 'connect_timeout'
    if any(isinstance(item, requests.exceptions.ReadTimeout) for item in chain):
        return 'read_timeout'
    if any(isinstance(item, requests.exceptions.Timeout) for item in chain):
        return 'timeout'
    if any(isinstance(item, requests.exceptions.HTTPError) for item in chain):
        return 'http_error'
    if any(isinstance(item, requests.exceptions.ConnectionError) for item in chain):
        return 'connection_error'
    if any(isinstance(item, (TypeError, ValueError)) for item in chain):
        return 'invalid_response'
    if isinstance(error, UniversityScheduleFetchError):
        return 'schedule_validation'
    return 'unexpected_error'


def _http_status(error):
    for item in _error_chain(error):
        response = getattr(item, 'response', None)
        status = getattr(response, 'status_code', None)
        if type(status) is int and 100 <= status <= 599:
            return status
    return None


def _warn_fetch_failure(stage, export, started, error):
    status = _http_status(error)
    logger.warning(
        'TPU fetch failed stage=%s export=%s elapsed_ms=%d error=%s http_status=%s',
        stage, export, max(0, int((time.perf_counter() - started) * 1000)),
        _safe_error_label(error), status if status is not None else 'none',
    )


@contextmanager
def _diagnostic_stage(stage, export):
    started = time.perf_counter()
    try:
        yield
    except Exception as error:
        if stage == 'ics_fetch':
            stage = ('ics_download' if any(
                isinstance(cause, requests.RequestException) for cause in _error_chain(error)
            ) else 'validation')
        _warn_fetch_failure(stage, export, started, error)
        raise


class _TpuSession(requests.Session):
    """Use only the supplied proxies for TPU, rotating on proxy connection errors."""

    def __init__(self):
        super().__init__()
        self.trust_env = False
        self._proxy_index = 0

    def request(self, method, url, **kwargs):
        for attempt in range(len(_TPU_PROXIES)):
            proxy = _TPU_PROXIES[self._proxy_index]
            kwargs['proxies'] = {'http': proxy, 'https': proxy}
            timeout = kwargs.get('timeout')
            if isinstance(timeout, (int, float)):
                kwargs['timeout'] = (min(timeout, 3), timeout)
            try:
                return super().request(method, url, **kwargs)
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectTimeout):
                self._proxy_index = (self._proxy_index + 1) % len(_TPU_PROXIES)
                if attempt == len(_TPU_PROXIES) - 1:
                    raise


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
    export_label: str = 'current',
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
    export_label = export_label if export_label in {'current', 'next', 'selected'} else 'selected'
    client = session or _TpuSession()
    headers = {'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.1'}
    try:
        with _diagnostic_stage('group_get', export_label):
            page_response = _get_with_timeout_retry(
                client, view_url, timeout_seconds=timeout_seconds, headers=headers,
            )
            page_response.raise_for_status()
        with _diagnostic_stage('group_parse', export_label):
            page = _parse_html(page_response.content)
            source_url, action_url, calendar_key = _modal_urls(page, view_url)
        with _diagnostic_stage('modal_get', export_label):
            source_response = _get_with_timeout_retry(
                client, source_url, timeout_seconds=timeout_seconds, headers=headers,
            )
            source_response.raise_for_status()
        with _diagnostic_stage('modal_parse', export_label):
            candidate = next(_ical_candidates(_parse_html(source_response.content), source_url), None)
            if candidate and not action_url:
                return candidate
            if not action_url:
                raise UniversityScheduleFetchError(
                    'TPU did not return an iCal link after submitting its export form.'
                )
        # This is the same save action as the public TPU export dialog.
        # The session carries any ordinary anti-CSRF cookies set by the
        # group page.  The generated key stays in memory only.
        with _diagnostic_stage('form_post', export_label):
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
        with _diagnostic_stage('form_parse', export_label):
            candidate = next(_ical_candidates(_parse_html(action_response.content), action_url), None)
            if candidate:
                return candidate
            diagnostic = _safe_export_response_message(action_response.content)
            suffix = f' Ответ TPU: {diagnostic}' if diagnostic else ''
            raise UniversityScheduleFetchError(
                'TPU did not return an iCal link after submitting its export form.'
                + suffix
            )
    except requests.RequestException as error:
        raise UniversityScheduleFetchError(
            'Could not obtain the current TPU iCal export link'
        ) from error


def _merge_tpu_calendars(calendars: Iterable[Calendar]) -> Calendar:
    """Combine overlapping exports without dropping distinct recurrence instances.

    The same UID/recurrence ID with differing lesson data is ambiguous: preserve
    the existing snapshot by failing rather than choosing an arbitrary export.
    Export timestamps do not count as lesson changes.
    """
    merged = None
    events = {}
    semantic_events = set()
    other_components = set()
    timezones = {}
    for calendar in calendars:
        if merged is None:
            merged = deepcopy(calendar)
            merged.subcomponents = []
        semantic_seen_in_calendar = set()
        identities_seen_in_calendar = set()
        for component in calendar.subcomponents:
            if component.name == 'VEVENT':
                recurrence_id = component.decoded('RECURRENCE-ID', None)
                identity = (str(component['UID']), recurrence_id)
                if identity in identities_seen_in_calendar:
                    raise UniversityScheduleFetchError(
                        'TPU export contains duplicate event identities'
                    )
                identities_seen_in_calendar.add(identity)
                comparable = deepcopy(component)
                for field in ('DTSTAMP', 'CREATED', 'LAST-MODIFIED'):
                    comparable.pop(field, None)
                encoded = comparable.to_ical()
                if identity in events:
                    if events[identity] != encoded:
                        raise UniversityScheduleFetchError(
                            'TPU exports contain conflicting versions of the same event'
                        )
                    continue
                semantic_key = _tpu_semantic_properties(
                    component, include_status_sequence=True,
                )
                if semantic_key in semantic_seen_in_calendar:
                    raise UniversityScheduleFetchError(
                        'TPU export contains indistinguishable schedule events'
                    )
                semantic_seen_in_calendar.add(semantic_key)
                if semantic_key in semantic_events:
                    events[identity] = encoded
                    continue
                semantic_events.add(semantic_key)
                events[identity] = encoded
            else:
                encoded = component.to_ical()
                if component.name == 'VTIMEZONE':
                    identity = str(component.get('TZID', ''))
                    if identity in timezones and timezones[identity] != encoded:
                        raise UniversityScheduleFetchError('TPU exports contain conflicting time zones')
                    timezones[identity] = encoded
                if encoded in other_components:
                    continue
                other_components.add(encoded)
            merged.add_component(deepcopy(component))
    if merged is None:
        raise UniversityScheduleFetchError('TPU returned no calendar exports')
    return merged


_TPU_VOLATILE_PROPERTIES = {'UID', 'DTSTAMP', 'CREATED', 'LAST-MODIFIED'}
_TPU_IDENTITY_IGNORED_PROPERTIES = _TPU_VOLATILE_PROPERTIES | {'SEQUENCE', 'STATUS'}


def _tpu_semantic_properties(component, *, include_status_sequence=False) -> str:
    ignored = (_TPU_VOLATILE_PROPERTIES if include_status_sequence
               else _TPU_IDENTITY_IGNORED_PROPERTIES)
    properties = []
    for name, value in component.property_items():
        name = str(name).upper()
        if name in ignored or name in {'BEGIN', 'END'}:
            continue
        encoded = value.to_ical() if hasattr(value, 'to_ical') else str(value)
        if isinstance(encoded, str):
            encoded = encoded.encode('utf-8')
        elif not isinstance(encoded, bytes):
            encoded = bytes(encoded)
        properties.append((name, encoded.hex()))
    return json.dumps(sorted(properties), ensure_ascii=False, separators=(',', ':'))


def tpu_group_identity_namespace(view_url: str) -> str:
    """Return the stable group/year namespace, excluding the rolling week."""
    parsed = urlparse(validate_tpu_group_page_url(view_url))
    group, year, _week, _view = parsed.path.strip('/').split('/')
    return f'tpu:{group}:{year}'


def _normalize_tpu_event_uids(calendar: Calendar, identity_namespace: str) -> None:
    """Replace regenerated TPU UIDs with deterministic semantic identities.

    The identity includes every VEVENT property except the source UID and
    export/version metadata. If two events are indistinguishable on those
    fields, fail closed rather than collapsing separate timetable entries.
    """
    seen: set[str] = set()
    for component in calendar.walk():
        if component.name != 'VEVENT':
            continue
        identity = json.dumps([identity_namespace, _tpu_semantic_properties(component)],
                              ensure_ascii=False, separators=(',', ':'))
        stable_uid = 'tpu-' + sha256(identity.encode('utf-8')).hexdigest() + '@personalos.invalid'
        if stable_uid in seen:
            raise UniversityScheduleFetchError(
                'TPU export contains indistinguishable schedule events'
            )
        seen.add(stable_uid)
        component['UID'] = stable_uid


def _normalize_tpu_schedule_file(
    path: Path, source_url: str, identity_namespace: str,
) -> UniversityScheduleFetchResult:
    calendar = Calendar.from_ical(path.read_bytes())
    _normalize_tpu_event_uids(calendar, identity_namespace)
    return save_university_schedule(calendar.to_ical(), path, source_url)


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
    holidays. The automatic two-week mode always combines exports from the
    current and following academic pages, so a weekday refresh does not drop
    the supplemental page fetched on the weekend. This relies on TPU anchoring
    exports to the selected page; it cannot prove completeness when TPU omits
    future classes.
    Manual page selection and explicit one-week/month variants keep their range.
    """
    client = session or _TpuSession()
    normalized_view_url = validate_tpu_group_page_url(view_url)
    reference_time = now or datetime.now(timezone.utc)
    candidate = normalized_view_url
    if auto_period:
        candidate = current_tpu_group_page_url(normalized_view_url, reference_time, week_one_start)
    if auto_period and export_variant_id == 2:
        # Stage every export before touching the published file. A failed second
        # download must not publish only the first half of the requested range.
        with TemporaryDirectory(prefix='tpu-schedule-') as staging:
            calendars = []
            for index, page in enumerate((candidate, alternate_tpu_group_page_url(candidate))):
                export_label = 'current' if index == 0 else 'next'
                ical_url = discover_tpu_ical_url(
                    page, timeout_seconds, client, export_variant_id, export_label,
                )
                staged = Path(staging) / f'{index}.ics'
                with _diagnostic_stage('ics_fetch', export_label):
                    fetch_university_schedule(
                        ical_url, staged, timeout_seconds, session=client,
                        allow_empty=True,
                    )
                with _diagnostic_stage('parse_merge', export_label):
                    calendars.append(Calendar.from_ical(staged.read_bytes()))
            with _diagnostic_stage('parse_merge', 'current+next'):
                combined = _merge_tpu_calendars(calendars)
                _normalize_tpu_event_uids(
                    combined, tpu_group_identity_namespace(normalized_view_url),
                )
                serialized = combined.to_ical()
            with _diagnostic_stage('validation', 'current+next'):
                return save_university_schedule(serialized, output_path, candidate)
    export_label = 'current' if auto_period else 'selected'
    ical_url = discover_tpu_ical_url(
        candidate, timeout_seconds, client, export_variant_id, export_label,
    )
    with _diagnostic_stage('ics_fetch', export_label):
        fetch_university_schedule(ical_url, output_path, timeout_seconds, session=client)
    # TPU regenerates UIDs for identical exports. Normalize only this source
    # type; ordinary ICS imports retain their publisher's UIDs.
    with _diagnostic_stage('parse_merge', export_label):
        return _normalize_tpu_schedule_file(
            Path(output_path), candidate, tpu_group_identity_namespace(normalized_view_url),
        )
