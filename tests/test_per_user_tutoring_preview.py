"""Synthetic tutoring inputs share the owned, non-projecting weekly preview."""

from dataclasses import asdict
from datetime import datetime, timedelta
import json
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from models import WeeklyCapacityModel
from planning_engine import PlanningEngine
from services.per_user_weekly_preview import PerUserWeeklyPreview
from services.tutoring_service import TutoringMode, TutoringSession
from services.weekly_plan_service import CommitmentType, WeeklyPlan, WeeklyPlanPipeline, WeeklyPlanStatus


START = datetime(2026, 9, 21, 9, tzinfo=ZoneInfo('Asia/Tomsk'))


def session(**overrides):
    fields = dict(
        id='lesson-1', student='Synthetic student', start=START + timedelta(hours=2),
        duration_minutes=45, mode=TutoringMode.OFFLINE,
        travel_before_minutes=15, travel_after_minutes=15,
        material_preparation_minutes=30, homework_review_minutes=30,
    )
    return TutoringSession(**(fields | overrides))


def save_fixture(directory, *sessions):
    records = []
    for value in sessions:
        record = asdict(value)
        record.pop('metadata')
        record['start'] = value.start.isoformat()
        record['mode'] = value.mode.value
        records.append(record)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'tutoring_sessions.json').write_text(
        json.dumps({'version': 1, 'sessions': records}), encoding='utf-8',
    )


def base_plan():
    return WeeklyPlan(START, START + timedelta(hours=6), items=[])


def pipeline():
    return WeeklyPlanPipeline(PlanningEngine(
        weekly_capacity=WeeklyCapacityModel(
            total_week_minutes=360, sleep_block=0, recovery_block=0, buffer=0,
        ), num_candidates=4,
    ))


def test_confirmed_45_minute_lesson_travel_and_work_share_the_existing_scheduler(tmp_path):
    save_fixture(tmp_path, session())
    preview = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline)

    plan = preview.preview()

    assert plan.status is WeeklyPlanStatus.SELECTED
    teaching = [block for block in plan.fixed_commitments
                if block.commitment_type is CommitmentType.TUTORING]
    assert len(teaching) == 1
    assert teaching[0].start == START + timedelta(hours=2)
    assert teaching[0].end == START + timedelta(hours=2, minutes=45)
    schedule = plan.selected_candidate.schedule
    lesson_slots = [slot for slot in schedule.slots if slot.scheduled_item_id == teaching[0].id]
    assert sum(slot.duration_minutes for slot in lesson_slots) == 45
    assert {item.metadata['activity_type'] for item in plan.items} == {
        'material_preparation', 'homework_review',
    }
    assert len(plan.fixed_commitments) == 3
    for item in plan.items:
        slots = [slot for slot in schedule.slots if slot.scheduled_item_id == item.id]
        assert slots
        if item.metadata['activity_type'] == 'material_preparation':
            assert max(slot.end for slot in slots) <= teaching[0].start
        else:
            assert min(slot.start for slot in slots) >= teaching[0].end


def test_personal_tutoring_and_travel_capacity_are_counted_once_on_a_copied_engine(tmp_path):
    save_fixture(tmp_path, session())
    (tmp_path / 'natural_commands.json').write_text(json.dumps({
        'version': 1, 'tasks': [], 'executed': {}, 'personal_events': [{
            'id': 'meeting', 'summary': 'Personal meeting',
            'start': (START + timedelta(hours=4)).isoformat(),
            'end': (START + timedelta(hours=5)).isoformat(),
        }],
    }), encoding='utf-8')
    source_bytes = (tmp_path / 'tutoring_sessions.json').read_bytes()
    configured = pipeline()
    capacity = configured.engine.weekly_capacity
    capacity.teaching_load, capacity.travel_load, capacity.fixed_commitments = 20, 30, 30
    observed = []
    original = WeeklyPlanPipeline.run_until_selection

    def capture(instance, plan, granularity_minutes=30):
        current = instance.engine.weekly_capacity
        observed.append((current.teaching_load, current.travel_load, current.fixed_commitments))
        return original(instance, plan, granularity_minutes)

    preview = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=lambda: configured)
    with patch.object(WeeklyPlanPipeline, 'run_until_selection', capture):
        first = preview.preview()
        second = preview.preview()
    assert first.status is second.status is WeeklyPlanStatus.SELECTED
    assert observed == [(65, 60, 90), (65, 60, 90)]
    assert (capacity.teaching_load, capacity.travel_load, capacity.fixed_commitments) == (20, 30, 30)
    assert configured.engine.metrics == {}
    assert (tmp_path / 'tutoring_sessions.json').read_bytes() == source_bytes


def test_adjacent_travel_and_homework_tail_intersect_horizon_without_future_preparation(tmp_path):
    previous = session(id='previous', start=START - timedelta(minutes=45))
    future = session(id='future', start=START + timedelta(hours=6, minutes=15),
                     travel_before_minutes=30)
    expired = session(id='expired', start=START - timedelta(days=3))
    save_fixture(tmp_path, future, expired, previous)

    plan = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline).preview()

    assert plan.status is WeeklyPlanStatus.SELECTED
    assert [(value.start, value.end, value.commitment_type) for value in plan.fixed_commitments] == [
        (START, START + timedelta(minutes=15), CommitmentType.TRAVEL),
        (START + timedelta(hours=5, minutes=45), START + timedelta(hours=6), CommitmentType.TRAVEL),
    ]
    assert [item.id for item in plan.items] == ['tutoring:previous:homework-review']
    assert plan.items[0].earliest_start == START


@pytest.mark.parametrize('offset, expected_minutes', [(-15, 30), (345, 15)])
def test_lesson_crossing_horizon_is_clipped_without_changing_store(tmp_path, offset, expected_minutes):
    save_fixture(tmp_path, session(start=START + timedelta(minutes=offset),
                                   material_preparation_minutes=0, homework_review_minutes=0))
    before = (tmp_path / 'tutoring_sessions.json').read_bytes()

    plan = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline).preview()

    assert plan.status is WeeklyPlanStatus.SELECTED
    lesson = next(value for value in plan.fixed_commitments
                  if value.commitment_type is CommitmentType.TUTORING)
    assert lesson.duration_minutes == expected_minutes
    assert (tmp_path / 'tutoring_sessions.json').read_bytes() == before


def test_preparation_without_time_and_colliding_generated_ids_fail_closed(tmp_path):
    save_fixture(tmp_path, session(start=START, mode=TutoringMode.ONLINE,
                                   travel_before_minutes=0, travel_after_minutes=0))
    preview = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline)
    plan = preview.preview()
    assert plan.status is WeeklyPlanStatus.INFEASIBLE
    assert 'tutoring:lesson-1:materials' in plan.infeasibility.unplaced_item_ids

    save_fixture(tmp_path, session(id='one'), session(id='one:travel-before'))
    with pytest.raises(ValueError, match='identifier'):
        preview.preview()
    assert 'Не удалось безопасно' in preview()


def test_confirmed_owned_store_reloads_through_telegram_preview_without_cross_user_leak(tmp_path):
    from services.onboarding_service import OnboardingState, OnboardingStep, OnboardingStore, TelegramOnboardingService
    from services.product_state import UserProductStateStore
    from services.schedule_source_service import ScheduleSourceService
    from services.telegram_multi_user_runtime import TelegramMultiUserDispatcher
    from services.telegram_onboarding_handler import TelegramOnboardingHandler
    from services.telegram_user_router import TelegramUserRouter
    from services.user_planning_profile_store import UserPlanningProfileStore
    from services.user_registry import UserRegistryStore, UserStatePaths
    from services.user_tutoring_store import UserTutoringStore

    registry = UserRegistryStore(tmp_path / 'registry.json')
    paths = UserStatePaths(tmp_path)

    def factory(account, directory):
        onboarding = OnboardingStore(directory / 'onboarding.json')
        onboarding.save(OnboardingState(step=OnboardingStep.COMPLETE))
        return TelegramOnboardingHandler(
            account=account, state_directory=directory,
            onboarding=TelegramOnboardingService(onboarding),
            source_service=ScheduleSourceService(UserProductStateStore(directory / 'product_state.json')),
            events_for_source=lambda _: (),
            profile_store=UserPlanningProfileStore(directory / 'planning_profile.json'),
            calendar_connected=lambda: False,
            weekly_preview=PerUserWeeklyPreview(directory, base_plan, pipeline_factory=pipeline),
        )

    def dispatcher():
        return TelegramMultiUserDispatcher(TelegramUserRouter(registry, paths, factory))

    def reply(runtime, chat):
        return runtime.handle_update({'message': {'chat': {'id': chat}, 'text': '/weekly_preview'}})[1]['text']

    runtime = dispatcher()
    for chat in ('101', '202'):
        runtime.handle_update({'message': {'chat': {'id': chat}, 'text': '/start'}})
    assert 'Synthetic student' not in reply(runtime, '101')
    first = registry.resolve('101')
    store = UserTutoringStore(paths.directory(first.id))
    with pytest.raises(PermissionError):
        store.upsert(session())
    assert 'Synthetic student' not in reply(runtime, '101')
    store.upsert(session(), confirmed=True)
    before = store.path.read_bytes()
    assert 'Synthetic student' in reply(runtime, '101')
    assert 'Synthetic student' not in reply(runtime, '202')
    assert reply(dispatcher(), '101') == reply(runtime, '101')
    assert store.path.read_bytes() == before
