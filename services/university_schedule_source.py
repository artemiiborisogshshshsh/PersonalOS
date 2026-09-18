"""Safe ingestion of an official university iCalendar feed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Union
from urllib.parse import urlparse
import os

import requests
from icalendar import Calendar


class UniversityScheduleFetchError(RuntimeError):
    """Raised when the university source cannot provide a valid ICS feed."""


_SCHEDULE_IDENTITY_FIELDS = (
    'UID', 'RECURRENCE-ID', 'SEQUENCE', 'STATUS', 'SUMMARY', 'DESCRIPTION',
    'LOCATION', 'DTSTART', 'DTEND',
)


def schedule_content_hash(calendar: Calendar) -> str:
    """Hash actual class data, ignoring volatile ICS export metadata.

    TPU regenerates ``DTSTAMP``/``PRODID`` on every download. Those fields do
    not represent a moved, cancelled or edited lesson and must not trigger a
    destructive replan.
    """
    events: list[bytes] = []
    for component in calendar.walk():
        if component.name != 'VEVENT':
            continue
        fields = []
        for name in _SCHEDULE_IDENTITY_FIELDS:
            value = component.get(name)
            if value is not None:
                encoded = value.to_ical() if hasattr(value, 'to_ical') else str(value).encode()
                fields.append(name.encode() + b'=' + encoded)
        events.append(b'\n'.join(fields))
    return sha256(b'\n\n'.join(sorted(events))).hexdigest()


def schedule_hash_from_ics(content: bytes) -> str:
    """Return the semantic schedule hash for an already-downloaded ICS file."""
    try:
        calendar = Calendar.from_ical(content)
    except (TypeError, ValueError) as error:
        raise UniversityScheduleFetchError('The university source did not return a valid ICS calendar') from error
    return schedule_content_hash(calendar)


def _validate_schedule_events(calendar: Calendar) -> int:
    """Reject structurally incomplete exports before replacing a known snapshot."""
    events = [component for component in calendar.walk() if component.name == 'VEVENT']
    if not events:
        raise UniversityScheduleFetchError('The university schedule feed contains no events')
    for component in events:
        if not component.get('UID') or component.get('DTSTART') is None or component.get('DTEND') is None:
            raise UniversityScheduleFetchError('The university schedule feed contains an incomplete event')
        try:
            start = component.decoded('DTSTART')
            end = component.decoded('DTEND')
            if isinstance(start, date) and not isinstance(start, datetime):
                start = datetime.combine(start, time.min)
            if isinstance(end, date) and not isinstance(end, datetime):
                end = datetime.combine(end, time.min)
        except (TypeError, ValueError) as error:
            raise UniversityScheduleFetchError('The university schedule feed contains an unreadable event time') from error
        if end <= start:
            raise UniversityScheduleFetchError('The university schedule feed contains an event with an invalid interval')
    return len(events)


@dataclass(frozen=True)
class UniversityScheduleFetchResult:
    source_url: str
    output_path: Path
    event_count: int
    content_hash: str
    fetched_at: datetime


def fetch_university_schedule(
    source_url: str,
    output_path: Union[str, Path],
    timeout_seconds: float = 20.0,
    session: requests.Session | None = None,
) -> UniversityScheduleFetchResult:
    """Download, validate and atomically save an iCalendar schedule feed."""
    parsed_url = urlparse(source_url)
    if parsed_url.scheme != 'https' or not parsed_url.netloc:
        raise ValueError('University schedule source must be an HTTPS URL')
    if timeout_seconds <= 0:
        raise ValueError('timeout_seconds must be positive')

    try:
        client = session or requests
        response = client.get(
            source_url,
            timeout=timeout_seconds,
            headers={'Accept': 'text/calendar, text/plain;q=0.9, */*;q=0.1'},
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise UniversityScheduleFetchError(
            'Could not download the university schedule feed'
        ) from error

    content = response.content
    try:
        calendar = Calendar.from_ical(content)
    except (TypeError, ValueError) as error:
        raise UniversityScheduleFetchError(
            'The university source did not return a valid ICS calendar'
        ) from error

    event_count = _validate_schedule_events(calendar)

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(
            mode='wb', dir=destination.parent, delete=False,
            prefix=f'.{destination.name}.', suffix='.tmp',
        ) as temporary_file:
            temporary_file.write(content)
            temp_path = Path(temporary_file.name)
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    return UniversityScheduleFetchResult(
        source_url=source_url,
        output_path=destination,
        event_count=event_count,
        content_hash=schedule_content_hash(calendar),
        fetched_at=datetime.now(timezone.utc),
    )
