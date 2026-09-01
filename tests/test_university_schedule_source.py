from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime, timezone

import pytest
from icalendar import Calendar, Event

from services.university_schedule_source import (
    UniversityScheduleFetchError,
    fetch_university_schedule,
)


def ics_payload() -> bytes:
    calendar = Calendar()
    event = Event()
    event.add('uid', 'source-event')
    event.add('summary', 'Математика (ЛК) 8И41')
    event.add('dtstart', datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc))
    event.add('dtend', datetime(2026, 9, 1, 11, 30, tzinfo=timezone.utc))
    calendar.add_component(event)
    return calendar.to_ical()


def test_fetch_validates_and_atomically_writes_ics(tmp_path):
    response = Mock(content=ics_payload())
    response.raise_for_status.return_value = None
    output = tmp_path / 'schedule.ics'

    with patch('services.university_schedule_source.requests.get', return_value=response):
        result = fetch_university_schedule('https://example.test/schedule.ics', output)

    assert output.read_bytes() == response.content
    assert result.output_path == output
    assert result.event_count == 1
    assert len(result.content_hash) == 64


def test_fetch_does_not_overwrite_existing_file_with_invalid_ics(tmp_path):
    output = Path(tmp_path / 'schedule.ics')
    output.write_text('existing schedule', encoding='utf-8')
    response = Mock(content=b'not a calendar')
    response.raise_for_status.return_value = None

    with patch('services.university_schedule_source.requests.get', return_value=response):
        with pytest.raises(UniversityScheduleFetchError):
            fetch_university_schedule('https://example.test/schedule.ics', output)

    assert output.read_text(encoding='utf-8') == 'existing schedule'


def test_fetch_rejects_non_http_source(tmp_path):
    with pytest.raises(ValueError, match='HTTP'):
        fetch_university_schedule('file:///tmp/schedule.ics', tmp_path / 'out.ics')
