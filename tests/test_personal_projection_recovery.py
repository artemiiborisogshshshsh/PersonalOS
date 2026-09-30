from datetime import datetime, timedelta
from unittest.mock import Mock

from models import PersonalEventState, PersonalUniversityEvent
from services.calendar.personal_event_sync_service import PersonalEventSyncService
from services.calendar.projection_state import CalendarProjectionState


def event():
    start = datetime(2026, 9, 20, 10)
    return PersonalUniversityEvent(
        id='personal-1', title='Synthetic lesson', description='',
        start_time=start, end_time=start + timedelta(hours=1),
        university_event_uid='source-1', state=PersonalEventState.CONFIRMED,
    )


def test_unowned_matching_uid_is_never_updated_or_deleted():
    adapter = Mock()
    adapter.get_event_by_uid.return_value = {
        'id': 'user-event', 'iCalUID': 'personal-1',
        'description': 'User event',
    }
    service = PersonalEventSyncService(adapter)
    lesson = event()

    assert service.sync_personal_event_to_calendar(lesson, 'test') is None
    lesson.state = PersonalEventState.CANCELLED
    assert service.sync_personal_event_to_calendar(lesson, 'test') is None
    adapter._update_event.assert_not_called()
    adapter._delete_event.assert_not_called()


def test_ambiguous_insert_is_verified_before_retry(tmp_path):
    adapter = Mock()
    rows = {}
    adapter.get_event_by_uid.side_effect = lambda calendar, uid, **kwargs: rows.get(uid)
    adapter.list_events_in_calendar.return_value = []
    def insert(data, calendar_id, **kwargs):
        rows[data.uid] = {
            'id': 'remote-1', 'iCalUID': data.uid,
            'summary': data.summary, 'description': data.description,
            'start': {'dateTime': data.dtstart.isoformat()},
            'end': {'dateTime': data.dtend.isoformat()},
            'extendedProperties': {'private': {'personal_os_block_id': data.uid}},
        }
        raise TimeoutError('secret provider URL')
    adapter._insert_event.side_effect = insert
    state = CalendarProjectionState(tmp_path / 'projection.json')
    service = PersonalEventSyncService(adapter, state)

    assert service.sync_personal_event_to_calendar(event(), 'test') == 'remote-1'
    assert adapter._insert_event.call_count == 1
    assert CalendarProjectionState(tmp_path / 'projection.json').get('personal-1')['event_id'] == 'remote-1'


def test_manual_move_and_delete_survive_restart(tmp_path):
    adapter = Mock()
    rows = {}
    adapter.get_event_by_uid.side_effect = lambda calendar, uid, **kwargs: rows.get(uid)
    adapter.list_events_in_calendar.return_value = []
    def insert(data, calendar_id, **kwargs):
        rows[data.uid] = {
            'id': 'remote-1', 'iCalUID': data.uid,
            'summary': data.summary, 'description': data.description,
            'start': {'dateTime': data.dtstart.isoformat()},
            'end': {'dateTime': data.dtend.isoformat()},
            'extendedProperties': {'private': {'personal_os_block_id': data.uid}},
        }
        return 'remote-1'
    adapter._insert_event.side_effect = insert
    path = tmp_path / 'projection.json'
    PersonalEventSyncService(adapter, CalendarProjectionState(path)).sync_personal_event_to_calendar(event(), 'test')

    rows['personal-1']['start']['dateTime'] = '2026-09-20T12:00:00'
    moved = PersonalEventSyncService(adapter, CalendarProjectionState(path))
    assert moved.sync_personal_event_to_calendar(event(), 'test') == 'remote-1'
    assert CalendarProjectionState(path).get('personal-1')['override'] == 'moved'
    rows.clear()
    deleted = PersonalEventSyncService(adapter, CalendarProjectionState(path))
    assert deleted.sync_personal_event_to_calendar(event(), 'test') is None
    adapter._insert_event.assert_called_once()
    adapter._update_event.assert_not_called()


def test_verified_cancellation_can_reappear_without_manual_delete_override(tmp_path):
    adapter = Mock()
    rows = {}
    adapter.get_event_by_uid.side_effect = lambda calendar, uid, **kwargs: rows.get(uid)
    adapter.get_event_by_id.side_effect = lambda calendar, event_id, **kwargs: rows.get('personal-1')
    adapter.list_events_in_calendar.return_value = []
    def insert(data, calendar_id, **kwargs):
        rows[data.uid] = {
            'id': 'remote-1', 'iCalUID': data.uid,
            'summary': data.summary, 'description': data.description,
            'start': {'dateTime': data.dtstart.isoformat()},
            'end': {'dateTime': data.dtend.isoformat()},
            'extendedProperties': {'private': {'personal_os_block_id': data.uid}},
        }
        return 'remote-1'
    def delete(calendar, event_id, **kwargs):
        rows.clear()
        return True
    adapter._insert_event.side_effect = insert
    adapter._delete_event.side_effect = delete
    state = CalendarProjectionState(tmp_path / 'projection.json')
    service = PersonalEventSyncService(adapter, state)
    lesson = event()
    service.sync_personal_event_to_calendar(lesson, 'test')
    lesson.state = PersonalEventState.CANCELLED
    service.sync_personal_event_to_calendar(lesson, 'test')
    lesson.state = PersonalEventState.CONFIRMED
    service.sync_personal_event_to_calendar(lesson, 'test')
    assert adapter._insert_event.call_count == 2
    assert state.get(lesson.id).get('override') is None


def test_calendar_recovery_rebinds_legacy_lesson_checkpoint_once(tmp_path):
    adapter = Mock()
    rows = {}
    adapter.get_event_by_uid.side_effect = lambda calendar, uid, **kwargs: rows.get((calendar, uid))
    adapter.list_events_in_calendar.return_value = []
    def insert(data, calendar_id, **kwargs):
        rows[calendar_id, data.uid] = {
            'id': 'new-event', 'summary': data.summary, 'description': data.description,
            'start': {'dateTime': data.dtstart.isoformat()}, 'end': {'dateTime': data.dtend.isoformat()},
            'extendedProperties': {'private': {'personal_os_block_id': data.uid}}}
        return 'new-event'
    adapter._insert_event.side_effect = insert
    path = tmp_path / 'classes.json'
    state = CalendarProjectionState(path)
    state.put('personal-1', event_id='deleted-calendar-event', start=event().start_time.isoformat(),
              end=event().end_time.isoformat())
    service = PersonalEventSyncService(adapter, state)
    service.rebind_recovered_calendar({'old'}, 'new')
    assert service.sync_personal_event_to_calendar(event(), 'new') == 'new-event'
    restarted = PersonalEventSyncService(adapter, CalendarProjectionState(path))
    restarted.rebind_recovered_calendar({'old'}, 'new')
    assert restarted.sync_personal_event_to_calendar(event(), 'new') == 'new-event'
    adapter._insert_event.assert_called_once()
    assert restarted.projection_state.get('personal-1')['calendar_id'] == 'new'
    assert 'override' not in restarted.projection_state.get('personal-1')


def test_calendar_recovery_keeps_individual_overrides_and_other_calendar_rows(tmp_path):
    adapter = Mock()
    adapter.get_event_by_uid.return_value = None
    state = CalendarProjectionState(tmp_path / 'classes.json')
    state.put('personal-1', event_id='old-event', override='deleted')
    state.put('other', calendar_id='unrelated', event_id='keep')
    service = PersonalEventSyncService(adapter, state)
    service.rebind_recovered_calendar({'old'}, 'new')
    assert service.sync_personal_event_to_calendar(event(), 'new') is None
    assert state.get('personal-1')['override'] == 'deleted'
    assert state.get('other') == {'calendar_id': 'unrelated', 'event_id': 'keep'}
    state.put('personal-1', override='moved')
    assert service.sync_personal_event_to_calendar(event(), 'new') is None
    adapter._insert_event.assert_not_called()
