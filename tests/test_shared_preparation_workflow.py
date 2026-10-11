import json
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from services.adaptive_preparation_service import AdaptivePreparationService, DraftCalendarProjector, DraftPlanSyncService
from services.draft_operation_store import DraftOperationStore
from services.preparation_draft_workflow import PreparationDraftWorkflow
from services.shared_preparation_workflow import SharedPreparationWorkflow
from services.work_schedule_service import WorkPreparationPlanner, WorkPreparationWorkflow
from tests.test_adaptive_preparation_service import personal_event
from tests.test_work_schedule_service import lesson, service
from services.weekly_plan_service import FixedCommitment
from services.update_all_workflow import UpdateAllBlocked
from types import SimpleNamespace


def install_owned_reads(adapter):
    stored = {}
    insert = adapter._insert_event.side_effect
    update = adapter._update_event.side_effect
    def record_insert(event, calendar, **kwargs):
        identifier = insert(event, calendar)
        if identifier:
            stored[event.uid] = (identifier, event)
        return identifier
    def record_update(calendar, identifier, event, **kwargs):
        result = update(calendar, identifier, event) if callable(update) else identifier
        if result:
            stored[event.uid] = (identifier, event)
        return result
    def as_remote(identifier, event):
        return {'id': identifier, 'iCalUID': event.uid,
                'summary': event.summary, 'description': event.description,
                'start': {'dateTime': event.dtstart.isoformat()},
                'end': {'dateTime': event.dtend.isoformat()},
                'extendedProperties': {'private': {
                    'personal_os_block_id': event.system_block_id,
                    'personal_os_operation_id': event.system_operation_id}}}
    adapter._insert_event.side_effect = record_insert
    adapter._update_event.side_effect = record_update
    adapter.get_event_by_uid.side_effect = lambda calendar, uid, **kwargs: (
        as_remote(*stored[uid]) if uid in stored else None)
    adapter.get_event_by_id.side_effect = lambda calendar, identifier, **kwargs: next(
        (as_remote(saved_id, event) for saved_id, event in stored.values()
         if saved_id == identifier), None)
    return stored


@pytest.mark.parametrize('manual_conflict', [False, 'false', 1, None, True])
def test_preflight_excludes_conflicting_proposals_regardless_of_marker(tmp_path, manual_conflict):
    from services.adaptive_preparation_service import DraftOperation, DraftPreparationBlock
    start = datetime(2026, 9, 12, 10)
    block = DraftPreparationBlock('prep', 'source', 'Preparation', start,
        start + timedelta(minutes=20), 20, 'No free slot', manual_conflict=manual_conflict)
    study = Mock(current_operation=None, completed_source_event_ids=set(),
                 carryover_minutes_by_course={})
    work = Mock(current_operation=None)
    study.planner.build_draft.return_value = DraftOperation('study', [block])
    work.planner.build_draft.return_value = DraftOperation('work', [])
    busy = FixedCommitment('lesson', 'Fixed lesson', start, start + timedelta(hours=1))
    for flow in (study, work):
        flow.commitments_provider.return_value = [busy]
        flow.now_provider.return_value = datetime(2026, 9, 11)
    study.events_provider.return_value = []
    study.flexible_items_provider.return_value = []
    work.lessons_provider.return_value = []
    work.university_provider.return_value = []
    queue = SharedPreparationWorkflow(tmp_path / 'queue.json', study, work)
    result, _ = queue.calculate()
    assert result.blocks == []
    assert result.no_slot_reasons['source']
    assert any('подготовка не создана' in text for text in result.explanations)
    study.stage.assert_not_called()
    work.stage.assert_not_called()


def test_unpublished_manual_proposal_does_not_displace_a_valid_block(tmp_path):
    from services.adaptive_preparation_service import DraftOperation, DraftPreparationBlock

    start = datetime(2026, 9, 12, 10)
    normal = DraftPreparationBlock(
        'ordinary-prep', 'ordinary-source', 'Ordinary preparation', start,
        start + timedelta(minutes=20), 20, 'No free slot', manual_conflict=False,
    )
    manual = DraftPreparationBlock(
        'manual-prep', 'manual-source', 'Manual proposal', start + timedelta(minutes=5),
        start + timedelta(minutes=25), 20, 'No free slot', manual_conflict=True,
    )
    study = Mock(current_operation=None, completed_source_event_ids=set(),
                 carryover_minutes_by_course={})
    work = Mock(current_operation=None)
    study.planner.build_draft.return_value = DraftOperation('study', [normal])
    work.planner.build_draft.return_value = DraftOperation('work', [manual])
    for workflow in (study, work):
        workflow.commitments_provider.return_value = []
        workflow.now_provider.return_value = datetime(2026, 9, 11)
    study.events_provider.return_value = []
    study.flexible_items_provider.return_value = []
    work.lessons_provider.return_value = []
    work.university_provider.return_value = []

    result, work_result = SharedPreparationWorkflow(tmp_path / 'queue.json', study, work).calculate()

    assert result.blocks == [normal]
    assert work_result.blocks == []
    assert work_result.no_slot_reasons['manual-source']


def test_shared_refresh_preserves_manual_move_and_manual_delete():
    from services.adaptive_preparation_service import DraftOperation, DraftPreparationBlock

    start = datetime(2026, 9, 12, 10)
    moved = DraftPreparationBlock('old-moved', 'moved-source', 'Moved', start,
                                  start + timedelta(minutes=20), 20, 'old')
    deleted = DraftPreparationBlock('old-deleted', 'deleted-source', 'Deleted', start,
                                    start + timedelta(minutes=20), 20, 'old')
    previous = DraftOperation('old', [moved, deleted])
    previous.manual_calendar_overrides[moved.id] = {
        'start': (start + timedelta(hours=1)).isoformat(),
        'end': (start + timedelta(hours=1, minutes=20)).isoformat(),
    }
    previous.manually_deleted_block_ids.append(deleted.id)
    candidate = DraftOperation('new', [
        DraftPreparationBlock('new-moved', 'moved-source', 'new moved', start,
                              start + timedelta(minutes=20), 20, 'new'),
        DraftPreparationBlock('new-deleted', 'deleted-source', 'new deleted', start,
                              start + timedelta(minutes=20), 20, 'new'),
    ])

    SharedPreparationWorkflow._preserve_manual_actions(previous, candidate)

    assert candidate.blocks == []
    assert candidate.no_slot_reasons == {'deleted-source': 'подготовка удалена пользователем вручную'}
    assert any('сохранён ручной перенос' in text for text in candidate.explanations)
    assert any('не восстановлена' in text for text in candidate.explanations)


def test_preflight_rejects_cross_scope_overlap_without_any_writes(tmp_path):
    start = datetime(2026, 9, 12, 10)
    study = Mock(current_operation=None, completed_source_event_ids=set(),
                 carryover_minutes_by_course={})
    work = Mock(current_operation=None)
    for workflow in (study, work):
        workflow.commitments_provider.return_value = []
        workflow.now_provider.return_value = datetime(2026, 9, 11)
        workflow.planner.build_draft.return_value = SimpleNamespace(blocks=[
            SimpleNamespace(id='prep', title='Preparation', start=start,
                            end=start + timedelta(minutes=20)),
        ])
    study.events_provider.return_value = []
    study.flexible_items_provider.return_value = []
    work.lessons_provider.return_value = []
    work.university_provider.return_value = []
    queue = SharedPreparationWorkflow(tmp_path / 'queue.json', study, work)
    with pytest.raises(UpdateAllBlocked, match='подготовки пересекаются'):
        queue.run()
    assert not queue.path.exists()
    study.stage.assert_not_called()
    work.stage.assert_not_called()
    study.rollback.assert_not_called()
    work.rollback.assert_not_called()


def test_transition_cycle_requires_review_without_writes_or_discarding_journal(tmp_path):
    from dataclasses import replace
    from services.adaptive_preparation_service import DraftOperation, DraftPreparationBlock

    start = datetime(2026, 9, 12, 10)
    first = DraftPreparationBlock('a', 'source-a', 'First', start,
                                  start + timedelta(minutes=20), 20, 'test')
    second = DraftPreparationBlock('b', 'source-b', 'Second', start + timedelta(hours=1),
                                   start + timedelta(hours=1, minutes=20), 20, 'test')
    previous = DraftOperation('saved-study', [first, second],
                              calendar_event_ids={'a': 'calendar-a', 'b': 'calendar-b'})
    study = Mock(current_operation=previous, completed_source_event_ids=set(),
                 carryover_minutes_by_course={})
    work = Mock(current_operation=None)
    study.planner.build_draft.return_value = DraftOperation('new-study', [
        replace(first, start=second.start, end=second.end),
        replace(second, start=first.start, end=first.end),
    ])
    work.planner.build_draft.return_value = DraftOperation('new-work', [])
    for workflow in (study, work):
        workflow.commitments_provider.return_value = []
        workflow.now_provider.return_value = start - timedelta(days=1)
    study.events_provider.return_value = []
    study.flexible_items_provider.return_value = []
    work.lessons_provider.return_value = []
    work.university_provider.return_value = []
    journal = tmp_path / 'queue.json'
    journal.write_text(json.dumps({'version': 2, 'phase': 'complete'}))
    original_journal = journal.read_bytes()

    with pytest.raises(UpdateAllBlocked, match='Передай этот отчёт разработчику'):
        SharedPreparationWorkflow(journal, study, work).run()

    assert journal.read_bytes() == original_journal
    assert study.current_operation is previous
    assert previous.blocks == [first, second]
    study.stage.assert_not_called()
    work.stage.assert_not_called()
    study.rollback.assert_not_called()
    work.rollback.assert_not_called()


@pytest.mark.parametrize('interrupt_at', ['after_study', 'complete_checkpoint', 'external_busy'])
def test_real_planners_retry_cycle_with_old_projections_fixed(tmp_path, interrupt_at):
    """The bounded retry lets real planners find destinations around both old slots."""
    from dataclasses import replace
    from services.adaptive_preparation_service import (
        AdaptivePreparationService, CalendarRoute, DraftOperation, DraftPreparationBlock,
    )
    now = datetime(2026, 9, 4, 9)
    study_source = personal_event()
    work_source = lesson(start=datetime(2026, 9, 10, 18))
    work_service, _ = service(tmp_path)
    work_service.set_mode(work_source, 'online')
    study_planner = AdaptivePreparationService()
    work_planner = WorkPreparationPlanner(work_service)
    real_study_build, real_work_build = study_planner.build_draft, work_planner.build_draft
    study_planner.build_draft = Mock(wraps=real_study_build)
    work_planner.build_draft = Mock(wraps=real_work_build)
    study_fresh = study_planner.build_draft([study_source], now=now)
    work_fresh = work_planner.build_draft([work_source], [], [], now)
    study_planner.build_draft.reset_mock()
    work_planner.build_draft.reset_mock()
    assert study_fresh.blocks and work_fresh.blocks
    study_target, work_target = study_fresh.blocks[0], work_fresh.blocks[0]
    assert (study_target.start, study_target.end) != (work_target.start, work_target.end)

    # Each scope's old published event occupies the other scope's destination,
    # so the initial transition graph is cyclic while its final layout is free.
    old_study = replace(study_target, start=work_target.start,
                        end=work_target.start + (study_target.end - study_target.start))
    old_work = replace(work_target, start=study_target.start,
                       end=study_target.start + (work_target.end - work_target.start),
                       calendar=CalendarRoute.WORK)
    study_op = DraftOperation('old-study-op', [old_study],
                               calendar_event_ids={old_study.id: 'google-old-study'},
                               calendar_id='study-calendar')
    work_op = DraftOperation('old-work-op', [old_work],
                              calendar_event_ids={old_work.id: 'google-old-work'},
                              calendar_id='work-calendar', scope='work-preparation')

    study_store = DraftOperationStore(tmp_path / 'study-store.json')
    work_store = DraftOperationStore(tmp_path / 'work-store.json')
    study_store.save(study_op)
    work_store.save(work_op)

    study_sync, work_sync = DraftPlanSyncService(), DraftPlanSyncService()
    study_sync.stage(study_op)
    study_sync.calendar_event_ids.update(study_op.calendar_event_ids)
    work_sync.stage(work_op)
    work_sync.calendar_event_ids.update(work_op.calendar_event_ids)
    study_sync.preview = Mock(return_value='study')
    work_sync.preview = Mock(return_value='work')
    study = SimpleNamespace(
        current_operation=study_op, completed_source_event_ids=set(),
        carryover_minutes_by_course={}, planner=study_planner,
        events_provider=lambda: [study_source], flexible_items_provider=lambda: [],
        commitments_provider=lambda: [], now_provider=lambda: now,
        calendar_projector=SimpleNamespace(capture_manual_actions=lambda *a, **k: None),
        operation_store=study_store,
        draft_sync=study_sync,
    )
    work = SimpleNamespace(
        current_operation=work_op, planner=work_planner,
        lessons_provider=lambda: [work_source], university_provider=lambda: [],
        commitments_provider=lambda: [], now_provider=lambda: now,
        projector=SimpleNamespace(capture_manual_actions=lambda *a, **k: None),
        store=work_store,
        sync=work_sync,
    )
    update_ids = {'study': [], 'work': []}
    def publish_stage(workflow, study_scope):
        candidate = workflow.current_operation
        previous_ids = candidate.previous_calendar_event_ids
        previous = {block.source_event_id: block for block in candidate.previous_blocks}
        for block in candidate.blocks:
            old = previous.get(block.source_event_id)
            event_id = previous_ids.get(old.id) if old else None
            assert event_id, (f'replacements should update their existing Calendar event: '
                              f'{candidate.previous_blocks=} {candidate.previous_calendar_event_ids=}')
            candidate.calendar_event_ids[block.id] = event_id
            update_ids['study' if study_scope else 'work'].append(event_id)
        candidate.projection_pending = False
        workflow.current_operation = candidate
        (study_store if study_scope else work_store).save(candidate)
        return 'published'
    study.stage = lambda: publish_stage(study, True)
    fail_work_once = {'value': interrupt_at in {'after_study', 'external_busy'}}
    def stage_work():
        if fail_work_once['value']:
            fail_work_once['value'] = False
            raise RuntimeError('simulated interruption after study')
        return publish_stage(work, False)
    work.stage = stage_work
    queue = SharedPreparationWorkflow(tmp_path / 'queue.json', study, work)
    if interrupt_at == 'complete_checkpoint':
        original_save = queue._save
        def fail_final_checkpoint(state):
            if state.get('phase') == 'complete':
                raise RuntimeError('simulated final checkpoint failure')
            original_save(state)
        queue._save = fail_final_checkpoint

    expected_failure = ('simulated interruption' if interrupt_at != 'complete_checkpoint'
                        else 'simulated final checkpoint')
    with pytest.raises(RuntimeError, match=expected_failure):
        queue.run()
    assert study_planner.build_draft.call_count == 2
    assert work_planner.build_draft.call_count == 2
    first_state = json.loads(queue.path.read_text(encoding='utf-8'))
    assert first_state['phase'] == 'work'
    assert len(first_state['fallback_reservations']) == 2
    result = [DraftOperationStore._deserialize(item) for item in first_state['candidates']]
    old_intervals = [(old_study.start, old_study.end), (old_work.start, old_work.end)]
    all_new = [*result[0].blocks, *result[1].blocks]
    assert all_new, 'real planners should find replacement slots in this synthetic week'
    assert all(not any(a < block.end and block.start < b for a, b in old_intervals)
               for block in all_new)
    assert not any(a.start < b.end and b.start < a.end
                   for index, a in enumerate(all_new) for b in all_new[index + 1:])
    # Reconstruct the workflows and sync caches from durable operation stores,
    # as a process restart would.
    saved_ids = [item['id'] for item in first_state['candidates']]
    study.current_operation = study_store.load(saved_ids[0])
    work_operations = work_store.load_all()
    work.current_operation = work_operations.get(saved_ids[1], work_op)
    study.draft_sync = DraftPlanSyncService()
    study.draft_sync.operations[study.current_operation.id] = study.current_operation
    study.draft_sync.current_blocks.update({block.id: block for block in study.current_operation.blocks})
    study.draft_sync.calendar_event_ids.update(study.current_operation.calendar_event_ids)
    work.sync = DraftPlanSyncService()
    work.sync.operations[work.current_operation.id] = work.current_operation
    work.sync.current_blocks.update({block.id: block for block in work.current_operation.blocks})
    work.sync.calendar_event_ids.update(work.current_operation.calendar_event_ids)
    study.stage = lambda: publish_stage(study, True)
    work.stage = lambda: publish_stage(work, False)
    queue = SharedPreparationWorkflow(queue.path, study, work)
    if interrupt_at == 'external_busy':
        busy_block = result[1].blocks[0]
        new_busy = FixedCommitment('new-external-busy', 'New external busy',
                                   busy_block.start, busy_block.end)
        study.commitments_provider = lambda: [new_busy]
        work.commitments_provider = lambda: [new_busy]
        before_resume = queue.path.read_bytes()
        with pytest.raises(UpdateAllBlocked, match='Условия незавершённого плана изменились'):
            queue.run()
        assert queue.path.read_bytes() == before_resume
        assert update_ids == {'study': ['google-old-study'], 'work': []}
        return
    queue.run()
    assert json.loads(queue.path.read_text(encoding='utf-8'))['phase'] == 'complete'
    completed_updates = {scope: list(ids) for scope, ids in update_ids.items()}
    SharedPreparationWorkflow(queue.path, study, work).run()
    assert update_ids == completed_updates
    assert update_ids['study'] == ['google-old-study']
    assert update_ids['work'] == ['google-old-work']
    assert study.current_operation.calendar_event_ids == {
        result[0].blocks[0].id: 'google-old-study',
    }
    assert work.current_operation.calendar_event_ids == {
        result[1].blocks[0].id: 'google-old-work',
    }


@pytest.mark.parametrize('reservation', [
    None,
    {'id': [], 'title': 'Old block', 'scope': 'work-preparation',
     'start': '2026-09-05T10:00:00', 'end': '2026-09-05T10:30:00'},
    {'id': 'old', 'title': 'Old block', 'scope': 'work-preparation',
     'start': 'bad date', 'end': '2026-09-05T10:30:00'},
])
def test_malformed_fallback_journal_is_preserved_before_provider_reads(tmp_path, reservation):
    study = Mock()
    work = Mock()
    journal = tmp_path / 'queue.json'
    original = {'version': 2, 'phase': 'work', 'fallback_reservations': [reservation]}
    journal.write_text(json.dumps(original))
    before = journal.read_bytes()

    with pytest.raises(UpdateAllBlocked, match='занятые интервалы'):
        SharedPreparationWorkflow(journal, study, work).run()

    assert journal.read_bytes() == before
    study.events_provider.assert_not_called()
    work.lessons_provider.assert_not_called()


def test_busy_week_reports_no_slot_without_writes_and_preserves_lessons(tmp_path):
    work_service, adapter = service(tmp_path)
    adapter._get_or_create_calendar.side_effect = lambda name: name
    remote = {}
    def insert(event, calendar):
        identifier = 'google-' + event.uid
        remote[(calendar, identifier)] = event
        return identifier
    adapter._insert_event.side_effect = insert
    adapter.event_exists_by_uid.side_effect = lambda calendar, uid: next(
        (identifier for (cal, identifier), event in remote.items()
         if cal == calendar and event.uid == uid), None)
    adapter._update_event.side_effect = lambda calendar, identifier, event: identifier
    install_owned_reads(adapter)
    busy = FixedCommitment('busy-week', 'Busy week', datetime(2026, 9, 1), datetime(2026, 9, 20))
    study_class, work_class = personal_event(), lesson()
    before = study_class.start_time, study_class.end_time, work_class.start, work_class.end
    def workflows():
        return (
            PreparationDraftWorkflow(AdaptivePreparationService(), DraftPlanSyncService(),
                DraftCalendarProjector(adapter), DraftOperationStore(tmp_path / 'study.json'),
                lambda: [study_class], commitments_provider=lambda: [busy],
                now_provider=lambda: datetime(2026, 9, 4, 10)),
            WorkPreparationWorkflow(WorkPreparationPlanner(work_service), DraftCalendarProjector(adapter),
                DraftOperationStore(tmp_path / 'work-drafts.json'), lambda: [work_class], lambda: [],
                commitments_provider=lambda: [busy], now_provider=lambda: datetime(2026, 9, 4, 10)),
        )
    study, work = workflows()
    SharedPreparationWorkflow(tmp_path / 'queue.json', study, work).run()
    assert remote == {}
    assert study.current_operation.blocks == []
    assert work.current_operation.blocks == []
    assert study.current_operation.no_slot_reasons[study_class.id]
    assert work.current_operation.no_slot_reasons[work_class.id]
    assert before == (study_class.start_time, study_class.end_time, work_class.start, work_class.end)
    adapter.reset_mock()
    study, work = workflows()
    SharedPreparationWorkflow(tmp_path / 'queue.json', study, work).run()
    adapter._insert_event.assert_not_called()
    adapter._update_event.assert_not_called()
    adapter._delete_event.assert_not_called()


@pytest.mark.parametrize('failure', ['calculate', 'publish', 'checkpoint'])
def test_resume_work_after_restart_preserves_completed_study(tmp_path, failure):
    work_service, adapter = service(tmp_path)
    remote = {}
    inserts = []
    broken = True

    def insert(event, calendar):
        if event.event_type == 'WORK_PREPARATION' and broken and failure == 'publish':
            return None
        inserts.append(event.uid)
        remote[event.uid] = 'event-' + event.uid
        return remote[event.uid]

    adapter._insert_event.side_effect = insert
    adapter.event_exists_by_uid.side_effect = lambda calendar, uid: remote.get(uid)
    adapter._update_event.side_effect = lambda calendar, identifier, event: identifier
    install_owned_reads(adapter)

    def commitments():
        if broken and failure == 'calculate':
            raise RuntimeError('read failed')
        return []

    def workflows():
        study = PreparationDraftWorkflow(
            AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
            DraftOperationStore(tmp_path / 'study.json'), lambda: [personal_event()],
            now_provider=lambda: datetime(2026, 9, 4, 10),
        )
        work = WorkPreparationWorkflow(
            WorkPreparationPlanner(work_service), DraftCalendarProjector(adapter),
            DraftOperationStore(tmp_path / 'work-drafts.json'), lambda: [lesson()], lambda: [],
            commitments_provider=commitments, now_provider=lambda: datetime(2026, 9, 4, 10),
        )
        return study, work

    journal = tmp_path / 'queue.json'
    study, work = workflows()
    queue = SharedPreparationWorkflow(journal, study, work)
    real_save = queue._save

    def save(state):
        if failure == 'checkpoint' and broken and state['phase'] == 'work':
            raise RuntimeError('process stopped before journal checkpoint')
        real_save(state)

    queue._save = save
    with pytest.raises(RuntimeError):
        queue.run()
    if failure == 'calculate':
        assert study.current_operation is None
        assert work.current_operation is None
        assert not journal.exists()
        adapter._insert_event.assert_not_called()
        adapter._update_event.assert_not_called()
        adapter._delete_event.assert_not_called()
        broken = False
        SharedPreparationWorkflow(journal, *workflows()).run()
        assert len(inserts) == 2
        return
    study_id = study.current_operation.id
    assert json.loads(journal.read_text())['phase'] == ('study' if failure == 'checkpoint' else 'work')
    broken = False
    study, work = workflows()
    study.replan = Mock(side_effect=AssertionError('completed study must not be rebuilt'))
    SharedPreparationWorkflow(journal, study, work).run()
    assert study.current_operation.id == study_id
    assert json.loads(journal.read_text())['phase'] == 'complete'
    assert len(remote) == 2
    assert len(inserts) == 2
    adapter._delete_event.assert_not_called()


def test_repeated_shared_queue_after_restart_performs_no_calendar_writes(tmp_path):
    work_service, adapter = service(tmp_path)
    remote = {}
    extra_busy = []

    def insert(event, calendar):
        identifier = 'google-' + event.uid
        remote[identifier] = event
        return identifier

    adapter._insert_event.side_effect = insert
    adapter.event_exists_by_uid.side_effect = lambda calendar, uid: next(
        (identifier for identifier, event in remote.items() if event.uid == uid), None,
    )
    adapter._update_event.side_effect = lambda calendar, identifier, event: identifier
    owned = install_owned_reads(adapter)

    def busy():
        return [*extra_busy, *[FixedCommitment(
            identifier, event.summary, event.dtstart, event.dtend,
            metadata={'google_event_id': identifier, 'preparation_scope':
                      'university-preparation' if event.event_type == 'PREPARATION' else 'work-preparation'},
        ) for identifier, event in remote.items()]]

    def workflows():
        return (
            PreparationDraftWorkflow(
                AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
                DraftOperationStore(tmp_path / 'study.json'), lambda: [personal_event()],
                commitments_provider=busy, now_provider=lambda: datetime(2026, 9, 4, 10),
            ),
            WorkPreparationWorkflow(
                WorkPreparationPlanner(work_service), DraftCalendarProjector(adapter),
                DraftOperationStore(tmp_path / 'work-drafts.json'), lambda: [lesson()], lambda: [],
                commitments_provider=busy, now_provider=lambda: datetime(2026, 9, 4, 10),
            ),
        )

    journal = tmp_path / 'queue.json'
    study, work = workflows()
    SharedPreparationWorkflow(journal, study, work).run()
    previous_ids = study.current_operation.id, work.current_operation.id
    assert len(remote) == 2
    study, work = workflows()
    adapter.reset_mock()
    replies = SharedPreparationWorkflow(journal, study, work).run()
    assert all('не изменился' in reply for reply in replies)
    assert previous_ids == (study.current_operation.id, work.current_operation.id)
    adapter._delete_event.assert_not_called()
    adapter._insert_event.assert_not_called()
    adapter._update_event.assert_not_called()

    # A real new commitment invalidates the comparison even when source
    # lesson IDs and hashes have not changed.
    block = work.current_operation.blocks[0]
    extra_busy.append(FixedCommitment('new-meeting', 'Встреча', block.start,
                                     block.end + timedelta(minutes=10)))
    assert not SharedPreparationWorkflow(journal, study, work)._unchanged()

    def update(calendar, identifier, event, **kwargs):
        remote[identifier] = event
        owned[event.uid] = (identifier, event)
        return identifier

    adapter._update_event.side_effect = update
    old_event_id = work.current_operation.calendar_event_ids[block.id]
    study.replan = Mock(side_effect=AssertionError('must publish calculated plan'))
    work.replan = Mock(side_effect=AssertionError('must publish calculated plan'))
    adapter.reset_mock()
    SharedPreparationWorkflow(journal, study, work).run()
    adapter._delete_event.assert_not_called()
    adapter._insert_event.assert_not_called()
    assert adapter._update_event.call_count == 1
    assert work.current_operation.calendar_event_ids[work.current_operation.blocks[0].id] == old_event_id
    assert work.current_operation.blocks[0].start != block.start

    # An omitted source is not proof of cancellation. Retain its projection
    # and ownership through an empty candidate and a process restart.
    work.lessons_provider = lambda: []
    adapter.reset_mock()
    SharedPreparationWorkflow(journal, study, work).run()
    assert work.current_operation.blocks == []
    assert len(work.current_operation.retained_blocks) == 1
    assert old_event_id in work.current_operation.calendar_event_ids.values()
    adapter._delete_event.assert_not_called()
    adapter._insert_event.assert_not_called()
    study, work = workflows()
    work.lessons_provider = lambda: []
    assert len(work.current_operation.retained_blocks) == 1
    retained = work.current_operation.retained_blocks[0]
    assert retained.id in work.sync.current_blocks
    SharedPreparationWorkflow(journal, study, work).run()
    adapter._update_event.assert_not_called()


def test_shared_busy_union_protects_both_scopes_and_refresh_is_idempotent(tmp_path):
    from dataclasses import replace

    instance, adapter = service(tmp_path)
    adapter._get_or_create_calendar.side_effect = lambda name: name
    adapter._insert_event.side_effect = lambda event, calendar: 'google-' + event.uid
    install_owned_reads(adapter)
    now = datetime(2026, 9, 5, 10)
    classes = [replace(personal_event('lecture'), id=f'study-{i}',
                       start_time=datetime(2026, 9, 7, 10 + i),
                       end_time=datetime(2026, 9, 7, 11 + i)) for i in range(3)]
    lessons = [lesson(f'work-{i}', datetime(2026, 9, 7, 16 + i)) for i in range(2)]
    for item in lessons:
        instance.set_route(item.id, 'direct')
    # Each provider knows a different occupied interval. Both planners must
    # see both; the night boundary is also a hard profile commitment.
    study_busy = [FixedCommitment('study-busy', 'Study busy', now + timedelta(hours=1),
                                  now + timedelta(hours=2))]
    work_busy = [FixedCommitment('work-busy', 'Work busy', now, now + timedelta(hours=1)),
                 FixedCommitment('sleep', 'Profile sleep', now.replace(hour=22, minute=20),
                                  datetime(2026, 9, 6, 10))]
    study = PreparationDraftWorkflow(AdaptivePreparationService(), DraftPlanSyncService(),
        DraftCalendarProjector(adapter), DraftOperationStore(tmp_path / 'study.json'),
        lambda: classes, commitments_provider=lambda: study_busy, now_provider=lambda: now)
    work = WorkPreparationWorkflow(WorkPreparationPlanner(instance), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'work-drafts.json'), lambda: lessons, lambda: classes,
        commitments_provider=lambda: work_busy, now_provider=lambda: now)
    queue = SharedPreparationWorkflow(tmp_path / 'queue.json', study, work)
    queue.run()
    blocks = [*study.current_operation.blocks, *work.current_operation.blocks]
    assert len(blocks) >= 3
    assert study.current_operation.blocks and work.current_operation.blocks
    assert len(blocks) + len(study.current_operation.no_slot_reasons) + len(work.current_operation.no_slot_reasons) == 5
    for index, block in enumerate(blocks):
        assert not block.manual_conflict
        assert all(block.end <= item.start or block.start >= item.end
                   for item in [*study_busy, *work_busy, *blocks[:index]])
    old_ids = (dict(study.current_operation.calendar_event_ids),
               dict(work.current_operation.calendar_event_ids))
    adapter.reset_mock()
    queue.run()
    assert old_ids == (study.current_operation.calendar_event_ids, work.current_operation.calendar_event_ids)
    adapter._insert_event.assert_not_called()
    adapter._update_event.assert_not_called()
    adapter._delete_event.assert_not_called()


def test_new_no_slot_result_keeps_old_published_projection_without_writes(tmp_path):
    instance, adapter = service(tmp_path)
    adapter._get_or_create_calendar.side_effect = lambda name: name
    adapter._insert_event.side_effect = lambda event, calendar: 'google-' + event.uid
    install_owned_reads(adapter)
    now = datetime(2026, 9, 4, 10)
    busy = []
    study = PreparationDraftWorkflow(AdaptivePreparationService(), DraftPlanSyncService(),
        DraftCalendarProjector(adapter), DraftOperationStore(tmp_path / 'study.json'),
        lambda: [personal_event()], commitments_provider=lambda: busy, now_provider=lambda: now)
    work = WorkPreparationWorkflow(WorkPreparationPlanner(instance), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'work-drafts.json'), lambda: [], lambda: [],
        now_provider=lambda: now)
    queue = SharedPreparationWorkflow(tmp_path / 'queue.json', study, work)
    queue.run()
    old = study.current_operation.blocks[0]
    old_id = study.current_operation.calendar_event_ids[old.id]
    busy.append(FixedCommitment('busy-week', 'Busy week', now, datetime(2026, 9, 20)))
    adapter.reset_mock()
    study_reply, _ = queue.run()
    assert study.current_operation.blocks == []
    assert study.current_operation.retained_blocks == [old]
    assert study.current_operation.calendar_event_ids[old.id] == old_id
    assert 'прежние конфликты требуют проверки' in study_reply
    queue.run()
    adapter._insert_event.assert_not_called()
    adapter._update_event.assert_not_called()
    adapter._delete_event.assert_not_called()
