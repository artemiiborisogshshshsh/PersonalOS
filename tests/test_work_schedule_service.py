from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo
from hashlib import sha256
import pytest

from models import PersonalEventState
from services.alfacrm_schedule_source import AlfaCRMLesson
from services.work_schedule_service import (
    WorkPlanningStateStore, WorkPreparationPlanner, WorkScheduleService, WorkPreparationWorkflow,
)
from services.draft_operation_store import DraftOperationStore
from services.adaptive_preparation_service import (
    CalendarRoute, DraftCalendarProjector, DraftOperation, DraftPreparationBlock,
)


def lesson(identifier='7:11', start=datetime(2026, 9, 10, 18, 0)):
    return AlfaCRMLesson(
        id=identifier, title='Математика', subject='Математика', group='Группа А',
        students=('Анна', 'Борис'), start=start, end=start + timedelta(minutes=90),
    )


def service(tmp_path: Path):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'work-calendar'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'google-work-id'
    return WorkScheduleService(adapter, WorkPlanningStateStore(tmp_path / 'work.json')), adapter


def test_verified_cancellation_survives_restart_and_keeps_feedback(tmp_path):
    instance, adapter = service(tmp_path)
    current = lesson()
    instance.sync([current])
    instance.record_feedback(current.id, 'done', 'Домашнее задание')
    horizon = (datetime(2026, 9, 7), datetime(2026, 9, 21))
    result = instance.sync([], verified_horizon=horizon)
    assert result['deleted_source_ids'] == [current.id]
    reloaded = instance.state_store.load()
    assert current.id in reloaded.cancelled_lessons
    assert reloaded.feedback[current.id]['homework'] == 'Домашнее задание'
    assert current.id not in reloaded.lessons
    instance.sync([current], verified_horizon=horizon)
    assert current.id not in instance.state_store.load().cancelled_lessons


def test_unverified_absence_does_not_mark_cancellation(tmp_path):
    instance, adapter = service(tmp_path)
    current = lesson()
    instance.sync([current])
    adapter.reset_mock()
    instance.sync([])
    assert current.id in instance.state.lessons
    assert not instance.state.cancelled_lessons
    adapter._delete_event.assert_not_called()


def test_failed_cancellation_delete_keeps_lesson_and_verified_evidence(tmp_path):
    instance, adapter = service(tmp_path)
    instance.sync([lesson()])
    adapter._delete_event.return_value = False
    with pytest.raises(RuntimeError, match='deletion'):
        instance.sync([], verified_horizon=(datetime(2026, 9, 7), datetime(2026, 9, 21)))
    saved = instance.state_store.load()
    assert lesson().id in saved.lessons
    assert lesson().id in saved.cancelled_lessons


def test_completed_work_preparation_is_not_recreated_or_deleted(tmp_path):
    instance, adapter = service(tmp_path)
    current = lesson()
    instance.sync([current])
    prep_id = 'work-prep:' + sha256(current.id.encode()).hexdigest()[:20]
    instance.record_preparation_feedback(prep_id, 'done', 20, 3)
    candidate = WorkPreparationPlanner(instance).build_draft(
        [current], [], now=datetime(2026, 9, 4, 10))
    assert not candidate.blocks
    adapter.event_exists_by_uid.return_value = 'completed-prep'
    adapter.reset_mock()
    instance.sync([], verified_horizon=(datetime(2026, 9, 7), datetime(2026, 9, 21)))
    assert all(call.args[1] != 'completed-prep' for call in adapter._delete_event.call_args_list)


def test_work_preparation_id_and_feedback_survive_conflict_classification(tmp_path):
    instance, _ = service(tmp_path)
    current = lesson()
    ordinary = WorkPreparationPlanner._block_id(current.id)
    conflict = WorkPreparationPlanner._block_id(current.id, manual_conflict=True)

    assert conflict == ordinary
    instance.record_preparation_feedback(conflict, 'done', 20, 3)
    operation = WorkPreparationPlanner(instance).build_draft(
        [current], [], now=datetime(2026, 9, 4, 10),
    )
    assert operation.blocks == []


def test_work_preparation_does_not_create_a_marker_after_its_lesson(tmp_path):
    instance, _ = service(tmp_path)
    current = lesson(start=datetime(2026, 9, 10, 18))
    instance.set_mode(current, 'online')

    operation = WorkPreparationPlanner(instance).build_draft(
        [current], [], now=datetime(2026, 9, 10, 17, 50),
    )

    assert operation.blocks == []
    assert current.id in operation.no_slot_reasons
    assert any('не помещается до рабочей пары' in text for text in operation.explanations)


def test_work_replan_failure_keeps_previous_projection(tmp_path):
    instance, adapter = service(tmp_path)
    workflow = WorkPreparationWorkflow(
        WorkPreparationPlanner(instance), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'draft.json'), lambda: [lesson()], lambda: [],
        now_provider=lambda: datetime(2026, 9, 4, 10),
    )
    workflow.preview()
    workflow.stage()
    previous = workflow.current_operation
    workflow.commitments_provider = Mock(side_effect=RuntimeError('Calendar unavailable'))
    with pytest.raises(RuntimeError, match='Calendar unavailable'):
        workflow.replan()
    assert workflow.current_operation is previous
    assert previous.status == 'draft'
    adapter._delete_event.assert_not_called()


def test_identical_work_replan_keeps_operation_and_remote_events(tmp_path):
    instance, adapter = service(tmp_path)
    workflow = WorkPreparationWorkflow(
        WorkPreparationPlanner(instance), DraftCalendarProjector(adapter),
        DraftOperationStore(tmp_path / 'draft.json'), lambda: [lesson()], lambda: [],
        now_provider=lambda: datetime(2026, 9, 4, 10),
    )
    workflow.preview()
    workflow.stage()
    previous = workflow.current_operation.id
    adapter.reset_mock()
    assert 'План не изменился' in workflow.replan()
    assert workflow.current_operation.id == previous
    adapter._delete_event.assert_not_called()
    adapter._insert_event.assert_not_called()
    adapter._update_event.assert_not_called()


def test_partial_publication_resumes_after_restart_without_duplicate(tmp_path):
    instance, adapter = service(tmp_path)
    remote = {}
    attempts = []
    fail_second = True

    def insert(event, calendar_id):
        attempts.append(event.uid)
        if len(attempts) == 2 and fail_second:
            return None
        remote[event.uid] = 'id-' + event.uid
        return remote[event.uid]

    adapter._insert_event.side_effect = insert
    adapter.event_exists_by_uid.side_effect = lambda calendar_id, uid: remote.get(uid)
    adapter._update_event.side_effect = lambda calendar_id, event_id, event: event_id
    store = DraftOperationStore(tmp_path / 'draft.json')

    def workflow():
        return WorkPreparationWorkflow(
            WorkPreparationPlanner(instance), DraftCalendarProjector(adapter), store,
            lambda: [lesson('7:11'), lesson('7:12', datetime(2026, 9, 11, 18))],
            lambda: [], now_provider=lambda: datetime(2026, 9, 4, 10),
        )

    first = workflow()
    first.preview()
    operation_id = first.current_operation.id
    with pytest.raises(RuntimeError, match='запись подготовки'):
        first.stage()
    saved = store.load(operation_id)
    assert saved.projection_pending
    assert len(saved.calendar_event_ids) == 1
    fail_second = False
    restarted = workflow()
    restarted.replan()
    assert restarted.current_operation.id == operation_id
    assert not store.load(operation_id).projection_pending
    assert len(remote) == 2
    assert attempts.count(saved.blocks[0].id) == 1
    adapter._delete_event.assert_not_called()


def test_failed_update_does_not_insert_a_replacement(tmp_path):
    instance, adapter = service(tmp_path)
    operation = WorkPreparationPlanner(instance).build_draft(
        [lesson()], [], now=datetime(2026, 9, 4, 10),
    )
    adapter.event_exists_by_uid.return_value = 'existing'
    adapter._update_event.return_value = None
    with pytest.raises(RuntimeError, match='запись подготовки'):
        DraftCalendarProjector(adapter).stage(operation)
    adapter._insert_event.assert_not_called()


def test_work_sync_is_idempotent_and_uses_work_calendar(tmp_path):
    instance, adapter = service(tmp_path)
    result = instance.sync([lesson()])
    same = instance.sync([lesson()])

    assert result['created'] == 1
    assert same['changed'] is False
    adapter._insert_event.assert_called_once()
    event = adapter._insert_event.call_args.args[0]
    assert event.summary == 'Работа: Математика — Группа А'
    assert event.event_type == 'WORK_LESSON'
    assert 'Ученики: Анна, Борис' in event.description


def test_mode_is_persisted_by_subject_group_and_students(tmp_path):
    instance, _ = service(tmp_path)
    first = lesson()
    instance.set_mode(first, 'online')
    reloaded = WorkScheduleService(Mock(), WorkPlanningStateStore(tmp_path / 'work.json'))

    assert reloaded.mode_for(first) == 'online'
    assert reloaded.mode_for(lesson('7:12')) == 'online'


def test_feedback_updates_only_the_owned_work_projection(tmp_path):
    instance, adapter = service(tmp_path)
    current = lesson()
    instance.sync([current])
    adapter.event_exists_by_uid.return_value = 'google-work-id'
    adapter._update_event.return_value = 'google-work-id'
    instance.record_feedback(current.id, 'done', 'лист 3', '12.09', 'хорошо')
    instance.sync([current])

    event = adapter._update_event.call_args.args[2]
    assert 'ДЗ: лист 3' in event.description
    assert 'Дедлайн: 12.09' in event.description


def test_work_preparation_defaults_unknown_mode_to_offline(tmp_path):
    instance, _ = service(tmp_path)
    operation = WorkPreparationPlanner(instance).build_draft(
        [lesson()], [], now=datetime(2026, 9, 8, 10, 0),
    )

    assert len(operation.blocks) == 1
    assert instance.mode_for(lesson()) == 'offline'


def test_work_preparation_never_uses_university_day_or_transition(tmp_path):
    instance, _ = service(tmp_path)
    current = lesson(start=datetime(2026, 9, 10, 18, 0))
    instance.set_mode(current, 'online')
    university = SimpleNamespace(
        state=PersonalEventState.CONFIRMED,
        start_time=datetime(2026, 9, 10, 9, 0),
        end_time=datetime(2026, 9, 10, 16, 0),
    )
    operation = WorkPreparationPlanner(instance).build_draft(
        [current], [university], now=datetime(2026, 9, 9, 10, 0),
    )

    assert len(operation.blocks) == 1
    block = operation.blocks[0]
    assert block.calendar.value == 'Работа'
    assert not (block.start < university.end_time and block.end > university.start_time)
    assert not (block.start < current.end and block.end > current.start)
    assert block.end <= datetime(2026, 9, 10, 16, 40) or block.start < datetime(2026, 9, 10, 9, 0)


def test_offline_after_university_requires_route_before_preparation(tmp_path):
    instance, _ = service(tmp_path)
    current = lesson()
    instance.set_mode(current, 'offline')
    university = SimpleNamespace(
        state=PersonalEventState.CONFIRMED,
        start_time=datetime(2026, 9, 10, 9, 0),
        end_time=datetime(2026, 9, 10, 16, 0),
    )
    operation = WorkPreparationPlanner(instance).build_draft(
        [current], [university], now=datetime(2026, 9, 8, 10, 0),
    )

    assert operation.blocks == []
    assert 'маршруте' in operation.explanations[0]


def test_work_draft_is_projected_only_to_work_calendar():
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'work-calendar'
    adapter.event_exists_by_uid.return_value = None
    adapter._insert_event.return_value = 'work-prep-event'
    block = DraftPreparationBlock(
        id='work-prep:1', source_event_id='7:11', title='Подготовка к работе',
        start=datetime(2026, 9, 8, 10), end=datetime(2026, 9, 8, 10, 20),
        minutes=20, reason='test', calendar=CalendarRoute.WORK,
    )
    operation = DraftOperation(id='operation', blocks=[block], scope='work-preparation')

    DraftCalendarProjector(adapter).stage(operation)

    adapter._get_or_create_calendar.assert_called_once_with('Работа')
    assert adapter._insert_event.call_args.args[1] == 'work-calendar'
    assert adapter._insert_event.call_args.args[0].event_type == 'WORK_PREPARATION'


def test_overlap_audit_reports_but_preserves_owned_work_preparation(tmp_path):
    instance, adapter = service(tmp_path)
    current = lesson()
    adapter.list_events_in_calendar.return_value = [
        {
            'id': 'bad-work-prep', 'summary': 'Подготовка к работе',
            'description': 'AI Calendar Block: work-prep\nAI Calendar Source: 7:11\nStatus: draft',
            'start': {'dateTime': '2026-09-10T18:00:00'},
            'end': {'dateTime': '2026-09-10T18:20:00'},
        },
        {
            'id': 'user-event', 'summary': 'Подготовка к работе', 'description': 'пользовательская заметка',
            'start': {'dateTime': '2026-09-10T18:00:00'},
            'end': {'dateTime': '2026-09-10T18:20:00'},
        },
    ]
    adapter._delete_event.return_value = True

    assert instance.remove_overlapping_work_preparations([current]) == 1
    adapter._delete_event.assert_not_called()


def test_work_planner_normalises_naive_profile_intervals_with_alfacrm_timezone(tmp_path):
    instance, _ = service(tmp_path)
    timezone = ZoneInfo('Asia/Tomsk')
    current = lesson(start=datetime(2026, 9, 10, 18, tzinfo=timezone))
    fixed = SimpleNamespace(
        id='sleep', title='Сон', start=datetime(2026, 9, 9, 23),
        end=datetime(2026, 9, 10, 7), commitment_type=None,
    )
    # Real profile objects are FixedCommitment dataclasses; use the same type
    # here to exercise conversion without a timezone on saved profile data.
    from services.weekly_plan_service import CommitmentType, FixedCommitment
    fixed = FixedCommitment('sleep', 'Сон', fixed.start, fixed.end, CommitmentType.SLEEP)

    operation = WorkPreparationPlanner(instance).build_draft(
        [current], [], [fixed], now=datetime(2026, 9, 8, 10),
    )

    assert operation.blocks


def test_each_work_preparation_prefers_its_own_latest_slot_over_an_older_bundle(tmp_path):
    instance, _ = service(tmp_path)
    first = lesson('7:11', datetime(2026, 9, 10, 18))
    second = lesson('7:12', datetime(2026, 9, 11, 18))

    operation = WorkPreparationPlanner(instance, break_minutes=0).build_draft(
        [first, second], [], now=datetime(2026, 9, 8, 10),
    )

    assert len(operation.blocks) == 2
    first_block, second_block = operation.blocks
    assert first_block.source_event_id == first.id
    assert second_block.source_event_id == second.id
    assert first_block.start.date() == first.start.date()
    assert second_block.start.date() == second.start.date()
    assert first_block.end <= first.start
    assert second_block.end <= second.start


def test_next_week_work_preparations_are_packed_on_previous_saturday(tmp_path):
    instance, _ = service(tmp_path)
    first = lesson('7:11', datetime(2026, 9, 7, 18))
    second = lesson('7:12', datetime(2026, 9, 8, 18))

    operation = WorkPreparationPlanner(instance, break_minutes=0).build_draft(
        [first, second], [], now=datetime(2026, 9, 4, 10),
    )

    assert [(block.start.weekday(), block.start.hour, block.start.minute)
            for block in operation.blocks] == [(5, 10, 0), (5, 10, 20)]
    assert all('Субботний блок' in block.reason for block in operation.blocks)


def test_work_preparation_series_requires_profile_break_after_two_hours(tmp_path):
    instance, _ = service(tmp_path)
    lessons = [
        lesson(f'7:{index}', datetime(2026, 9, 7, 18))
        for index in range(7)
    ]

    operation = WorkPreparationPlanner(
        instance, max_continuous_minutes=120, series_break_minutes=15,
    ).build_draft(lessons, [], now=datetime(2026, 9, 4, 10))

    assert [(block.start.hour, block.start.minute) for block in operation.blocks] == [
        (10, 0), (10, 20), (10, 40), (11, 0), (11, 20), (11, 40), (12, 15),
    ]


def test_work_saturday_session_starts_after_university_preparations(tmp_path):
    instance, _ = service(tmp_path)
    first = lesson('7:11', datetime(2026, 9, 7, 18))
    second = lesson('7:12', datetime(2026, 9, 8, 18))
    from services.weekly_plan_service import CommitmentType, FixedCommitment
    university_session = FixedCommitment(
        'university-preparations', 'Подготовки к университету',
        datetime(2026, 9, 5, 10), datetime(2026, 9, 5, 14),
        CommitmentType.OTHER,
    )

    operation = WorkPreparationPlanner(instance, break_minutes=0).build_draft(
        [first, second], [], [university_session], now=datetime(2026, 9, 4, 10),
    )

    assert [(block.start.hour, block.start.minute) for block in operation.blocks] == [
        (14, 0), (14, 20),
    ]


def test_saturday_work_does_not_fill_break_between_study_blocks(tmp_path):
    from services.weekly_plan_service import CommitmentType, FixedCommitment
    instance, _ = service(tmp_path)
    study = [FixedCommitment(
        f'study-{hour}', 'Учебная подготовка', datetime(2026, 9, 5, hour),
        datetime(2026, 9, 5, hour + 1), CommitmentType.OTHER,
        metadata={'preparation_scope': 'university-preparation'},
    ) for hour in (10, 13)]
    operation = WorkPreparationPlanner(instance).build_draft(
        [lesson(start=datetime(2026, 9, 7, 18))], [], study,
        now=datetime(2026, 9, 4, 10),
    )
    assert operation.blocks[0].start == datetime(2026, 9, 5, 14)


def test_work_does_not_plan_in_the_past_when_now_has_seconds(tmp_path):
    instance, _ = service(tmp_path)
    now = datetime(2026, 9, 5, 10, 0, 30)
    operation = WorkPreparationPlanner(instance).build_draft(
        [lesson(start=datetime(2026, 9, 7, 18))], [], now=now,
    )
    assert operation.blocks[0].start == datetime(2026, 9, 5, 10, 5)
