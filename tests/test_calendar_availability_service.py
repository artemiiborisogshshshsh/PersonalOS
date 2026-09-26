from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from services.calendar_availability_service import CalendarAvailabilityService, CalendarBusyEvent
from services.update_all_workflow import UpdateAllBlocked


def _event(event_id, title, start, end, description=''):
    return {
        'id': event_id, 'summary': title, 'description': description,
        'start': {'dateTime': start}, 'end': {'dateTime': end},
    }


def test_all_visible_google_calendar_events_are_hard_commitments():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [
        {'id': 'work', 'summary': 'Работа'}, {'id': 'personal', 'summary': 'личное'},
    ]
    adapter.list_events_in_calendar.side_effect = [
        [_event('hard', 'Встреча', '2026-09-03T10:00:00+07:00', '2026-09-03T11:00:00+07:00')],
        [
            _event('soft', 'Друзья', '2026-09-03T12:00:00+07:00', '2026-09-03T13:00:00+07:00'),
            _event('draft', 'Проект', '2026-09-03T14:00:00+07:00', '2026-09-03T15:00:00+07:00',
                   'AI Calendar Block: project\nStatus: draft'),
        ],
    ]
    service = CalendarAvailabilityService(adapter)
    events = service.load(
        datetime(2026, 9, 3, tzinfo=timezone.utc), datetime(2026, 9, 4, tzinfo=timezone.utc),
    )

    commitments = service.hard_commitments(events)
    drafts = service.movable_system_items(events)

    assert [item.title for item in commitments] == ['Встреча', 'Друзья', 'Проект']
    assert drafts == []
    assert next(item for item in events if item.event_id == 'soft').movable_with_confirmation


def test_university_calendars_skip_lessons_but_keep_system_preparations_busy():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [
        {'id': 'university', 'summary': 'University Schedule'},
        {'id': 'personal-university', 'summary': 'Personal University Schedule'},
    ]
    adapter.list_events_in_calendar.side_effect = [
        [_event('source', 'Лекция', '2026-09-03T10:00:00+07:00', '2026-09-03T11:00:00+07:00')],
        [
            _event('projection', 'Лабораторная', '2026-09-03T12:00:00+07:00', '2026-09-03T13:00:00+07:00',
                   'Стабильный ID личного события: source-1'),
            _event('manual', 'Личная встреча', '2026-09-03T16:00:00+07:00', '2026-09-03T17:00:00+07:00'),
            _event('prep', 'Подготовка', '2026-09-03T14:00:00+07:00', '2026-09-03T14:20:00+07:00',
                   'AI Calendar Block: university-prep\nStatus: draft'),
        ],
    ]
    service = CalendarAvailabilityService(adapter)

    events = service.load(
        datetime(2026, 9, 3, tzinfo=timezone.utc),
        datetime(2026, 9, 4, tzinfo=timezone.utc),
    )

    assert [event.event_id for event in events] == ['manual', 'prep']


def test_zero_duration_calendar_events_are_omitted_by_instant_including_offset_equivalence():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'personal', 'summary': 'личное'}]
    adapter.list_events_in_calendar.return_value = [
        _event('zero', 'Нулевая встреча', '2026-09-03T10:00:00+07:00', '2026-09-03T03:00:00Z'),
        _event('positive', 'Встреча', '2026-09-03T10:00:00+07:00', '2026-09-03T11:00:00+07:00'),
    ]

    events = CalendarAvailabilityService(adapter).load(
        datetime(2026, 9, 3, tzinfo=timezone.utc), datetime(2026, 9, 4, tzinfo=timezone.utc),
    )

    assert [event.event_id for event in events] == ['positive']
    assert not any(call[0] in {'insert_event', 'update_event', 'delete_event'}
                   for call in adapter.method_calls)


def test_reversed_calendar_interval_fails_closed_without_writing_or_leaking_event_details():
    adapter = Mock()
    adapter.list_visible_calendars.return_value = [{'id': 'personal', 'summary': 'личное'}]
    adapter.list_events_in_calendar.return_value = [
        _event('private-id', 'Секретное название', '2026-09-03T12:00:00+07:00', '2026-09-03T11:00:00+07:00'),
    ]

    with pytest.raises(UpdateAllBlocked, match='окончание раньше начала') as error:
        CalendarAvailabilityService(adapter).load(
            datetime(2026, 9, 3, tzinfo=timezone.utc), datetime(2026, 9, 4, tzinfo=timezone.utc),
        )

    assert 'private-id' not in str(error.value)
    assert 'Секретное название' not in str(error.value)
    assert not any('insert' in call[0] or 'update' in call[0] or 'delete' in call[0]
                   for call in adapter.method_calls)


def test_manual_busy_events_are_checked_without_mutation():
    service = CalendarAvailabilityService(Mock())
    equal = CalendarBusyEvent(
        'calendar', 'личное', 'equal', 'Zero', datetime(2026, 9, 3, 10, tzinfo=timezone.utc),
        datetime(2026, 9, 3, 10, tzinfo=timezone.utc), movable_with_confirmation=True,
    )
    positive = CalendarBusyEvent(
        'calendar', 'Работа', 'positive', 'Meeting', datetime(2026, 9, 3, 10, tzinfo=timezone.utc),
        datetime(2026, 9, 3, 11, tzinfo=timezone.utc),
    )
    reversed_event = CalendarBusyEvent(
        'calendar', 'личное', 'negative', 'Reversed', datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
        datetime(2026, 9, 3, 11, tzinfo=timezone.utc), movable_with_confirmation=True,
    )

    assert [item.id for item in service.hard_commitments([equal, positive])] == ['google:calendar:positive']
    assert [item.id for item in service.personal_commitments([equal])] == []
    with pytest.raises(UpdateAllBlocked, match='окончание раньше начала'):
        service.hard_commitments([reversed_event])
    with pytest.raises(UpdateAllBlocked, match='окончание раньше начала'):
        service.personal_commitments([reversed_event])
    assert equal.start == equal.end
    assert reversed_event.end < reversed_event.start
