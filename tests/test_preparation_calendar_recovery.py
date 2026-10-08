"""Deleted-calendar recovery with synthetic sources, stores and provider state."""
from copy import deepcopy
import json

import pytest

from tests.test_legacy_preparation_commands import legacy_runtime


def publish(bot, runtime):
    bot.handle_text('123', '/work_schedule')
    bot.work_route_apply(runtime.lessons[0].id, 'direct')
    bot.handle_text('123', '/work_schedule')


@pytest.mark.parametrize('phase', ['complete', 'work'])
@pytest.mark.parametrize('interrupt', [False, True])
def test_deleted_calendar_recreated_and_repeat_has_no_duplicates(legacy_runtime, interrupt, phase):
    def check(bot, adapter, remote):
        publish(bot, legacy_runtime)
        assert any(cal == 'study' for cal, _ in remote)
        queue_path = next(legacy_runtime.path.rglob('shared_preparation.json'))
        journal = json.loads(queue_path.read_text())
        journal['phase'] = phase
        queue_path.write_text(json.dumps(journal))
        unaffected = {key: deepcopy(event) for key, event in remote.items() if key[0] != 'study'}
        for key in list(remote):
            if key[0] == 'study':
                del remote[key]
        created = []
        adapter.calendar_is_accessible.side_effect = lambda identifier: identifier != 'study'

        def replacement(old_id, name, *, allow_create, before_create):
            assert old_id == 'study'
            if not created:
                assert allow_create
                before_create()
                created.append('study-new')
                if interrupt:
                    raise TimeoutError('lost create response')
            return created[0]

        adapter.replacement_calendar.side_effect = replacement
        adapter.list_visible_calendars.return_value = [
            {'id': 'study-new', 'summary': 'Personal University Schedule'},
            {'id': 'work', 'summary': 'Работа'}, {'id': 'personal', 'summary': 'Личное'}]
        adapter._get_or_create_calendar.side_effect = {
            'Personal University Schedule': 'study-new', 'Работа': 'work', 'Личное': 'personal'}.__getitem__
        if interrupt:
            reply = bot.handle_text('123', '/preparations')
            assert 'восстановление' in reply['text']
            assert not any(cal == 'study-new' for cal, _ in remote)
        reply = bot.handle_text('123', '/preparations')
        assert 'остановлен' not in reply['text']
        assert any(cal == 'study-new' for cal, _ in remote)
        assert len(created) == 1
        recovery = next(legacy_runtime.path.rglob('shared_preparation.calendar-recovery.json'))
        record = json.loads(recovery.read_text())
        assert record['complete']
        assert record['queue_before']['version'] == 2
        assert record['queue_before']['phase'] == phase
        assert record['operations'][0]['calendar_event_ids']
        assert record['calendars']['study']['new_id'] == 'study-new'
        for key, event in unaffected.items():
            assert remote[key] == event
        before = deepcopy(remote)
        adapter.reset_mock()
        again = bot.handle_text('123', '/preparations')
        assert 'остановлен' not in again['text']
        assert remote == before
        adapter._insert_event.assert_not_called()
        adapter._delete_event.assert_not_called()
        adapter.replacement_calendar.assert_not_called()

    legacy_runtime.run(check)


@pytest.mark.parametrize('status', [401, 403, 500])
def test_non_404_access_failure_never_creates_calendar(legacy_runtime, status):
    from googleapiclient.errors import HttpError
    from httplib2 import Response

    def check(bot, adapter, remote):
        publish(bot, legacy_runtime)
        before = deepcopy(remote)
        adapter.calendar_is_accessible.side_effect = HttpError(Response({'status': str(status)}), b'private')
        reply = bot.handle_text('123', '/preparations')
        assert str(status) in reply['text']
        assert remote == before
        adapter.replacement_calendar.assert_not_called()
        assert not list(legacy_runtime.path.rglob('*.calendar-recovery.json'))

    legacy_runtime.run(check)


def test_recovery_preserves_manual_times_and_deleted_sources_across_new_plan():
    from datetime import datetime, timedelta
    from dataclasses import replace
    from services.adaptive_preparation_service import DraftOperation, DraftPreparationBlock, CalendarRoute
    from services.preparation_calendar_recovery import _rebound
    from services.shared_preparation_workflow import SharedPreparationWorkflow

    start = datetime(2026, 10, 3, 10)
    moved = DraftPreparationBlock('moved', 'source-moved', 'Moved', start,
                                  start + timedelta(minutes=20), 20, 'reason')
    deleted = replace(moved, id='deleted', source_event_id='source-deleted')
    other = replace(moved, id='work', source_event_id='source-work', calendar=CalendarRoute.WORK)
    old = DraftOperation('old', [moved, deleted, other],
        calendar_ids={CalendarRoute.STUDY.value: 'gone', CalendarRoute.WORK.value: 'work'},
        calendar_event_ids={'moved': 'm', 'deleted': 'd', 'work': 'w'},
        manually_deleted_block_ids=['deleted'], completed_source_event_ids=['finished'],
        manual_calendar_overrides={'moved': {'start': (start + timedelta(hours=2)).isoformat(),
                                          'end': (start + timedelta(hours=2, minutes=20)).isoformat()}})
    restored = _rebound(old, {'gone': 'new'})
    assert restored.calendar_event_ids == {'work': 'w'}
    assert restored.completed_source_event_ids == ['finished']
    assert old.calendar_event_ids == {'moved': 'm', 'deleted': 'd', 'work': 'w'}
    for _ in range(2):
        candidate = DraftOperation('next', [replace(moved, id='fresh-moved'), replace(deleted, id='fresh-deleted')])
        SharedPreparationWorkflow._preserve_manual_actions(restored, candidate)
        assert [block.source_event_id for block in candidate.blocks] == ['source-moved']
        assert candidate.blocks[0].start == start + timedelta(hours=2)
        # The durable history copied by _publish must continue suppressing the
        # deleted source even after its old active block is gone.
        restored = replace(restored, blocks=list(candidate.blocks))


def test_manual_time_already_started_is_not_recreated():
    from datetime import datetime, timedelta
    from dataclasses import replace
    from services.adaptive_preparation_service import DraftOperation, DraftPreparationBlock
    from services.shared_preparation_workflow import SharedPreparationWorkflow
    now = datetime(2026, 10, 3, 10)
    future = DraftPreparationBlock('fresh', 'source', 'Prep', now + timedelta(hours=2),
                                   now + timedelta(hours=2, minutes=20), 20, 'reason')
    previous = DraftOperation('old', [replace(future, id='old')],
        pending_calendar_writes={'old': 'insert'}, manual_calendar_overrides={
            'old': {'start': (now - timedelta(minutes=5)).isoformat(),
                    'end': (now + timedelta(minutes=15)).isoformat()}})
    candidate = DraftOperation('candidate', [future])
    SharedPreparationWorkflow._preserve_manual_actions(previous, candidate, now=now)
    assert candidate.blocks == []
    assert 'уже наступило' in candidate.no_slot_reasons['source']
    assert previous.pending_calendar_writes == {'old': 'insert'}


def test_restart_after_calendar_creation_recovers_from_durable_backup(legacy_runtime):
    targets = []

    def interrupt(bot, adapter, remote):
        publish(bot, legacy_runtime)
        for key in list(remote):
            if key[0] == 'study':
                del remote[key]
        adapter.calendar_is_accessible.side_effect = lambda identifier: identifier != 'study'
        def create(old, name, *, allow_create, before_create):
            assert allow_create
            before_create()
            targets.append('study-new')
            raise TimeoutError('response lost')
        adapter.replacement_calendar.side_effect = create
        reply = bot.handle_text('123', '/preparations')
        assert 'восстановление' in reply['text']
    legacy_runtime.run(interrupt)

    def resume(bot, adapter, remote):
        def discover(old, name, *, allow_create, before_create):
            assert allow_create is False
            return targets[0]
        adapter.replacement_calendar.side_effect = discover
        adapter.list_visible_calendars.return_value = [
            {'id': 'study-new', 'summary': 'Personal University Schedule'},
            {'id': 'work', 'summary': 'Работа'}, {'id': 'personal', 'summary': 'Личное'}]
        reply = bot.handle_text('123', '/preparations')
        assert 'остановлен' not in reply['text']
        assert any(cal == 'study-new' for cal, _ in remote)
        assert targets == ['study-new']
    legacy_runtime.run(resume)


def test_study_only_runtime_recovers_deleted_calendar(legacy_runtime):
    from unittest.mock import patch
    from scripts import telegram_schedule_bot as runtime

    def initial(bot, adapter, remote):
        publish(bot, legacy_runtime)
        for key in list(remote):
            if key[0] == 'study':
                del remote[key]
        adapter.calendar_is_accessible.side_effect = lambda identifier: identifier != 'study'
        adapter.replacement_calendar.return_value = 'study-new'
        adapter.list_visible_calendars.return_value = [
            {'id': 'study-new', 'summary': 'Personal University Schedule'},
            {'id': 'work', 'summary': 'Работа'}, {'id': 'personal', 'summary': 'Личное'}]
    legacy_runtime.run(initial)

    def without_crm(bot, adapter, remote):
        reply = bot.handle_text('123', '/preparations')
        assert 'остановлен' not in reply['text']
        assert any(cal == 'study-new' for cal, _ in remote)
        before = deepcopy(remote)
        again = bot.handle_text('123', '/preparations')
        assert 'остановлен' not in again['text']
        assert remote == before
    with patch.object(runtime, 'AlfaCRMScheduleSource', side_effect=ValueError('not configured')):
        legacy_runtime.run(without_crm)


def test_restart_after_first_rebound_store_checkpoint(legacy_runtime):
    from unittest.mock import patch
    from services.draft_operation_store import DraftOperationStore
    save = DraftOperationStore.save
    interrupted = []

    def first(bot, adapter, remote):
        publish(bot, legacy_runtime)
        for key in list(remote):
            if key[0] == 'study':
                del remote[key]
        adapter.calendar_is_accessible.side_effect = lambda identifier: identifier != 'study'
        adapter.replacement_calendar.return_value = 'study-new'
        adapter.list_visible_calendars.return_value = [
            {'id': 'study-new', 'summary': 'Personal University Schedule'},
            {'id': 'work', 'summary': 'Работа'}, {'id': 'personal', 'summary': 'Личное'}]
        def fail_after_save(store, operation):
            save(store, operation)
            if 'study-new' in operation.calendar_ids.values() and not interrupted:
                interrupted.append(True)
                raise OSError('synthetic crash after checkpoint')
        with patch.object(DraftOperationStore, 'save', fail_after_save):
            with pytest.raises(OSError, match='synthetic crash'):
                bot.handle_text('123', '/preparations')
        assert interrupted
        assert not any(cal == 'study-new' for cal, _ in remote)
    legacy_runtime.run(first)

    def second(bot, adapter, remote):
        adapter.replacement_calendar.reset_mock()
        reply = bot.handle_text('123', '/preparations')
        assert 'остановлен' not in reply['text']
        assert any(cal == 'study-new' for cal, _ in remote)
        adapter.replacement_calendar.assert_not_called()
    legacy_runtime.run(second)


def test_vanished_excluded_projection_does_not_attempt_remote_retirement():
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import Mock
    from services.adaptive_preparation_service import DraftOperation, DraftPreparationBlock
    from services.preparation_calendar_recovery import _rebound
    from services.shared_preparation_workflow import SharedPreparationWorkflow
    now = datetime(2026, 10, 3, 10)
    block = DraftPreparationBlock('old', 'excluded', 'Old prep', now,
                                  now + timedelta(minutes=20), 20, 'reason')
    old = DraftOperation('old', [block], calendar_id='gone',
                         calendar_event_ids={'old': 'remote'})
    rebound = _rebound(old, {'gone': 'new'})
    assert rebound.blocks == []
    assert rebound.retired_blocks == [block]
    flow = SimpleNamespace(current_operation=rebound, draft_sync=Mock(),
                           operation_store=Mock(), calendar_projector=Mock())
    assert SharedPreparationWorkflow.retire_retained(
        flow, lambda *_: 'user_excluded', (now, now + timedelta(days=1)), study=True) == 0
    flow.calendar_projector.calendar_adapter.get_event_by_id.assert_not_called()


def test_work_calendar_recovery_precedes_lesson_deletion_detection(legacy_runtime):
    def check(bot, adapter, remote):
        publish(bot, legacy_runtime)
        for key in list(remote):
            if key[0] == 'work':
                del remote[key]
        adapter.calendar_is_accessible.side_effect = lambda identifier: identifier != 'work'
        adapter.replacement_calendar.return_value = 'work-new'
        adapter._get_or_create_calendar.side_effect = {
            'Personal University Schedule': 'study', 'Работа': 'work-new', 'Личное': 'personal'}.__getitem__
        adapter.list_visible_calendars.return_value = [
            {'id': 'study', 'summary': 'Personal University Schedule'},
            {'id': 'work-new', 'summary': 'Работа'}, {'id': 'personal', 'summary': 'Личное'}]
        reply = bot.handle_text('123', '/work_schedule')
        assert 'остановлен' not in reply['text']
        assert any(cal == 'work-new' and event.get('iCalUID') == 'personal-os:work:7:1'
                   for (cal, _), event in remote.items())
        state_path = next(legacy_runtime.path.rglob('work_planning_state.json'))
        assert json.loads(state_path.read_text())['state']['manual_calendar_overrides'] == {}
        before = deepcopy(remote)
        again = bot.handle_text('123', '/work_schedule')
        assert 'остановлен' not in again['text']
        assert remote == before
    legacy_runtime.run(check)


def test_class_only_deleted_calendar_recovers_from_checkpoint_and_survives_restart(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from services.calendar.personal_event_sync_service import PersonalEventSyncService
    from services.calendar.projection_state import CalendarProjectionState
    from services.preparation_calendar_recovery import recover_preparation_calendars

    projection_path = tmp_path / 'university_calendar_projection.json'
    state = CalendarProjectionState(projection_path)
    start, end = '2026-10-12T10:00:00+07:00', '2026-10-12T11:30:00+07:00'
    state.put('class-1', calendar_id='class-old', event_id='historic-event',
              start=start, end=end, override='moved')
    state.put('deleted-class', calendar_id='class-old', event_id=None,
              start='2026-10-13T10:00:00+07:00', end='2026-10-13T11:30:00+07:00',
              override='deleted')
    adapter = Mock()
    adapter.calendar_is_accessible.side_effect = lambda identifier: identifier != 'class-old'
    adapter.replacement_calendar.side_effect = lambda old, name, *, allow_create, before_create: (
        before_create() or 'class-new')
    # No preparation operation references the class calendar.
    workflow = SimpleNamespace(current_operation=None)
    queue = SimpleNamespace(
        path=tmp_path / 'shared_preparation.json',
        study=SimpleNamespace(current_operation=None, operation_store=None, draft_sync=None),
        work=SimpleNamespace(current_operation=None, store=None, sync=None),
        _save=lambda _: None,
    )
    class_sync = PersonalEventSyncService(adapter, state)

    assert recover_preparation_calendars(queue, adapter, class_sync=class_sync)
    assert adapter.replacement_calendar.call_args.args == ('class-old', 'Personal University Schedule')
    assert adapter.replacement_calendar.call_args.kwargs['allow_create'] is True
    assert state.get('class-1') == {
        'calendar_id': 'class-new', 'calendar_recovery': 'class-new',
        'start': start, 'end': end, 'override': 'moved'}
    assert state.get('deleted-class') == {
        'calendar_id': 'class-new', 'calendar_recovery': 'class-new',
        'start': '2026-10-13T10:00:00+07:00',
        'end': '2026-10-13T11:30:00+07:00', 'override': 'deleted'}

    restarted = PersonalEventSyncService(adapter, CalendarProjectionState(projection_path))
    adapter.calendar_is_accessible.side_effect = lambda identifier: True
    adapter.reset_mock()
    assert recover_preparation_calendars(queue, adapter, class_sync=restarted) is False
    adapter.replacement_calendar.assert_not_called()
    assert restarted.projection_state.get('class-1')['start'] == start


@pytest.mark.parametrize('status', [401, 403, 500])
def test_uncertain_class_calendar_read_does_not_create_or_change_checkpoints(tmp_path, status):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from googleapiclient.errors import HttpError
    from httplib2 import Response
    from services.calendar.personal_event_sync_service import PersonalEventSyncService
    from services.calendar.projection_state import CalendarProjectionState
    from services.preparation_calendar_recovery import recover_preparation_calendars
    from services.update_all_workflow import UpdateAllBlocked

    state = CalendarProjectionState(tmp_path / 'university_calendar_projection.json')
    state.put('class-1', calendar_id='class-old', event_id='historic-event',
              start='2026-10-12T10:00:00+07:00', end='2026-10-12T11:30:00+07:00',
              override='moved')
    before = deepcopy(state.rows)
    adapter = Mock()
    adapter.calendar_is_accessible.side_effect = HttpError(
        Response({'status': str(status)}), b'private provider detail')
    queue = SimpleNamespace(path=tmp_path / 'shared_preparation.json',
        study=SimpleNamespace(current_operation=None, operation_store=None, draft_sync=None),
        work=SimpleNamespace(current_operation=None, store=None, sync=None), _save=lambda _: None)
    class_sync = PersonalEventSyncService(adapter, state)

    with pytest.raises(UpdateAllBlocked):
        recover_preparation_calendars(queue, adapter, class_sync=class_sync)
    adapter.replacement_calendar.assert_not_called()
    assert state.rows == before
    assert not list(tmp_path.glob('*.calendar-recovery.json'))


def test_class_create_timeout_resumes_without_blind_create(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from services.calendar.personal_event_sync_service import PersonalEventSyncService
    from services.calendar.projection_state import CalendarProjectionState
    from services.preparation_calendar_recovery import recover_preparation_calendars
    from services.update_all_workflow import UpdateAllBlocked

    state_path = tmp_path / 'university_calendar_projection.json'
    state = CalendarProjectionState(state_path)
    state.put('class-1', calendar_id='class-old', event_id='historic-event',
              start='2026-10-12T10:00:00+07:00', end='2026-10-12T11:30:00+07:00')
    adapter = Mock()
    adapter.calendar_is_accessible.side_effect = lambda identifier: identifier != 'class-old'
    def timeout(old, name, *, allow_create, before_create):
        assert allow_create
        before_create()
        raise TimeoutError('provider response lost')
    adapter.replacement_calendar.side_effect = timeout
    queue = SimpleNamespace(path=tmp_path / 'shared_preparation.json',
        study=SimpleNamespace(current_operation=None, operation_store=None, draft_sync=None),
        work=SimpleNamespace(current_operation=None, store=None, sync=None), _save=lambda _: None)
    class_sync = PersonalEventSyncService(adapter, state)

    with pytest.raises(UpdateAllBlocked):
        recover_preparation_calendars(queue, adapter, class_sync=class_sync)
    journal_path = tmp_path / 'shared_preparation.calendar-recovery.json'
    journal = json.loads(journal_path.read_text())
    assert journal['class_calendars']['class-old']['creation_requested'] is True
    assert journal['class_projection_before']['class-1']['event_id'] == 'historic-event'

    adapter.replacement_calendar.side_effect = lambda old, name, *, allow_create, before_create: (
        'class-new' if allow_create is False else pytest.fail('blind retry'))
    restarted = PersonalEventSyncService(adapter, CalendarProjectionState(state_path))
    assert recover_preparation_calendars(queue, adapter, class_sync=restarted)
    assert restarted.projection_state.get('class-1')['calendar_id'] == 'class-new'
    assert adapter.replacement_calendar.call_args.kwargs['allow_create'] is False


def test_class_checkpoint_rebind_resumes_after_partial_row_persistence(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from services.calendar.personal_event_sync_service import PersonalEventSyncService
    from services.calendar.projection_state import CalendarProjectionState
    from services.preparation_calendar_recovery import recover_preparation_calendars

    state_path = tmp_path / 'university_calendar_projection.json'
    state = CalendarProjectionState(state_path)
    state.put('class-1', calendar_id='class-old', event_id='remote-1', start='s1', end='e1')
    state.put('class-2', calendar_id='class-old', event_id='remote-2', start='s2', end='e2')
    adapter = Mock()
    adapter.calendar_is_accessible.side_effect = lambda identifier: identifier != 'class-old'
    adapter.replacement_calendar.return_value = 'class-new'
    queue = SimpleNamespace(path=tmp_path / 'shared_preparation.json',
        study=SimpleNamespace(current_operation=None, operation_store=None, draft_sync=None),
        work=SimpleNamespace(current_operation=None, store=None, sync=None), _save=lambda _: None)
    class_sync = PersonalEventSyncService(adapter, state)
    save_row = state.put
    writes = []
    def fail_after_one_row(key, **changes):
        save_row(key, **changes)
        writes.append(key)
        if len(writes) == 1:
            raise OSError('synthetic checkpoint interruption')
    state.put = fail_after_one_row

    with pytest.raises(OSError, match='synthetic checkpoint'):
        recover_preparation_calendars(queue, adapter, class_sync=class_sync)
    assert state.get('class-1')['calendar_id'] == 'class-new'
    assert state.get('class-2')['calendar_id'] == 'class-old'

    restarted = PersonalEventSyncService(adapter, CalendarProjectionState(state_path))
    assert recover_preparation_calendars(queue, adapter, class_sync=restarted)
    assert all(row['calendar_id'] == 'class-new'
               for row in restarted.projection_state.rows.values())
    adapter.replacement_calendar.assert_called_once()
