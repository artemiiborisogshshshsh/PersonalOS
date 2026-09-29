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
