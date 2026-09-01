"""Safe ingestion of an official university iCalendar feed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
) -> UniversityScheduleFetchResult:
    """Download, validate and atomically save an iCalendar schedule feed."""
    parsed_url = urlparse(source_url)
    if parsed_url.scheme not in {'http', 'https'} or not parsed_url.netloc:
        raise ValueError('University schedule source must be an HTTP(S) URL')
    if timeout_seconds <= 0:
        raise ValueError('timeout_seconds must be positive')

    try:
        response = requests.get(
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

    event_count = sum(
        1 for component in calendar.walk() if component.name == 'VEVENT'
    )
    if event_count == 0:
        raise UniversityScheduleFetchError(
            'The university schedule feed contains no events'
        )

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
        content_hash=sha256(content).hexdigest(),
        fetched_at=datetime.now(timezone.utc),
    )
