"""Google retains deleted iCalUIDs: restore only exact receipted tombstones."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from googleapiclient.errors import HttpError
from httplib2 import Response
import pytest

from adapters.google_calendar_adapter import GoogleCalendarAdapter
from services.attendance_preferences import AttendancePreferenceStore
from services.invited_beta_runtime import InvitedBot
from tests import test_attendance_deletion as fixture
from tests.test_closed_beta_runtime import NOW, confirmation
from tests.test_manage_attendance_acceptance import edit_algebra, send_callback, send_text
from tests.test_published_plan_updates import ProcessCrash


class GoogleTombstoneCalendar(fixture.AttendanceCalendar):
    def __init__(self):
        super().__init__()
        self.deleted_uids = {}
        self.restores = []
        self.rejected_inserts = 0
        self.crash_restore = False
        self.sparse = False
        self.after_restore = None

    def list_events_in_calendar(self, calendar, *args, **kwargs):
        return [row for row in super().list_events_in_calendar(calendar, *args, **kwargs)
                if row.get('status') != 'cancelled']

    def get_event_by_uid(self, calendar, uid, **kwargs):
        return next((deepcopy(row) for (cal, _), row in self.remote.items()
                     if cal == calendar and row.get('iCalUID') == uid and row.get('status') != 'cancelled'), None)

    def _insert_event(self, event, calendar_id, **kwargs):
        if (calendar_id, event.uid) in self.deleted_uids:
            self.rejected_inserts += 1
            raise HttpError(Response({'status': '409'}), b'{"error":{"code":409}}')
        return super()._insert_event(event, calendar_id, **kwargs)

    def _delete_event(self, calendar_id, event_id, *, strict=False, expected_etag=None):
        assert strict and expected_etag
        row = self.remote[(calendar_id, event_id)]
        assert row['etag'] == expected_etag
        self.deleted_uids[(calendar_id, row['iCalUID'])] = event_id
        self.writes.append(('delete', event_id))
        if self.sparse:
            self.remote[(calendar_id, event_id)] = {'id': event_id, 'etag': 'deleted-' + expected_etag,
                                                     'status': 'cancelled'}
        else:
            row.update(status='cancelled', etag='deleted-' + expected_etag)
        return True

    def restore_cancelled_event(self, calendar_id, event_id, desired, *, expected_etag):
        old = self.remote[(calendar_id, event_id)]
        assert old['status'] == 'cancelled' and old['etag'] == expected_etag
        old.update(status='confirmed', summary=desired.summary, description=desired.description,
                   iCalUID=desired.uid, location=desired.location,
                   start={'dateTime': desired.dtstart.isoformat()}, end={'dateTime': desired.dtend.isoformat()},
                   extendedProperties={'private': {'personal_os_block_id': desired.system_block_id,
                       'personal_os_source_event_id': desired.system_source_event_id,
                       'personal_os_operation_id': desired.system_operation_id}},
                   etag='restored-' + expected_etag)
        self.restores.append(event_id)
        self.writes.append(('restore', event_id))
        if self.after_restore:
            callback, self.after_restore = self.after_restore, None
            callback()
        if self.crash_restore:
            self.crash_restore = False
            raise ProcessCrash()
        return event_id


def deleted_setup(tmp_path, monkeypatch, *, sparse=False):
    monkeypatch.setattr(fixture, 'AttendanceCalendar', GoogleTombstoneCalendar)
    bot, calendars, content, number, app, classes, removed = fixture.setup(tmp_path)
    calendar = calendars['101']
    calendar.sparse = sparse
    number = fixture.disable_labs(bot, number)
    preview = send_text(bot, number, '101', '/review_missing'); number += 1
    result = send_callback(bot, number, '101', confirmation(preview)); number += 1
    assert 'Удаление проверено' in result['text'], result
    operation = next(iter(app.operations.load_all().values()))
    receipts = operation.published_plan['attendance_deletions']
    assert set(receipts) == set(removed)
    assert all(receipts[uid]['row'] == row for uid, row in removed.items())
    return bot, calendars, content, number, app, classes, removed


def reenable(bot, number):
    number, _ = edit_algebra(bot, '101', number, labs=2)
    preview = send_text(bot, number, '101', '/weekly_preview'); number += 1
    assert 'Восстановить удалённое' in preview['text'], preview
    return number, preview


@pytest.mark.parametrize('sparse', [False, True])
def test_google_409_tombstones_restore_only_by_receipt_id_without_insert(tmp_path, monkeypatch, sparse):
    bot, calendars, _content, number, app, classes, removed = deleted_setup(tmp_path, monkeypatch, sparse=sparse)
    calendar = calendars['101']
    retained = {key: deepcopy(row) for key, row in calendar.remote.items() if row.get('status') != 'cancelled'}
    number, preview = reenable(bot, number)
    assert not calendar.restores
    original = next(iter(app.operations.load_all().values())).published_plan['rows']
    explicitly_updated = {uid for uid, row in app.pending[3]['rows'].items()
                          if uid in original and row['data'] != original[uid]['data']}
    result = send_callback(bot, number, '101', confirmation(preview)); number += 1
    assert 'обновлён' in result['text'], result
    assert calendar.rejected_inserts == 0
    operation = next(iter(app.operations.load_all().values()))
    assert classes <= set(operation.published_plan['rows'])
    for uid in classes:
        assert operation.published_plan['rows'][uid]['event_id'] == removed[uid]['event_id']
        assert uid not in operation.published_plan.get('attendance_deletions', {})
    assert set(calendar.restores) <= {row['event_id'] for row in removed.values()}
    assert len(calendar.restores) >= 2
    assert all(calendar.remote[key] == row for key, row in retained.items()
               if row['iCalUID'] not in explicitly_updated)
    assert not operation.pending_plan_update and not operation.projection_pending
    writes = list(calendar.writes)
    send_text(bot, number, '101', '/weekly_preview')
    assert calendar.writes == writes


@pytest.mark.parametrize('change', ['etag', 'ownership', 'source', 'preference', 'wrong_calendar', 'replaced_id', 'started'])
def test_restoration_fails_closed_when_evidence_changes(tmp_path, monkeypatch, change):
    bot, calendars, content, number, app, _classes, _removed = deleted_setup(tmp_path, monkeypatch)
    number, preview = reenable(bot, number)
    calendar = calendars['101']
    before = list(calendar.writes)
    observation = app.pending[3]['attendance_restores']
    uid = next(iter(observation))
    key = next(key for key in calendar.remote if key[1] == observation[uid]['event_id'])
    if change == 'etag':
        calendar.remote[key]['etag'] = 'changed-after-preview'
    elif change == 'ownership':
        calendar.remote[key]['extendedProperties']['private']['personal_os_block_id'] = 'foreign'
    elif change == 'source':
        content['ics'] = content['ics'].replace(b'LOCATION:\r\n', b'LOCATION:Changed\r\n')
    elif change == 'preference':
        store = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
        store.set_lab_slots('Алгебра', [])
        store.save()
    elif change == 'wrong_calendar':
        operation = next(iter(app.operations.load_all().values()))
        operation.published_plan['attendance_deletions'][uid]['calendar_id'] = 'another-calendar'
        app.operations.save(operation)
    elif change == 'replaced_id':
        calendar.remote[key]['id'] = 'replacement-event'
    else:
        app.now = lambda: NOW + timedelta(days=4)
    result = send_callback(bot, number, '101', confirmation(preview))
    assert 'обновлён' not in result['text']
    assert calendar.writes == before and not calendar.restores


def test_restore_crash_restart_recovers_exact_id_without_duplicate_restore(tmp_path, monkeypatch):
    root = tmp_path / 'beta'
    bot, calendars, _content, number, app, _classes, _removed = deleted_setup(root, monkeypatch)
    number, preview = reenable(bot, number)
    calendars['101'].crash_restore = True
    with pytest.raises(ProcessCrash):
        send_callback(bot, number, '101', confirmation(preview))
    number += 1
    assert len(calendars['101'].restores) == 1
    restarted = InvitedBot('synthetic', root, calendars.__getitem__, bot.application_factory)
    preview = send_text(restarted, number, '101', '/weekly_preview'); number += 1
    assert preview['buttons'], preview
    result = send_callback(restarted, number, '101', confirmation(preview))
    assert 'обновлён' in result['text'], result
    restores = calendars['101'].restores
    assert len(restores) == len(set(restores))
    assert not next(iter(restarted.applications['101'][1].operations.load_all().values())).pending_plan_update


def test_change_during_restore_stops_next_conditional_write(tmp_path, monkeypatch):
    bot, calendars, _content, number, app, _classes, _removed = deleted_setup(tmp_path, monkeypatch)
    number, preview = reenable(bot, number)

    def opt_out():
        store = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
        store.set_lab_slots('Алгебра', [])
        store.save()

    calendars['101'].after_restore = opt_out
    result = send_callback(bot, number, '101', confirmation(preview))
    assert 'обновлён' not in result['text']
    assert len(calendars['101'].restores) == 1
    assert next(iter(app.operations.load_all().values())).pending_plan_update


def test_operator_seeded_legacy_pending_insert_retains_attempt_and_uses_exact_restore(tmp_path, monkeypatch):
    bot, calendars, _content, number, app, _classes, _removed = deleted_setup(tmp_path, monkeypatch)
    number, preview = reenable(bot, number)
    operation = next(iter(app.operations.load_all().values()))
    payload = deepcopy(app.pending[3])
    uid = next(iter(payload['attendance_restores']))
    payload['attempted'] = [uid]
    payload['applied'] = {}
    payload.pop('attendance_restores')
    operation.pending_plan_update = payload
    operation.projection_pending = True
    app.operations.save(operation)
    # A fresh preview supplies conditional evidence, never clears old attempts.
    preview = send_text(bot, number, '101', '/weekly_preview'); number += 1
    assert preview['buttons'], preview
    saved = next(iter(app.operations.load_all().values())).pending_plan_update
    assert saved['attempted'] == [uid] and saved['attendance_restores'][uid]
    result = send_callback(bot, number, '101', confirmation(preview))
    assert 'обновлён' in result['text'], result
    assert calendars['101'].rejected_inserts == 0


def test_adapter_restore_sends_status_confirmed_with_if_match_only_to_saved_id():
    adapter = GoogleCalendarAdapter()
    adapter.service = Mock()
    api = adapter.service.events.return_value
    api.patch.return_value.headers = {}
    api.patch.return_value.execute.return_value = {'id': 'exact-deleted-id'}
    desired = SimpleNamespace(uid='stable', summary='Class', description='Owned', location='',
                              dtstart=NOW, dtend=NOW + timedelta(hours=1), system_block_id='stable',
                              system_source_event_id='source', system_operation_id='university-event')
    assert adapter.restore_cancelled_event('calendar', 'exact-deleted-id', desired,
                                           expected_etag='cancelled-version') == 'exact-deleted-id'
    call = api.patch.call_args.kwargs
    assert call['calendarId'] == 'calendar' and call['eventId'] == 'exact-deleted-id'
    assert call['body']['status'] == 'confirmed'
    assert call['body']['extendedProperties']['private']['personal_os_block_id'] == 'stable'
    assert api.patch.return_value.headers['If-Match'] == 'cancelled-version'
    api.insert.assert_not_called()
    api.update.assert_not_called()
    with pytest.raises(ValueError):
        adapter.restore_cancelled_event('calendar', 'exact-deleted-id', desired, expected_etag=None)
