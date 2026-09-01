from datetime import datetime, timedelta

from models import PersonalEventState, PersonalUniversityEvent
from services.adaptive_preparation_service import (
    AdaptivePreparationService, CalendarRoute, DraftPlanSyncService,
    DraftCalendarProjector, UserPlanningProfile, calendar_route_for,
)
from services.draft_operation_store import DraftOperationStore
from services.weekly_plan_service import CommitmentType, FixedCommitment
from unittest.mock import Mock


def personal_event(session_type='practical'):
    start = datetime(2026, 9, 10, 12)
    return PersonalUniversityEvent(
        id='event-1', title='ОС (ПР)', description='', start_time=start,
        end_time=start + timedelta(hours=1), university_event_uid='source-1',
        state=PersonalEventState.CONFIRMED,
        metadata={'session_type': session_type},
    )


def test_draft_uses_36_hour_deadline_and_user_durations():
    event = personal_event()
    draft = AdaptivePreparationService(UserPlanningProfile()).build_draft(
        [event], now=datetime(2026, 9, 1),
    )

    assert draft.blocks[0].minutes == 60
    assert draft.blocks[0].end == event.start_time - timedelta(hours=36)


def test_excluded_or_cancelled_events_do_not_create_preparation():
    event = personal_event()
    event.state = PersonalEventState.CANCELLED

    assert not AdaptivePreparationService().build_draft([event]).blocks


def test_draft_transaction_confirms_and_rolls_back_idempotently():
    draft = AdaptivePreparationService().build_draft(
        [personal_event()], now=datetime(2026, 9, 1),
    )
    sync = DraftPlanSyncService()
    sync.stage(draft)
    assert sync.current_blocks
    sync.confirm(draft.id)
    assert sync.current_blocks[draft.blocks[0].id].status == 'confirmed'
    sync.rollback(draft.id)
    assert not sync.current_blocks
    assert sync.rollback(draft.id).status == 'rolled_back'


def test_calendar_routing_keeps_study_work_personal_and_sleep_separate():
    assert calendar_route_for('lab') == (CalendarRoute.STUDY, 'ЛБ')
    assert calendar_route_for('preparation') == (CalendarRoute.WORK, 'PREPARATION')
    assert calendar_route_for('reading') == (CalendarRoute.PERSONAL, 'READING')
    assert calendar_route_for('sleep') == (CalendarRoute.SLEEP, 'SLEEP')


def test_calendar_draft_projector_is_idempotent_and_rolls_back_only_owned_event():
    operation = AdaptivePreparationService().build_draft(
        [personal_event()], now=datetime(2026, 9, 1),
    )
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'work'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'google-draft'
    projector = DraftCalendarProjector(adapter)

    projector.stage(operation)
    projector.rollback(operation)

    assert adapter._insert_event.call_count == 1
    assert adapter._insert_event.call_args.args[1] == 'work'
    adapter._delete_event.assert_called_once_with('work', 'google-draft')


def test_calendar_projector_restores_moved_system_block_without_creating_another():
    old_operation = AdaptivePreparationService().build_draft(
        [personal_event()], now=datetime(2026, 9, 1),
    )
    old_block = old_operation.blocks[0]
    moved = personal_event()
    moved.start_time += timedelta(days=1)
    moved.end_time += timedelta(days=1)
    operation = AdaptivePreparationService().build_draft([moved], now=datetime(2026, 9, 1))
    operation.previous_blocks = [old_block]
    operation.previous_calendar_event_ids = {old_block.id: 'google-old'}
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'work'
    adapter.event_exists_by_uid.return_value = None
    adapter._update_event.return_value = 'google-old'

    projector = DraftCalendarProjector(adapter)
    projector.stage(operation)
    projector.rollback(operation)

    adapter._insert_event.assert_not_called()
    assert adapter._update_event.call_count == 2
    adapter._delete_event.assert_not_called()


def test_operation_store_persists_version_hash_snapshot_and_calendar_ids(tmp_path):
    operation = AdaptivePreparationService().build_draft(
        [personal_event()], now=datetime(2026, 9, 1),
    )
    operation.calendar_id = 'work'
    operation.calendar_event_ids[operation.blocks[0].id] = 'google-draft'
    operation.created_calendar_block_ids.append(operation.blocks[0].id)
    store = DraftOperationStore(tmp_path / 'user-1' / 'drafts.json')

    store.save(operation)
    restored = store.load(operation.id)

    assert restored.version == 1
    assert restored.content_hash == operation.content_hash
    assert restored.blocks == operation.blocks
    assert restored.calendar_event_ids == operation.calendar_event_ids


def test_planning_engine_places_one_block_around_sleep_and_fixed_commitments():
    event = personal_event()
    event.start_time = datetime(2026, 9, 10, 12)
    event.end_time = datetime(2026, 9, 10, 13)
    sleep = FixedCommitment(
        id='sleep', title='Сон', start=datetime(2026, 9, 8, 22),
        end=datetime(2026, 9, 9, 7), commitment_type=CommitmentType.SLEEP,
    )
    recovery = FixedCommitment(
        id='recovery', title='Восстановление', start=datetime(2026, 9, 8, 18),
        end=datetime(2026, 9, 8, 20), commitment_type=CommitmentType.RECOVERY,
    )

    draft = AdaptivePreparationService().build_draft(
        [event], now=datetime(2026, 9, 8, 8),
        fixed_commitments=[sleep, recovery],
    )

    assert len(draft.blocks) == 1
    block = draft.blocks[0]
    assert block.end <= event.start_time - timedelta(hours=36)
    assert block.end <= recovery.start or block.start >= recovery.end
    assert block.end <= sleep.start or block.start >= sleep.end
