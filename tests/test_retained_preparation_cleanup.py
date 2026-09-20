from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.adaptive_preparation_service import (
    CalendarRoute, DraftOperation, DraftPreparationBlock, DraftCalendarProjector,
    DraftPlanSyncService,
)
from services.draft_operation_store import DraftOperationStore
from services.shared_preparation_workflow import SharedPreparationWorkflow


def fixture(tmp_path):
    start = datetime(2026, 9, 12, 10)
    block = DraftPreparationBlock('prep', '7:lesson', 'Preparation', start,
        start + timedelta(minutes=20), 20, '', CalendarRoute.WORK)
    operation = DraftOperation('op', [], scope='work-preparation', retained_blocks=[block],
        calendar_ids={'Работа': 'work'}, calendar_event_ids={'prep': 'google-id'},
        previous_blocks=[block], previous_calendar_event_ids={'prep': 'google-id'})
    store = DraftOperationStore(tmp_path / 'operations.json')
    store.save(operation)
    adapter = Mock()
    adapter._delete_event.return_value = True
    remote = {
        'id': 'google-id',
        'start': {'dateTime': block.start.isoformat()},
        'end': {'dateTime': block.end.isoformat()},
        'extendedProperties': {'private': {
            'personal_os_block_id': block.id,
            'personal_os_operation_id': operation.id,
        }},
    }
    adapter.get_event_by_id.return_value = remote
    sync = DraftPlanSyncService()
    sync.operations['op'] = operation
    workflow = SimpleNamespace(current_operation=operation, store=store, sync=sync,
        projector=DraftCalendarProjector(adapter))
    return workflow, adapter, (datetime(2026, 9, 7), datetime(2026, 9, 21))


def test_absence_does_not_authorize_cleanup(tmp_path):
    workflow, adapter, horizon = fixture(tmp_path)
    assert SharedPreparationWorkflow.retire_retained(workflow, lambda *_: None, horizon) == 0
    adapter._delete_event.assert_not_called()


def test_explicit_cleanup_is_checkpointed_and_not_restored_by_rollback(tmp_path):
    workflow, adapter, horizon = fixture(tmp_path)
    authorize = lambda *_: 'user_excluded'
    assert SharedPreparationWorkflow.retire_retained(workflow, authorize, horizon) == 1
    restored = workflow.store.load('op')
    assert not restored.retained_blocks
    assert restored.retired_blocks[0].id == 'prep'
    assert not restored.calendar_event_ids
    assert SharedPreparationWorkflow.retire_retained(workflow, authorize, horizon) == 0
    adapter._delete_event.assert_called_once_with('work', 'google-id', strict=True)
    workflow.projector.rollback(restored)
    workflow.sync.rollback('op')
    adapter._update_event.assert_not_called()
    assert 'prep' not in workflow.sync.current_blocks
    assert 'prep' not in workflow.sync.calendar_event_ids


def test_failed_delete_keeps_retryable_state(tmp_path):
    workflow, adapter, horizon = fixture(tmp_path)
    adapter._delete_event.return_value = False
    with pytest.raises(RuntimeError, match='не подтверждено'):
        SharedPreparationWorkflow.retire_retained(workflow, lambda *_: 'source_cancelled', horizon)
    assert workflow.store.load('op').calendar_event_ids == {'prep': 'google-id'}
    assert workflow.current_operation.retained_blocks


def test_verified_cancellation_also_retires_current_block(tmp_path):
    workflow, adapter, horizon = fixture(tmp_path)
    operation = workflow.current_operation
    operation.blocks = operation.retained_blocks
    operation.retained_blocks = []
    assert SharedPreparationWorkflow.retire_retained(
        workflow, lambda *_: 'source_cancelled', horizon) == 1
    saved = workflow.store.load('op')
    assert not saved.blocks
    assert saved.retired_blocks[0].source_event_id == '7:lesson'
    adapter._delete_event.assert_called_once_with('work', 'google-id', strict=True)


def test_completed_and_outside_horizon_are_protected(tmp_path):
    workflow, adapter, horizon = fixture(tmp_path)
    workflow.current_operation.completed_source_event_ids = ['7:lesson']
    authorize = lambda *_: 'source_cancelled'
    assert SharedPreparationWorkflow.retire_retained(workflow, authorize, horizon) == 0
    workflow.current_operation.completed_source_event_ids = []
    assert SharedPreparationWorkflow.retire_retained(workflow, authorize,
        (datetime(2026, 10, 1), datetime(2026, 10, 15))) == 0
    adapter._delete_event.assert_not_called()


def test_explicit_cleanup_preserves_manually_moved_owned_block(tmp_path):
    workflow, adapter, horizon = fixture(tmp_path)
    block = workflow.current_operation.retained_blocks[0]
    adapter.get_event_by_id.return_value['start']['dateTime'] = (
        block.start + timedelta(hours=1)).isoformat()

    assert SharedPreparationWorkflow.retire_retained(
        workflow, lambda *_: 'source_cancelled', horizon) == 0

    saved = workflow.store.load('op')
    assert block.id in saved.manual_calendar_overrides
    assert saved.retained_blocks == [block]
    adapter._delete_event.assert_not_called()


def test_explicit_cleanup_refuses_external_replacement(tmp_path):
    workflow, adapter, horizon = fixture(tmp_path)
    adapter.get_event_by_id.return_value['extendedProperties']['private'][
        'personal_os_operation_id'] = 'another-operation'

    with pytest.raises(RuntimeError, match='владелец'):
        SharedPreparationWorkflow.retire_retained(
            workflow, lambda *_: 'user_excluded', horizon)

    adapter._delete_event.assert_not_called()
