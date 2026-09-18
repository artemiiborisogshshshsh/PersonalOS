from unittest.mock import Mock
from types import SimpleNamespace
from datetime import datetime, timedelta
import asyncio

import pytest

from adapters.google_calendar_adapter import GoogleCalendarAdapter
from googleapiclient.errors import HttpError
from httplib2 import Response


def test_get_or_create_calendar_remembers_existing_calendar_id():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter.service.calendarList().list().execute.return_value = {
        'items': [{'id': 'calendar-42', 'summary': 'Personal schedule'}]
    }

    calendar_id = adapter._get_or_create_calendar('Personal schedule')

    assert calendar_id == 'calendar-42'
    assert adapter.calendar_id == 'calendar-42'


def test_get_or_create_calendar_remembers_created_calendar_id():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter.service.calendarList().list().execute.return_value = {'items': []}
    adapter.service.calendars().insert().execute.return_value = {'id': 'calendar-99'}

    calendar_id = adapter._get_or_create_calendar('Personal schedule')

    assert calendar_id == 'calendar-99'
    assert adapter.calendar_id == 'calendar-99'


def test_private_delete_executes_immediately_for_sync_rollback():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter._set_initialized(True)

    assert adapter._delete_event('calendar-42', 'event-42') is True
    adapter.service.events().delete.assert_called_once_with(
        calendarId='calendar-42', eventId='event-42',
    )


def test_private_delete_treats_google_tombstone_as_success():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter._set_initialized(True)
    adapter.service.events().delete().execute.side_effect = HttpError(
        Response({'status': '410'}), b'Resource has been deleted',
    )

    assert adapter._delete_event('calendar-42', 'already-gone') is True


def test_event_exists_by_uid_also_uses_our_persisted_system_marker():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter.service.events().list().execute.return_value = {'items': [{
        'id': 'google-block',
        'iCalUID': 'google-generated-id',
        'extendedProperties': {'private': {'personal_os_block_id': 'prep:block'}},
    }]}

    assert adapter.event_exists_by_uid('calendar-42', 'prep:block') == 'google-block'


def test_get_event_by_uid_returns_full_exact_match_without_legacy_scan():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    events_api = adapter.service.events.return_value
    event = {'id': 'google-event', 'iCalUID': 'event-1', 'description': 'saved'}
    events_api.list.return_value.execute.return_value = {'items': [event]}

    assert adapter.get_event_by_uid('calendar-42', 'event-1') is event
    events_api.list.assert_called_once_with(
        calendarId='calendar-42', iCalUID='event-1',
    )


def test_get_event_by_uid_uses_legacy_scan_only_after_exact_miss():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    events_api = adapter.service.events.return_value
    legacy = {
        'id': 'legacy-event',
        'extendedProperties': {'private': {'personal_os_block_id': 'event-1'}},
    }
    events_api.list.return_value.execute.side_effect = [
        {'items': []}, {'items': [legacy]},
    ]

    assert adapter.get_event_by_uid('calendar-42', 'event-1') is legacy
    calls = events_api.list.call_args_list
    assert calls[0].kwargs == {'calendarId': 'calendar-42', 'iCalUID': 'event-1'}
    assert calls[1].kwargs['calendarId'] == 'calendar-42'
    assert calls[1].kwargs['singleEvents'] is True


def test_strict_get_event_by_uid_miss_does_not_scan_legacy_calendar_range():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    events_api = adapter.service.events.return_value
    events_api.list.return_value.execute.return_value = {'items': []}

    assert adapter.get_event_by_uid('calendar-42', 'new-event', strict=True) is None
    events_api.list.assert_called_once_with(
        calendarId='calendar-42', iCalUID='new-event',
    )


def test_get_event_by_uid_does_not_swallow_read_failure():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter.service.events().list().execute.side_effect = RuntimeError('read failed')

    with pytest.raises(RuntimeError, match='read failed'):
        adapter.get_event_by_uid('calendar-42', 'event-1')

    # The compatibility wrapper intentionally retains its old contract.
    assert adapter.event_exists_by_uid('calendar-42', 'event-1') is None


def test_legacy_adapter_logs_never_echo_provider_exception_details(capsys):
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    secret = 'https://private.test/?token=TOP_SECRET'
    start = datetime(2026, 9, 3, 10, 0)
    event = SimpleNamespace(
        uid='event-1', summary='Subject', description='', location='',
        dtstart=start, dtend=start + timedelta(hours=1), event_type='',
    )
    adapter.service.events().insert().execute.side_effect = RuntimeError(secret)

    assert adapter._insert_event(event, 'calendar-42') is None

    output = capsys.readouterr().out
    assert 'Google Calendar insert failed' in output
    assert 'TOP_SECRET' not in output
    assert 'private.test' not in output


def test_sync_result_never_includes_an_untrusted_exception_message(capsys):
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter._set_initialized(True)
    secret = 'https://private.test/?token=TOP_SECRET'
    adapter._get_events_in_range = Mock(side_effect=RuntimeError(secret))
    event = SimpleNamespace(
        uid='event-1', summary='Subject', summary_normalized='Subject',
        dtstart=datetime(2026, 9, 3, 10), dtend=datetime(2026, 9, 3, 11),
        is_group_event=True,
    )

    result = asyncio.run(adapter.sync_events_to_calendar([event]))

    assert result['success'] is False
    assert result['errors'] == ['Unexpected Google Calendar sync failure.']
    assert 'TOP_SECRET' not in str(result) + capsys.readouterr().out


@pytest.mark.parametrize('status', [401, 403, 400, 429, 500])
def test_strict_writes_propagate_http_errors_while_legacy_calls_return_none(status):
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    error = HttpError(Response({'status': str(status)}), b'Google failure')
    adapter.service.events().insert().execute.side_effect = error
    start = datetime(2026, 9, 3, 10, 0)
    event = SimpleNamespace(
        uid='event-1', summary='Subject', description='', location='',
        dtstart=start, dtend=start + timedelta(hours=1), event_type='',
    )

    assert adapter._insert_event(event, 'calendar-42') is None
    with pytest.raises(HttpError):
        adapter._insert_event(event, 'calendar-42', strict=True)

    adapter.service.events().update().execute.side_effect = error
    assert adapter._update_event('calendar-42', 'event-42', event) is None
    with pytest.raises(HttpError):
        adapter._update_event('calendar-42', 'event-42', event, strict=True)


def test_strict_insert_propagates_failed_409_recovery_and_generic_errors():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    api = adapter.service.events.return_value
    start = datetime(2026, 9, 3, 10, 0)
    event = SimpleNamespace(
        uid='event-1', summary='Subject', description='', location='',
        dtstart=start, dtend=start + timedelta(hours=1), event_type='',
    )
    api.insert.return_value.execute.side_effect = HttpError(
        Response({'status': '409'}), b'already exists',
    )
    api.list.return_value.execute.side_effect = RuntimeError('SSL lookup failed')

    with pytest.raises(RuntimeError, match='SSL lookup failed'):
        adapter._insert_event(event, 'calendar-42', strict=True)

    api.list.return_value.execute.side_effect = None
    api.list.return_value.execute.return_value = {
        'items': [{'id': 'existing-google-event', 'iCalUID': 'event-1'}],
    }
    api.update.return_value.execute.side_effect = HttpError(
        Response({'status': '500'}), b'recovery update failed',
    )
    with pytest.raises(HttpError):
        adapter._insert_event(event, 'calendar-42', strict=True)

    api.insert.return_value.execute.side_effect = RuntimeError('SSL write failed')
    with pytest.raises(RuntimeError, match='SSL write failed'):
        adapter._insert_event(event, 'calendar-42', strict=True)

    api.update.return_value.execute.side_effect = RuntimeError('SSL update failed')
    with pytest.raises(RuntimeError, match='SSL update failed'):
        adapter._update_event('calendar-42', 'event-42', event, strict=True)


def test_duplicate_insert_updates_the_existing_google_event():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter.service.events().insert().execute.side_effect = HttpError(
        Response({'status': '409'}), b'The requested identifier already exists.',
    )
    adapter.service.events().list().execute.return_value = {
        'items': [{'id': 'existing-google-event', 'iCalUID': 'event-1'}],
    }
    adapter.service.events().update.return_value.execute.return_value = {
        'id': 'existing-google-event',
    }
    start = datetime(2026, 9, 3, 10, 0)
    event = SimpleNamespace(
        uid='event-1', summary='Подготовка', description='', location='',
        dtstart=start, dtend=start + timedelta(hours=1), event_type='PREPARATION',
    )

    result = adapter._insert_event(event, 'personal-university-calendar')

    assert result == 'existing-google-event'
    adapter.service.events().update.assert_called_once_with(
        calendarId='personal-university-calendar',
        eventId='existing-google-event',
        body={
            'summary': 'Подготовка', 'location': '', 'description': '',
            'start': {'dateTime': '2026-09-03T10:00:00Z'},
            'end': {'dateTime': '2026-09-03T11:00:00Z'},
            'iCalUID': 'event-1', 'reminders': {'useDefault': True},
            'colorId': '3',
        },
    )


@pytest.mark.parametrize(('session_type', 'google_color'), [
    ('ЛК', '1'),
    ('ЛБ', '2'),
    ('ПР', '4'),
])
def test_university_session_types_use_distinct_google_colours(
    session_type, google_color,
):
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    adapter.service.events().insert().execute.return_value = {'id': 'google-event'}
    start = datetime(2026, 9, 3, 10, 0)
    event = SimpleNamespace(
        uid='event-1', summary='Предмет', description='', location='',
        dtstart=start, dtend=start + timedelta(hours=1), event_type=session_type,
    )

    adapter._insert_event(event, 'university-calendar')

    body = adapter.service.events().insert.call_args.kwargs['body']
    assert body['colorId'] == google_color
