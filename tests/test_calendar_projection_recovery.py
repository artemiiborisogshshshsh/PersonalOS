"""Synthetic regressions for the durable preparation projection boundary."""

from datetime import datetime, timedelta, timezone

import pytest

from services.adaptive_preparation_service import (
    DraftCalendarProjector, DraftOperation, DraftPreparationBlock,
)
from services.draft_operation_store import DraftOperationStore
from services.calendar.projection_state import (
    CalendarProjectionError, delete_owned_verified,
)


def operation():
    start = datetime(2026, 9, 20, 10, tzinfo=timezone.utc)
    block = DraftPreparationBlock('block-1', 'source-1', 'Prep', start,
                                  start + timedelta(minutes=20), 20, 'reason')
    return DraftOperation('run-1', [block])


class FakeCalendar:
    def __init__(self):
        self.events = {}
        self.calls = []
        self.fail_after_insert = False
        self.failure = None

    def _get_or_create_calendar(self, name):
        return 'study'

    def get_event_by_uid(self, calendar_id, uid, *, strict=True):
        self.calls.append('read')
        return self.events.get(uid)

    def event_exists_by_uid(self, calendar_id, uid):
        return self.events.get(uid, {}).get('id')

    def _insert_event(self, data, calendar_id, *, strict=True):
        self.calls.append('insert')
        if self.failure:
            raise self.failure
        self.events[data.uid] = self._event(data)
        if self.fail_after_insert:
            raise TimeoutError('private provider URL must stay hidden')
        return 'remote-1'

    def _update_event(self, calendar_id, event_id, data, *, strict=True):
        self.calls.append('update')
        self.events[data.uid] = self._event(data)
        return event_id

    def _delete_event(self, calendar_id, event_id):
        self.calls.append('delete')
        self.events.pop('block-1', None)
        return True

    @staticmethod
    def _event(data):
        return {'id': 'remote-1', 'iCalUID': data.uid, 'summary': data.summary,
                'description': data.description,
                'start': {'dateTime': data.dtstart.isoformat()},
                'end': {'dateTime': data.dtend.isoformat()},
                'extendedProperties': {'private': {
                    'personal_os_block_id': data.system_block_id,
                    'personal_os_operation_id': data.system_operation_id,
                }}}


def test_ambiguous_insert_is_verified_and_restart_is_noop(tmp_path):
    remote = FakeCalendar()
    remote.fail_after_insert = True
    store = DraftOperationStore(tmp_path / 'operations.json')
    op = operation()
    projector = DraftCalendarProjector(remote)

    projector.stage(op, checkpoint=store.save)
    assert remote.calls.count('insert') == 1
    assert not op.pending_calendar_writes
    assert store.load(op.id).calendar_event_ids == {'block-1': 'remote-1'}

    projector.stage(store.load(op.id), checkpoint=store.save)
    assert remote.calls.count('insert') == 1
    assert remote.calls.count('update') == 0


def test_same_uid_without_ownership_marker_is_never_changed():
    remote = FakeCalendar()
    remote.events['block-1'] = {'id': 'external', 'iCalUID': 'block-1',
                                'summary': 'User event'}
    projector = DraftCalendarProjector(remote)

    with pytest.raises(RuntimeError, match='влад'):
        projector.stage(operation())

    assert 'insert' not in remote.calls and 'update' not in remote.calls


def test_rollback_does_not_delete_manual_move_or_external_replacement():
    remote = FakeCalendar()
    op = operation()
    projector = DraftCalendarProjector(remote)
    projector.stage(op)
    remote.events['block-1']['start']['dateTime'] = (
        op.blocks[0].start + timedelta(hours=1)).isoformat()

    projector.rollback(op)

    assert 'delete' not in remote.calls


def test_confirm_preserves_manual_move_in_durable_operation(tmp_path):
    remote = FakeCalendar()
    store = DraftOperationStore(tmp_path / 'operations.json')
    op = operation()
    projector = DraftCalendarProjector(remote)
    projector.stage(op, checkpoint=store.save)
    remote.events['block-1']['start']['dateTime'] = (
        op.blocks[0].start + timedelta(hours=2)).isoformat()

    projector.confirm(op, checkpoint=store.save)

    assert remote.calls.count('update') == 0
    assert 'block-1' in store.load(op.id).manual_calendar_overrides


def test_auth_error_does_not_retry_and_reports_safe_message():
    remote = FakeCalendar()
    remote.failure = PermissionError('secret credential')
    op = operation()

    with pytest.raises(RuntimeError) as error:
        DraftCalendarProjector(remote).stage(op)

    assert 'secret credential' not in str(error.value)
    assert remote.calls.count('insert') == 1


def test_pending_insert_can_be_rolled_back_after_restart(tmp_path):
    remote = FakeCalendar()
    op = operation()
    desired = DraftCalendarProjector._event_data(op.blocks[0], op)
    remote.events['block-1'] = remote._event(desired)
    op.pending_calendar_writes['block-1'] = 'insert'
    store = DraftOperationStore(tmp_path / 'operations.json')
    store.save(op)

    DraftCalendarProjector(remote).rollback(store.load(op.id))

    assert remote.calls.count('delete') == 1
    assert remote.events == {}


def test_transient_failure_retries_at_most_three_times():
    class TemporaryFailure(FakeCalendar):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def _insert_event(self, data, calendar_id, *, strict=True):
            self.attempts += 1
            if self.attempts < 3:
                raise TimeoutError('temporary')
            return super()._insert_event(data, calendar_id, strict=strict)

    remote = TemporaryFailure()
    DraftCalendarProjector(remote).stage(operation())

    assert remote.attempts == 3
    assert len(remote.events) == 1


def test_ambiguous_delete_is_verified_before_retry():
    class DeleteCalendar:
        def __init__(self):
            self.event = {'id': 'remote-1', 'extendedProperties': {'private': {
                'personal_os_block_id': 'block-1'}}}
            self.attempts = 0

        def get_event_by_id(self, calendar, event_id, *, strict=True):
            return self.event

        def _delete_event(self, calendar, event_id, *, strict=True):
            self.attempts += 1
            self.event = None
            raise TimeoutError('private URL')

    remote = DeleteCalendar()
    assert delete_owned_verified(remote, 'study', 'remote-1',
                                 lambda event: event['extendedProperties']['private']
                                 ['personal_os_block_id'] == 'block-1')
    assert remote.attempts == 1


def test_delete_refuses_event_without_ownership():
    class ExternalCalendar:
        def get_event_by_id(self, calendar, event_id, *, strict=True):
            return {'id': 'external'}

        def _delete_event(self, calendar, event_id, *, strict=True):
            raise AssertionError('must not mutate')

    with pytest.raises(CalendarProjectionError, match='владелец'):
        delete_owned_verified(ExternalCalendar(), 'study', 'external',
                              lambda event: False)
