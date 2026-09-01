"""Resilience tests for university ICS ingestion."""

from datetime import datetime, timedelta, timezone

from icalendar import Calendar, Event

from scripts.parse_ics import parse_ics


def test_cancelled_tombstone_without_dtend_is_parsed_safely(tmp_path):
    calendar = Calendar()
    event = Event()
    start = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    event.add('uid', 'cancelled-uid')
    event.add('summary', 'Математика (ЛК) 8И41')
    event.add('dtstart', start)
    event.add('status', 'CANCELLED')
    event.add('sequence', 3)
    calendar.add_component(event)
    path = tmp_path / 'cancelled.ics'
    path.write_bytes(calendar.to_ical())

    parsed = parse_ics(path)

    assert len(parsed) == 1
    assert parsed[0]['status'] == 'cancelled'
    assert parsed[0]['sequence'] == 3
    assert parsed[0]['dtend'] == start
    assert parsed[0]['description'] == ''
    assert parsed[0]['location'] == ''


def test_duration_is_used_when_dtend_is_absent(tmp_path):
    calendar = Calendar()
    event = Event()
    start = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    event.add('uid', 'duration-uid')
    event.add('summary', 'Физика 8И41')
    event.add('dtstart', start)
    event.add('duration', timedelta(minutes=90))
    calendar.add_component(event)
    path = tmp_path / 'duration.ics'
    path.write_bytes(calendar.to_ical())

    parsed = parse_ics(path)

    assert parsed[0]['dtend'] == start + timedelta(minutes=90)


def test_event_without_uid_is_ignored(tmp_path):
    calendar = Calendar()
    event = Event()
    event.add('summary', 'Malformed 8И41')
    event.add('dtstart', datetime(2026, 9, 1, 10, 0))
    calendar.add_component(event)
    path = tmp_path / 'malformed.ics'
    path.write_bytes(calendar.to_ical())

    assert parse_ics(path) == []
