from datetime import datetime
from unittest.mock import Mock
import pytest

from services.adaptive_preparation_service import (
    AdaptivePreparationService,
    DraftCalendarProjector,
    DraftPlanSyncService,
)
from services.draft_operation_store import DraftOperationStore
from services.preparation_draft_workflow import PreparationDraftWorkflow
from tests.test_adaptive_preparation_service import personal_event


def test_identical_study_replan_preserves_operation(tmp_path):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'study'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'prep'
    workflow = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'draft.json'), lambda: [personal_event()],
        now_provider=lambda: datetime(2026, 9, 4, 10),
    )
    workflow.preview()
    workflow.stage()
    previous = workflow.current_operation.id
    adapter.reset_mock()
    assert 'План не изменился' in workflow.replan(source_changed=True)
    assert workflow.current_operation.id == previous
    adapter._delete_event.assert_not_called()
    adapter._insert_event.assert_not_called()
    adapter._update_event.assert_not_called()


def test_replan_calendar_read_failure_preserves_published_operation(tmp_path):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'study'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'existing-prep'
    workflow = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'draft.json'), lambda: [personal_event()],
        now_provider=lambda: datetime(2026, 9, 1),
    )
    workflow.preview()
    workflow.stage()
    previous = workflow.current_operation
    workflow.commitments_provider = Mock(side_effect=RuntimeError('Calendar unavailable'))
    with pytest.raises(RuntimeError, match='Calendar unavailable'):
        workflow.replan(source_changed=True)
    assert workflow.current_operation is previous
    assert previous.status == 'draft'
    adapter._delete_event.assert_not_called()
    assert workflow.operation_store.load(previous.id).status == 'draft'


def test_workflow_previews_stages_confirms_and_rolls_back_owned_draft(tmp_path):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'work'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'google-preparation'
    workflow = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(),
        DraftCalendarProjector(adapter), DraftOperationStore(tmp_path / 'drafts.json'),
        events_provider=lambda: [personal_event()],
        now_provider=lambda: datetime(2026, 9, 1),
    )

    preview = workflow.preview()
    staged = workflow.stage()
    confirmed = workflow.confirm()
    rolled_back = workflow.rollback()

    assert 'Черновик подготовок' in preview
    assert 'календаре «Personal University Schedule»' in staged
    assert 'подтверждены' in confirmed
    assert 'восстановлены' in rolled_back
    adapter._delete_event.assert_called_once_with('work', 'google-preparation')


def test_workflow_recovers_pending_draft_after_restart_for_safe_rollback(tmp_path):
    store = DraftOperationStore(tmp_path / 'drafts.json')
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'work'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'google-preparation'
    first = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
        store, lambda: [personal_event()], now_provider=lambda: datetime(2026, 9, 1),
    )
    first.preview()
    first.stage()

    restarted = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
        store, lambda: [personal_event()], now_provider=lambda: datetime(2026, 9, 1),
    )
    restarted.rollback()

    adapter._delete_event.assert_called_once_with('work', 'google-preparation')


def test_preview_never_orphans_an_existing_projected_draft(tmp_path):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'work'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'google-preparation'
    workflow = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'drafts.json'), lambda: [personal_event()],
        now_provider=lambda: datetime(2026, 9, 1),
    )
    workflow.preview()
    workflow.stage()
    operation_id = workflow.current_operation.id

    preview = workflow.preview()

    assert workflow.current_operation.id == operation_id
    assert 'уже созданы в Google Calendar' in preview
    assert adapter._insert_event.call_count == 1


def test_calendar_reset_forgets_deleted_draft_ids_and_allows_fresh_preview(tmp_path):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'work'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'google-preparation'
    workflow = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'drafts.json'), lambda: [personal_event()],
        now_provider=lambda: datetime(2026, 9, 1),
    )
    workflow.preview()
    workflow.stage()

    workflow.reset_deleted_remote_drafts()
    preview = workflow.preview()

    assert workflow.current_operation.status == 'draft'
    assert workflow.current_operation.calendar_event_ids == {}
    assert 'уже созданы в Google Calendar' not in preview


def test_partial_feedback_automatically_replans_only_a_draft(tmp_path):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'work'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'google-preparation'
    workflow = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'drafts.json'), lambda: [personal_event()],
        now_provider=lambda: datetime(2026, 9, 1),
    )
    workflow.preview()
    workflow.stage()
    first_operation_id = workflow.current_operation.id

    reply = workflow.feedback(workflow.current_operation.blocks[0].id, 'partial', 20, 4)

    assert 'автоматически обновлены' in reply
    assert workflow.current_operation.id != first_operation_id
    adapter._delete_event.assert_called_once_with('work', 'google-preparation')


def test_replan_never_changes_confirmed_preparations_automatically(tmp_path):
    adapter = Mock()
    workflow = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'drafts.json'), lambda: [personal_event()],
        now_provider=lambda: datetime(2026, 9, 1),
    )
    workflow.preview()
    workflow.current_operation.status = 'confirmed'

    reply = workflow.replan()

    assert 'не меняются автоматически' in reply
    adapter._delete_event.assert_not_called()


def test_source_change_updates_confirmed_system_block_and_reports_reason(tmp_path):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'study'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'google-preparation'
    adapter._update_event.return_value = 'google-preparation'
    event = personal_event()
    workflow = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'drafts.json'), lambda: [event],
        now_provider=lambda: datetime(2026, 9, 1),
    )
    workflow.preview()
    workflow.stage()
    workflow.confirm()
    event.start_time = event.start_time.replace(day=11)
    event.end_time = event.end_time.replace(day=11)

    reply = workflow.replan(source_changed=True)

    assert 'Подтверждённые подготовки обновлены после изменения TPU' in reply
    assert adapter._update_event.called


def test_replan_preserves_projected_draft_when_calendar_is_not_initialized(tmp_path):
    class UnreadyAdapter:
        service = None
        is_initialized = False

    workflow = PreparationDraftWorkflow(
        AdaptivePreparationService(), DraftPlanSyncService(),
        DraftCalendarProjector(UnreadyAdapter()), DraftOperationStore(tmp_path / 'drafts.json'),
        lambda: [personal_event()], now_provider=lambda: datetime(2026, 9, 1),
    )
    workflow.preview()
    workflow.current_operation.calendar_event_ids['block'] = 'google-id'

    reply = workflow.replan()

    assert 'Google Calendar ещё не подключён' in reply
