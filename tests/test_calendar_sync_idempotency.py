"""CalendarSyncService operation and idempotency tests."""

from datetime import datetime, timezone
from unittest.mock import Mock, patch

from services.calendar.calendar_sync_service import (
    CalendarSyncService,
    create_calendar_sync_service,
)


def source_event(**overrides):
    event = {
        'uid': 'source-uid',
        'summary': 'Математика (ЛК)',
        'summary_normalized': 'Математика (ЛК)',
        'description': 'Группа 8И41',
        'location': 'Аудитория 101',
        'dtstart': datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        'dtend': datetime(2026, 9, 1, 11, 30, tzinfo=timezone.utc),
        'event_type': 'ЛК',
        'status': 'confirmed',
        'sequence': 0,
        'is_group_event': True,
    }
    event.update(overrides)
    return event


def run_sync(service, events):
    with patch('scripts.parse_ics.parse_ics', return_value=events):
        service.sync_ics_to_gcalendar('schedule.ics')


def existing_google_event(service, source, description=None, uid=None):
    return {
        'id': 'google-event-id',
        'iCalUID': uid or source['uid'],
        'summary': source['summary'],
        'description': description if description is not None else source['description'],
        'start': {'dateTime': service._format_datetime(source['dtstart'])},
        'end': {'dateTime': service._format_datetime(source['dtend'])},
    }


def configured_service(existing_events):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'calendar-id'
    adapter._get_events_in_range.return_value = existing_events
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'new-google-id'
    adapter._update_event.return_value = 'updated-google-id'
    service = CalendarSyncService(adapter)
    return service, adapter


def test_factory_wires_google_calendar_configuration():
    with patch(
        'adapters.google_calendar_adapter.GoogleCalendarAdapter'
    ) as adapter_class:
        service = create_calendar_sync_service({
            'calendar': {'calendar_name': 'Test University'}
        })

    adapter_class.assert_called_once_with({
        'calendar_name': 'Test University'
    })
    assert service.calendar_adapter is adapter_class.return_value


def test_create_writes_hash_and_version_markers():
    event = source_event()
    service, adapter = configured_service([])

    run_sync(service, [event])

    inserted = adapter._insert_event.call_args.args[0]
    assert 'Хеш:' in inserted.description
    assert f'Версия: {service.VERSION}' in inserted.description


def test_repeated_identical_sync_is_noop():
    event = source_event()
    service, adapter = configured_service([])
    description = service._append_hash_and_version_to_description(
        event['description'], event
    )
    adapter._get_events_in_range.return_value = [
        existing_google_event(service, event, description=description)
    ]

    run_sync(service, [event])

    adapter._delete_event.assert_not_called()
    adapter._insert_event.assert_not_called()


def test_changed_event_updates_by_google_event_id():
    old = source_event()
    new = source_event(location='Аудитория 202', sequence=1)
    service, adapter = configured_service([])
    old_description = service._append_hash_and_version_to_description(
        old['description'], old
    )
    adapter._get_events_in_range.return_value = [
        existing_google_event(service, old, description=old_description)
    ]

    run_sync(service, [new])

    adapter._update_event.assert_called_once()
    args = adapter._update_event.call_args.args
    assert args[:2] == ('calendar-id', 'google-event-id')
    assert args[2].location == 'Аудитория 202'
    adapter._delete_event.assert_not_called()
    adapter._insert_event.assert_not_called()


def test_explicit_cancellation_deletes_existing_projection():
    event = source_event(status='cancelled', sequence=2)
    service, adapter = configured_service([])
    adapter._get_events_in_range.return_value = [
        existing_google_event(service, event)
    ]

    run_sync(service, [event])

    adapter._delete_event.assert_called_once_with('calendar-id', 'google-event-id')
    adapter._insert_event.assert_not_called()


def test_same_slot_with_different_uid_and_hash_is_conflict():
    source = source_event()
    service, adapter = configured_service([])
    conflicting_description = service._append_hash_and_version_to_description(
        'Different source content',
        source_event(description='Different source content'),
    )
    adapter._get_events_in_range.return_value = [
        existing_google_event(
            service,
            source,
            description=conflicting_description,
            uid='other-uid',
        )
    ]

    run_sync(service, [source])

    adapter._delete_event.assert_not_called()
    adapter._insert_event.assert_not_called()


def test_moved_event_outside_fetch_window_updates_global_uid_match():
    event = source_event(
        dtstart=datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
        dtend=datetime(2026, 10, 1, 11, 30, tzinfo=timezone.utc),
        sequence=2,
    )
    service, adapter = configured_service([])
    adapter.event_exists_by_uid.return_value = 'old-google-event-id'

    run_sync(service, [event])

    adapter._update_event.assert_called_once()
    assert adapter._update_event.call_args.args[:2] == (
        'calendar-id', 'old-google-event-id'
    )
    adapter._insert_event.assert_not_called()
    adapter._delete_event.assert_not_called()
