from datetime import datetime, timedelta, timezone

from models import PersonalEventState, PersonalUniversityEvent
from services.adaptive_preparation_service import (
    AdaptivePreparationService, CalendarRoute, DraftPlanSyncService,
    DraftCalendarProjector, DraftOperation, DraftPreparationBlock,
    UserPlanningProfile, calendar_route_for,
)
from services.draft_operation_store import DraftOperationStore
from services.weekly_plan_service import CommitmentType, FixedCommitment
from planning_engine import PlanningItem, PlanningItemType
from unittest.mock import Mock


def personal_event(session_type='practical'):
    start = datetime(2026, 9, 10, 12)
    return PersonalUniversityEvent(
        id='event-1', title='ОС (ПР)', description='', start_time=start,
        end_time=start + timedelta(hours=1), university_event_uid='source-1',
        state=PersonalEventState.CONFIRMED,
        metadata={'session_type': session_type},
    )


def test_saturday_preference_is_found_from_a_weekday():
    planner = AdaptivePreparationService()
    assert planner._preferred_start(
        datetime(2026, 9, 4, 12), datetime(2026, 9, 7, 6), 60,
    ) == datetime(2026, 9, 5, 10)


def test_saturday_preference_never_rounds_into_the_past():
    planner = AdaptivePreparationService()
    assert planner._preferred_start(
        datetime(2026, 9, 5, 10, 0, 30), datetime(2026, 9, 7, 6), 60,
    ) == datetime(2026, 9, 5, 10, 5)


def test_draft_uses_weekly_0600_window_and_user_durations():
    event = personal_event()
    draft = AdaptivePreparationService(UserPlanningProfile()).build_draft(
        [event], now=datetime(2026, 9, 1),
    )

    assert draft.blocks[0].minutes == 60
    assert datetime(2026, 9, 3, 6) <= draft.blocks[0].start
    assert draft.blocks[0].end <= datetime(2026, 9, 10, 6)
    assert draft.blocks[0].start.minute % 5 == 0


def test_same_course_sessions_on_one_date_have_separate_requirements():
    lecture = personal_event('lecture')
    lecture.metadata['course'] = 'Операционные системы'
    practical = personal_event('practical')
    practical.id = 'event-2'
    practical.title = 'ОС (ПР)'
    practical.start_time = datetime(2026, 9, 10, 14)
    practical.end_time = datetime(2026, 9, 10, 15)
    practical.metadata['course'] = 'Операционные системы'

    draft = AdaptivePreparationService().build_draft(
        [lecture, practical], now=datetime(2026, 9, 1),
    )

    assert len(draft.blocks) == 2
    assert sorted(block.minutes for block in draft.blocks) == [20, 60]
    assert any('к ЛК' in block.title for block in draft.blocks)
    assert any('к ПР' in block.title for block in draft.blocks)
    assert len({block.source_event_id for block in draft.blocks}) == 2


def test_future_lesson_after_the_academic_day_anchor_gets_a_manual_conflict_marker():
    event = personal_event()
    event.start_time = datetime(2026, 9, 10, 12)
    event.end_time = datetime(2026, 9, 10, 13)
    service = AdaptivePreparationService()

    expired = service.build_draft([event], now=datetime(2026, 9, 10, 6))

    assert len(expired.blocks) == 1
    block = expired.blocks[0]
    assert block.manual_conflict
    assert block.start == datetime(2026, 9, 10, 6)
    assert block.start.minute % 5 == 0
    assert expired.no_slot_reasons[event.id] == 'допустимое окно подготовки уже прошло'
    assert 'Конфликт — перенести вручную' in block.reason


def test_study_preparation_id_does_not_change_with_conflict_status():
    start = datetime(2026, 9, 10, 10)
    assert AdaptivePreparationService._block_id('event', start, start + timedelta(minutes=20)) == (
        AdaptivePreparationService._block_id(
            'event', start, start + timedelta(minutes=20), manual_conflict=True,
        )
    )


def test_projection_does_not_treat_string_false_as_manual_conflict():
    start = datetime(2026, 9, 12, 10)
    block = DraftPreparationBlock('prep', 'source', 'Подготовка', start,
                                  start + timedelta(minutes=20), 20, 'reason',
                                  manual_conflict='false')
    event = DraftCalendarProjector._event_data(block, DraftOperation('operation', [block]))

    assert 'Manual conflict: true' not in event.description
    assert 'Конфликт — перенести вручную' not in event.summary


def test_past_university_lesson_does_not_create_a_manual_conflict_marker():
    event = personal_event()
    event.start_time = datetime(2026, 9, 10, 12)
    event.end_time = datetime(2026, 9, 10, 13)

    draft = AdaptivePreparationService().build_draft([event], now=datetime(2026, 9, 10, 13))

    assert not draft.blocks
    assert draft.no_slot_reasons[event.id] == 'допустимое окно подготовки уже прошло'


def test_too_short_remaining_time_does_not_create_a_post_lesson_conflict_marker():
    event = personal_event()
    event.start_time = datetime(2026, 9, 10, 10)
    event.end_time = datetime(2026, 9, 10, 11)

    draft = AdaptivePreparationService().build_draft([event], now=datetime(2026, 9, 10, 9, 30))

    assert draft.blocks == []
    assert draft.no_slot_reasons[event.id] == 'допустимое окно подготовки уже прошло'
    assert any('не помещается до занятия' in explanation for explanation in draft.explanations)


def test_draft_covers_the_full_following_calendar_week_only():
    this_week = personal_event()
    this_week.start_time = datetime(2026, 9, 13, 12)
    this_week.end_time = datetime(2026, 9, 13, 13)
    next_after_horizon = personal_event()
    next_after_horizon.id = 'event-after-horizon'
    next_after_horizon.start_time = datetime(2026, 9, 14, 12)
    next_after_horizon.end_time = datetime(2026, 9, 14, 13)

    draft = AdaptivePreparationService().build_draft(
        [this_week, next_after_horizon], now=datetime(2026, 9, 2, 10),
    )

    assert len(draft.blocks) == 1
    assert datetime(2026, 9, 6, 6) <= draft.blocks[0].start
    assert draft.blocks[0].end <= datetime(2026, 9, 13, 6)


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
    assert calendar_route_for('preparation') == (CalendarRoute.STUDY, 'PREPARATION')
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


def test_calendar_projector_preserves_manually_moved_owned_block():
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'study'
    adapter.event_exists_by_uid.return_value = 'google-draft'
    start = datetime(2026, 9, 12, 10)
    block = DraftPreparationBlock(
        'prep:manual', 'source', 'Подготовка', start, start + timedelta(minutes=20),
        20, 'draft',
    )
    adapter.get_event_by_uid.return_value = {
        'id': 'google-draft',
        'start': {'dateTime': (start + timedelta(hours=2)).isoformat()},
        'end': {'dateTime': (start + timedelta(hours=2, minutes=20)).isoformat()},
    }
    operation = DraftOperation('operation', [block])
    operation.calendar_event_ids[block.id] = 'google-draft'

    DraftCalendarProjector(adapter).stage(operation)

    assert operation.manual_calendar_overrides[block.id]['start'] == (start + timedelta(hours=2)).isoformat()
    adapter._update_event.assert_not_called()


def test_calendar_projector_does_not_restore_manually_deleted_owned_block():
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'study'
    adapter.event_exists_by_uid.return_value = None
    start = datetime(2026, 9, 12, 10)
    block = DraftPreparationBlock('prep:deleted', 'source', 'Подготовка', start,
                                  start + timedelta(minutes=20), 20, 'draft')
    operation = DraftOperation('operation', [block])
    operation.calendar_event_ids[block.id] = 'google-draft'

    DraftCalendarProjector(adapter).stage(operation)

    assert operation.manually_deleted_block_ids == [block.id]
    adapter._insert_event.assert_not_called()


def test_calendar_projector_captures_manual_actions_with_strict_read():
    adapter = Mock()
    start = datetime(2026, 9, 12, 10)
    moved = DraftPreparationBlock('prep:moved', 'moved', 'Подготовка', start,
                                  start + timedelta(minutes=20), 20, 'draft')
    deleted = DraftPreparationBlock('prep:deleted', 'deleted', 'Подготовка', start,
                                    start + timedelta(minutes=20), 20, 'draft')
    operation = DraftOperation('operation', [moved, deleted], calendar_id='study')
    operation.calendar_event_ids = {moved.id: 'moved-event', deleted.id: 'deleted-event'}
    adapter.get_event_by_uid.side_effect = [
        {'id': 'moved-event',
         'start': {'dateTime': (start + timedelta(hours=1)).isoformat()},
         'end': {'dateTime': (start + timedelta(hours=1, minutes=20)).isoformat()}},
        None,
    ]

    changed = DraftCalendarProjector(adapter).capture_manual_actions(operation)

    assert changed
    assert moved.id in operation.manual_calendar_overrides
    assert operation.manually_deleted_block_ids == [deleted.id]
    assert all(call.kwargs == {'strict': True} for call in adapter.get_event_by_uid.call_args_list)


def test_duplicate_cleanup_only_selects_system_owned_draft_duplicates():
    operation = AdaptivePreparationService().build_draft(
        [personal_event()], now=datetime(2026, 9, 1),
    )
    operation.calendar_event_ids[operation.blocks[0].id] = 'current-draft'
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'study'
    adapter.list_events_in_calendar.return_value = [
        {
            'id': 'old-draft', 'summary': operation.blocks[0].title + ' [черновик]',
            'updated': '2026-09-01T10:00:00Z',
            'description': 'AI Calendar Block: old\nStatus: draft',
        },
        {
            'id': 'current-draft', 'summary': operation.blocks[0].title + ' [черновик]',
            'updated': '2026-09-01T11:00:00Z',
            'description': 'AI Calendar Block: current\nStatus: draft',
        },
        {
            'id': 'user-event', 'summary': operation.blocks[0].title,
            'description': 'личная заметка',
        },
    ]
    projector = DraftCalendarProjector(adapter)

    assert projector.duplicate_draft_event_ids(operation) == ['old-draft']
    assert projector.delete_duplicate_drafts(operation) == 1
    adapter._delete_event.assert_called_once_with('study', 'old-draft')


def test_reset_selects_only_system_owned_drafts_not_confirmed_or_personal_events():
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'study'
    adapter.list_events_in_calendar.return_value = [
        {'id': 'draft', 'description': 'AI Calendar Block: p\nStatus: draft'},
        {'id': 'confirmed', 'description': 'AI Calendar Block: p\nStatus: confirmed'},
        {'id': 'personal', 'description': 'личная встреча'},
    ]
    projector = DraftCalendarProjector(adapter)

    assert projector.delete_all_system_drafts() == 1
    adapter._delete_event.assert_called_once_with('study', 'draft')


def test_operation_store_persists_version_hash_snapshot_and_calendar_ids(tmp_path):
    operation = AdaptivePreparationService().build_draft(
        [personal_event()], now=datetime(2026, 9, 1),
    )
    operation.calendar_id = 'work'
    operation.calendar_event_ids[operation.blocks[0].id] = 'google-draft'
    operation.created_calendar_block_ids.append(operation.blocks[0].id)
    operation.manual_calendar_overrides[operation.blocks[0].id] = {
        'start': '2026-09-02T10:00:00', 'end': '2026-09-02T10:20:00',
    }
    operation.manually_deleted_block_ids.append('prep:deleted')
    store = DraftOperationStore(tmp_path / 'user-1' / 'drafts.json')

    store.save(operation)
    restored = store.load(operation.id)

    assert restored.version == 1
    assert restored.content_hash == operation.content_hash
    assert restored.blocks == operation.blocks
    assert restored.calendar_event_ids == operation.calendar_event_ids
    assert restored.manual_calendar_overrides == operation.manual_calendar_overrides
    assert restored.manually_deleted_block_ids == operation.manually_deleted_block_ids


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
    window_start = datetime(2026, 9, 3, 6)
    window_end = datetime(2026, 9, 10, 6)
    assert block.start >= window_start
    assert block.end <= window_end
    assert block.end <= recovery.start or block.start >= recovery.end
    assert block.end <= sleep.start or block.start >= sleep.end


def test_university_day_reserves_commute_both_ways_and_recovery_once():
    service = AdaptivePreparationService()
    first = personal_event('lecture')
    first.id = 'first'
    first.start_time = datetime(2026, 9, 10, 10)
    first.end_time = datetime(2026, 9, 10, 11)
    second = personal_event('lab')
    second.id = 'second'
    second.start_time = datetime(2026, 9, 10, 14)
    second.end_time = datetime(2026, 9, 10, 16)

    commitments = service._university_commitments(
        [first, second], datetime(2026, 9, 9), datetime(2026, 9, 11),
    )
    spans = {commitment.title: (commitment.start, commitment.end) for commitment in commitments}

    assert spans['Дорога в университет'] == (
        datetime(2026, 9, 10, 9), datetime(2026, 9, 10, 10),
    )
    assert spans['Дорога из университета'] == (
        datetime(2026, 9, 10, 16), datetime(2026, 9, 10, 17),
    )
    assert spans['Восстановление после университета'] == (
        datetime(2026, 9, 10, 17), datetime(2026, 9, 10, 18),
    )
    assert spans['Университетский день'] == (
        datetime(2026, 9, 10, 10), datetime(2026, 9, 10, 16),
    )
    assert len([item for item in commitments if item.commitment_type == CommitmentType.UNIVERSITY]) == 1


def test_final_invariant_rejects_a_preparation_in_a_gap_between_classes():
    first = personal_event('lecture')
    first.start_time = datetime(2026, 9, 10, 10)
    first.end_time = datetime(2026, 9, 10, 11)
    last = personal_event('lab')
    last.id = 'last'
    last.start_time = datetime(2026, 9, 10, 15)
    last.end_time = datetime(2026, 9, 10, 16)

    assert AdaptivePreparationService._overlaps_university_day(
        datetime(2026, 9, 10, 12), datetime(2026, 9, 10, 13), [first, last],
    )
    assert not AdaptivePreparationService._overlaps_university_day(
        datetime(2026, 9, 10, 16), datetime(2026, 9, 10, 17), [first, last],
    )


def test_non_attended_tpu_class_still_reserves_the_whole_university_day():
    """Attendance controls requirements, never availability on campus."""
    first = personal_event('lecture')
    first.state = PersonalEventState.EXPECTED
    first.start_time = datetime(2026, 9, 10, 10)
    first.end_time = datetime(2026, 9, 10, 11)
    last = personal_event('lab')
    last.id = 'excluded-lab'
    last.state = PersonalEventState.EXPECTED
    last.start_time = datetime(2026, 9, 10, 15)
    last.end_time = datetime(2026, 9, 10, 16)

    commitments = AdaptivePreparationService()._university_commitments(
        [first, last], datetime(2026, 9, 9), datetime(2026, 9, 11),
    )
    campus_day = next(item for item in commitments if item.title == 'Университетский день')

    assert (campus_day.start, campus_day.end) == (
        datetime(2026, 9, 10, 10), datetime(2026, 9, 10, 16),
    )
    assert AdaptivePreparationService._overlaps_university_day(
        datetime(2026, 9, 10, 12), datetime(2026, 9, 10, 13), [first, last],
    )


def test_higher_priority_lab_displaces_project_task_with_an_explanation():
    event = personal_event('lab')
    event.start_time = datetime(2026, 9, 10, 12)
    event.end_time = datetime(2026, 9, 10, 13)
    now = datetime(2026, 9, 8, 18)
    deadline = datetime(2026, 9, 10, 6)
    # Only one hour remains available in the near-term part of the window.
    fixed = FixedCommitment(
        id='busy', title='Fixed', start=now + timedelta(hours=1), end=deadline,
    )
    project = PlanningItem(
        id='project', title='Проектный дедлайн', description='',
        item_type=PlanningItemType.PROJECT_TASK, duration_minutes=60,
        earliest_start=now, latest_end=deadline, priority=1,
    )

    draft = AdaptivePreparationService().build_draft(
        [event], now=now, fixed_commitments=[fixed], flexible_items=[project],
    )

    assert len(draft.blocks) == 1
    assert any('Проектный дедлайн' in reason and 'перенесено' in reason
               for reason in draft.explanations)


def test_timezone_aware_ics_events_and_naive_sleep_share_one_planning_timeline():
    event = personal_event()
    event.start_time = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    event.end_time = datetime(2026, 9, 10, 13, tzinfo=timezone.utc)
    sleep = FixedCommitment(
        id='sleep', title='Сон', start=datetime(2026, 9, 8, 23),
        end=datetime(2026, 9, 9, 7), commitment_type=CommitmentType.SLEEP,
    )

    draft = AdaptivePreparationService().build_draft(
        [event], now=datetime(2026, 9, 8, 8), fixed_commitments=[sleep],
    )

    assert draft.blocks
    assert draft.blocks[0].start.tzinfo is not None


def test_preparation_blocks_keep_a_fifteen_minute_break():
    first = personal_event('practical')
    first.start_time = datetime(2026, 9, 9, 12)
    first.end_time = datetime(2026, 9, 9, 13)
    second = personal_event('lab')
    second.id = 'event-2'
    second.start_time = datetime(2026, 9, 9, 14)
    second.end_time = datetime(2026, 9, 9, 15)

    draft = AdaptivePreparationService().build_draft(
        [first, second], now=datetime(2026, 9, 2, 10),
    )

    blocks = sorted(draft.blocks, key=lambda block: block.start)
    assert len(blocks) == 2
    assert blocks[1].start - blocks[0].end >= timedelta(minutes=15)


def test_sunday_accepts_only_urgent_preparation_after_ten():
    monday = personal_event('practical')
    monday.start_time = datetime(2026, 9, 7, 12)
    monday.end_time = datetime(2026, 9, 7, 13)
    later = personal_event('practical')
    later.id = 'later'
    later.start_time = datetime(2026, 9, 9, 12)
    later.end_time = datetime(2026, 9, 9, 13)

    draft = AdaptivePreparationService().build_draft(
        [monday, later], now=datetime(2026, 9, 6, 9),
    )

    monday_block = next(block for block in draft.blocks if block.source_event_id == 'event-1')
    assert monday_block.start.weekday() != 6 or monday_block.start.hour >= 10
