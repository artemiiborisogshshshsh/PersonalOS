"""Weekly capacity, fixed commitment and lifecycle tests."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from models import WeeklyCapacityModel
from planning_engine import (
    PlanningEngine,
    PlanningItem,
    PlanningItemType,
    Schedule,
    TimeSlot,
)
from services.weekly_plan_service import (
    CommitmentType,
    FixedCommitment,
    PlanCandidate,
    WeeklyPlan,
    WeeklyPlanPipeline,
    WeeklyPlanStatus,
)


START = datetime(2026, 9, 7, 9, 0)


def flexible_item(item_id="task", duration=60):
    return PlanningItem(
        id=item_id,
        title="Deep work",
        description="Project work",
        item_type=PlanningItemType.PROJECT_TASK,
        duration_minutes=duration,
        earliest_start=START,
        latest_end=START + timedelta(hours=3),
        flexible=True,
        priority=3,
        metadata={'deep_work': True},
    )


def test_fixed_commitment_is_immutable_planning_item():
    commitment = FixedCommitment(
        id="university",
        title="Lecture",
        start=START,
        end=START + timedelta(hours=1),
        commitment_type=CommitmentType.UNIVERSITY,
    )

    item = commitment.to_planning_item()

    assert item.flexible is False
    assert item.earliest_start == commitment.start
    assert item.latest_end == commitment.end
    assert item.metadata['capacity_exempt'] is True


def test_capacity_does_not_count_fixed_commitments_twice():
    capacity = WeeklyCapacityModel(
        total_week_minutes=120,
        sleep_block=0,
        fixed_commitments=60,
        university_load=0,
        teaching_load=0,
        travel_load=0,
        recovery_block=0,
        buffer=0,
    )
    engine = PlanningEngine(weekly_capacity=capacity, num_candidates=4)
    fixed = FixedCommitment(
        id="fixed",
        title="Fixed",
        start=START,
        end=START + timedelta(hours=1),
    )
    plan = WeeklyPlan(
        week_start=START,
        week_end=START + timedelta(hours=2),
        items=[flexible_item()],
        fixed_commitments=[fixed],
    )

    WeeklyPlanPipeline(engine).run_until_selection(plan)

    scheduled = plan.selected_candidate.schedule.get_scheduled_items()
    assert {item.id for item in scheduled} == {"fixed", "task"}


def test_weekly_plan_requires_explicit_approval_before_commit():
    projector = Mock()
    pipeline = WeeklyPlanPipeline(PlanningEngine(num_candidates=4), projector=projector)
    plan = WeeklyPlan(
        week_start=START,
        week_end=START + timedelta(hours=3),
        items=[flexible_item()],
    )

    pipeline.run_until_selection(plan)
    assert plan.status == WeeklyPlanStatus.SELECTED

    with pytest.raises(ValueError):
        pipeline.commit(plan)

    pipeline.approve(plan)
    pipeline.commit(plan)

    assert plan.status == WeeklyPlanStatus.COMMITTED
    projector.project.assert_called_once_with(plan)


def test_pipeline_returns_structured_infeasible_result_for_unplaced_work():
    capacity = WeeklyCapacityModel(
        total_week_minutes=60,
        sleep_block=0,
        fixed_commitments=0,
        university_load=0,
        teaching_load=0,
        travel_load=0,
        recovery_block=0,
        buffer=0,
    )
    plan = WeeklyPlan(
        week_start=START,
        week_end=START + timedelta(hours=3),
        items=[flexible_item(duration=120)],
    )

    WeeklyPlanPipeline(PlanningEngine(weekly_capacity=capacity)).run_until_selection(plan)

    assert plan.status == WeeklyPlanStatus.INFEASIBLE
    assert plan.selected_candidate is None
    assert plan.infeasibility is not None
    assert plan.infeasibility.capacity_deficit_minutes == 60
    assert plan.infeasibility.unplaced_item_ids == ['task']
    assert plan.infeasibility.alternatives
    with pytest.raises(ValueError):
        WeeklyPlanPipeline(PlanningEngine(weekly_capacity=capacity)).approve(plan)


def test_pipeline_generates_and_compares_multiple_candidates():
    engine = PlanningEngine(num_candidates=6)
    pipeline = WeeklyPlanPipeline(engine)
    plan = WeeklyPlan(
        week_start=START,
        week_end=START + timedelta(hours=4),
        items=[flexible_item("first"), flexible_item("second")],
    )

    pipeline.run_until_selection(plan)

    assert len(plan.candidates) >= 4
    assert all(candidate.valid for candidate in plan.candidates)
    assert plan.selected_candidate.score == max(
        candidate.score for candidate in plan.candidates
    )


def test_validate_rejects_sleep_overlap_and_four_hours_of_deep_work():
    deep = PlanningItem(
        id='deep',
        title='Deep work',
        description='',
        item_type=PlanningItemType.PROJECT_TASK,
        duration_minutes=240,
        flexible=True,
        priority=3,
        metadata={'deep_work': True},
    )
    late = PlanningItem(
        id='late',
        title='Late task',
        description='',
        item_type=PlanningItemType.PROJECT_TASK,
        duration_minutes=60,
        flexible=True,
    )
    slots = [
        TimeSlot(
            START + timedelta(minutes=30 * index),
            START + timedelta(minutes=30 * (index + 1)),
            scheduled_item_id=deep.id,
        )
        for index in range(8)
    ]
    slots.append(TimeSlot(
        START.replace(hour=21, minute=30),
        START.replace(hour=22, minute=30),
        scheduled_item_id=late.id,
    ))
    schedule = Schedule(items={deep.id: deep, late.id: late}, slots=slots)
    plan = WeeklyPlan(START, START + timedelta(days=1), [deep, late])
    plan.candidates = [PlanCandidate(schedule, 'external', 100.0)]

    WeeklyPlanPipeline(PlanningEngine()).validate(plan)

    assert not plan.candidates[0].valid
    assert 'max_continuous_deep_work_exceeded' in plan.candidates[0].violations
    assert 'sleep_overlap:late' in plan.candidates[0].violations
