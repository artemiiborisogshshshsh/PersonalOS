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
from types import SimpleNamespace


@pytest.mark.parametrize('manual_conflict', [False, 'false', 1, None, True])
def test_preflight_marks_only_literal_true_as_manual_conflict(tmp_path, manual_conflict):
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
    assert result.blocks[0].manual_conflict is True
    if manual_conflict is True:
        assert result.blocks[0] is block
    else:
        # Truthy non-boolean values never authorize an overlap.  The shared
        # workflow performs the required fallback classification itself.
        assert result.blocks[0] is not block
    study.stage.assert_not_called()
    work.stage.assert_not_called()


def test_normal_block_overlapping_manual_proposal_is_reclassified_without_identity_change(tmp_path):
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

    result, _ = SharedPreparationWorkflow(tmp_path / 'queue.json', study, work).calculate()

    reclassified = result.blocks[0]
    assert reclassified.manual_conflict is True
    assert (reclassified.id, reclassified.source_event_id, reclassified.start,
            reclassified.end, reclassified.minutes) == (
                normal.id, normal.source_event_id, normal.start, normal.end, normal.minutes,
            )
    assert reclassified is not normal


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

    assert candidate.blocks == [moved]
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
    with pytest.raises(RuntimeError, match='подготовки пересекаются'):
        queue.run()
    assert not queue.path.exists()
    study.stage.assert_not_called()
    work.stage.assert_not_called()
    study.rollback.assert_not_called()
    work.rollback.assert_not_called()


def test_busy_week_publishes_conflict_preparations_once_and_preserves_lessons(tmp_path):
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
    assert len(remote) == 2
    assert {calendar for calendar, _ in remote} == {'Personal University Schedule', 'Работа'}
    for event in remote.values():
        assert 'Конфликт — перенести вручную' in event.summary
        assert 'Manual conflict: true' in event.description
    assert before == (study_class.start_time, study_class.end_time, work_class.start, work_class.end)
    assert study.current_operation.blocks[0].manual_conflict
    assert work.current_operation.blocks[0].manual_conflict
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

    def update(calendar, identifier, event):
        remote[identifier] = event
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
