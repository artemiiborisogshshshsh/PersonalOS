from datetime import datetime, timezone
from unittest.mock import Mock

from services.calendar_availability_service import CalendarAvailabilityService


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
