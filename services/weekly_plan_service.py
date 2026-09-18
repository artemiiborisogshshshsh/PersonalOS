"""Deterministic weekly-plan domain and GENERATE→COMMIT pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, List, Optional
import uuid

from planning_engine import (
    MaxContinuousWorkConstraint,
    PlanningEngine,
    PlanningItem,
    PlanningItemType,
    Schedule,
    SleepConstraint,
    TimeSlot,
    TravelTimeConstraint,
)


class CommitmentType(Enum):
    SLEEP = "sleep"
    UNIVERSITY = "university"
    TUTORING = "tutoring"
    TRAVEL = "travel"
    RECOVERY = "recovery"
    BUFFER = "buffer"
    OTHER = "other"


@dataclass(frozen=True)
class FixedCommitment:
    """An immutable block that the scheduler must never move."""
    id: str
    title: str
    start: datetime
    end: datetime
    commitment_type: CommitmentType = CommitmentType.OTHER
    location: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("Fixed commitment end must be after start")

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)

    def to_planning_item(self) -> PlanningItem:
        metadata = {
            **self.metadata,
            'is_fixed_commitment': True,
            'commitment_type': self.commitment_type.value,
            'location': self.location,
            'capacity_exempt': True,
            'is_sleep': self.commitment_type == CommitmentType.SLEEP,
            'activity_type': self.commitment_type.value,
        }
        return PlanningItem(
            id=self.id,
            title=self.title,
            description=f"Fixed {self.commitment_type.value} commitment",
            item_type=PlanningItemType.UNIVERSITY_EVENT,
            preferred_start=self.start,
            preferred_end=self.end,
            duration_minutes=self.duration_minutes,
            earliest_start=self.start,
            latest_end=self.end,
            flexible=False,
            priority=5,
            metadata=metadata,
        )


class WeeklyPlanStatus(Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    SCORED = "scored"
    SELECTED = "selected"
    INFEASIBLE = "infeasible"
    APPROVED = "approved"
    COMMITTED = "committed"


@dataclass
class PlanCandidate:
    schedule: Schedule
    strategy: str
    score: float
    valid: bool = False
    violations: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class InfeasibleWeeklyPlan:
    """A deterministic explanation when no complete plan can be selected."""
    capacity_deficit_minutes: int
    unplaced_item_ids: List[str]
    hard_constraints: List[str]
    alternatives: List[str]


@dataclass
class WeeklyPlan:
    week_start: datetime
    week_end: datetime
    items: List[PlanningItem]
    fixed_commitments: List[FixedCommitment] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    candidates: List[PlanCandidate] = field(default_factory=list)
    selected_candidate: Optional[PlanCandidate] = None
    infeasibility: Optional[InfeasibleWeeklyPlan] = None
    status: WeeklyPlanStatus = WeeklyPlanStatus.DRAFT
    approved_at: Optional[datetime] = None
    committed_at: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.week_end <= self.week_start:
            raise ValueError("week_end must be after week_start")


class WeeklyPlanPipeline:
    """Explicit GENERATE → VALIDATE → SCORE → SELECT → APPROVE → COMMIT flow."""

    def __init__(self, engine: Optional[PlanningEngine] = None, projector: Any = None):
        self.engine = engine or PlanningEngine()
        self.projector = projector

    def generate(self, plan: WeeklyPlan, granularity_minutes: int = 30) -> WeeklyPlan:
        fixed_items = [commitment.to_planning_item() for commitment in plan.fixed_commitments]
        all_items = [*fixed_items, *plan.items]
        raw_candidates = self.engine.generate_candidate_schedules(
            all_items,
            plan.week_start,
            plan.week_end,
            granularity_minutes,
        )
        plan.candidates = [
            PlanCandidate(schedule=schedule, strategy=strategy, score=score)
            for schedule, strategy, score in raw_candidates
        ]
        plan.status = WeeklyPlanStatus.DRAFT
        return plan

    def validate(self, plan: WeeklyPlan) -> WeeklyPlan:
        fixed_ids = {commitment.id for commitment in plan.fixed_commitments}
        for candidate in plan.candidates:
            violations: List[str] = []
            scheduled_ids = {
                slot.scheduled_item_id for slot in candidate.schedule.slots
                if slot.scheduled_item_id
            }
            missing_fixed = fixed_ids - scheduled_ids
            if missing_fixed:
                violations.append(f"missing_fixed:{','.join(sorted(missing_fixed))}")

            spans = sorted(
                (slot.start, slot.end, slot.scheduled_item_id)
                for slot in candidate.schedule.slots
                if slot.scheduled_item_id
            )
            for (_, previous_end, previous_id), (start, _, current_id) in zip(spans, spans[1:]):
                if start < previous_end and previous_id != current_id:
                    violations.append(f"overlap:{previous_id}:{current_id}")

            grouped_slots: dict[str, List[TimeSlot]] = {}
            for slot in candidate.schedule.slots:
                if slot.scheduled_item_id:
                    grouped_slots.setdefault(slot.scheduled_item_id, []).append(slot)

            sleep_constraint = SleepConstraint()
            travel_constraint = TravelTimeConstraint()
            for item_id, item_slots in grouped_slots.items():
                item = candidate.schedule.items[item_id]
                span = TimeSlot(
                    min(slot.start for slot in item_slots),
                    max(slot.end for slot in item_slots),
                    scheduled_item_id=item_id,
                )
                scheduled_minutes = sum(slot.duration_minutes for slot in item_slots)
                if scheduled_minutes < item.duration_minutes:
                    violations.append(f"incomplete_item:{item_id}")
                if item.earliest_start and span.start < item.earliest_start:
                    violations.append(f"before_earliest_start:{item_id}")
                if item.latest_end and span.end > item.latest_end:
                    violations.append(f"after_latest_end:{item_id}")
                if sleep_constraint.evaluate(item, span, candidate.schedule) == float('-inf'):
                    violations.append(f"sleep_overlap:{item_id}")
                if travel_constraint.evaluate(item, span, candidate.schedule) == float('-inf'):
                    violations.append(f"travel_gap:{item_id}")

            # Validate the schedule as a timeline, independently from the
            # incremental placement checks used during candidate generation.
            continuous_deep_minutes = 0
            previous_end = None
            for slot in sorted(
                (slot for slot in candidate.schedule.slots if slot.scheduled_item_id),
                key=lambda value: (value.start, value.end),
            ):
                item = candidate.schedule.items[slot.scheduled_item_id]
                is_deep = (
                    item.metadata.get('deep_work', False)
                    or item.metadata.get('cognitive_load', 'low') in ('medium', 'high')
                    or item.priority >= 3
                )
                contiguous = previous_end is not None and slot.start == previous_end
                if is_deep and contiguous:
                    continuous_deep_minutes += slot.duration_minutes
                elif is_deep:
                    continuous_deep_minutes = slot.duration_minutes
                else:
                    continuous_deep_minutes = 0
                if continuous_deep_minutes > MaxContinuousWorkConstraint().max_continuous_work_minutes:
                    violations.append("max_continuous_deep_work_exceeded")
                    break
                previous_end = slot.end

            flexible_minutes = sum(
                slot.duration_minutes
                for slot in candidate.schedule.slots
                if slot.scheduled_item_id
                and candidate.schedule.items[slot.scheduled_item_id].flexible
                and not candidate.schedule.items[slot.scheduled_item_id].metadata.get(
                    'capacity_exempt', False
                )
            )
            if flexible_minutes > self.engine.weekly_capacity.available_capacity:
                violations.append("weekly_capacity_exceeded")

            candidate.violations = list(dict.fromkeys(violations))
            candidate.valid = not violations

        plan.status = WeeklyPlanStatus.VALIDATED
        return plan

    def score(self, plan: WeeklyPlan) -> WeeklyPlan:
        if plan.status != WeeklyPlanStatus.VALIDATED:
            raise ValueError("Weekly plan must be validated before scoring")
        for candidate in plan.candidates:
            unscheduled_penalty = sum(
                max(1, item.priority) * max(1, item.duration_minutes)
                for item in candidate.schedule.unscheduled_items
                if item.flexible
            )
            candidate.score = candidate.schedule.total_score - unscheduled_penalty
        plan.status = WeeklyPlanStatus.SCORED
        return plan

    def select(self, plan: WeeklyPlan) -> Optional[PlanCandidate]:
        if plan.status != WeeklyPlanStatus.SCORED:
            raise ValueError("Weekly plan must be scored before selection")
        valid_candidates = [candidate for candidate in plan.candidates if candidate.valid]
        complete_candidates = [
            candidate for candidate in valid_candidates
            if not any(item.flexible for item in candidate.schedule.unscheduled_items)
        ]
        if not complete_candidates:
            plan.selected_candidate = None
            plan.infeasibility = self._infeasibility(plan, valid_candidates)
            plan.status = WeeklyPlanStatus.INFEASIBLE
            return None
        plan.selected_candidate = max(complete_candidates, key=lambda candidate: candidate.score)
        plan.infeasibility = None
        plan.status = WeeklyPlanStatus.SELECTED
        return plan.selected_candidate

    def _infeasibility(
        self,
        plan: WeeklyPlan,
        valid_candidates: List[PlanCandidate],
    ) -> InfeasibleWeeklyPlan:
        """Explain the least-bad candidate without suggesting source moves."""
        candidates = valid_candidates or plan.candidates
        best = min(
            candidates,
            key=lambda candidate: (
                len([item for item in candidate.schedule.unscheduled_items if item.flexible]),
                len(candidate.violations),
                -candidate.score,
            ),
            default=None,
        )
        unplaced = [] if best is None else [
            item.id for item in best.schedule.unscheduled_items if item.flexible
        ]
        hard_constraints = [] if best is None else list(best.violations)
        required_minutes = sum(
            item.duration_minutes for item in plan.items
            if item.flexible and not item.metadata.get("capacity_exempt", False)
        )
        deficit = max(0, required_minutes - self.engine.weekly_capacity.available_capacity)
        alternatives: List[str] = []
        if deficit:
            alternatives.append(
                f"Уменьшить или перенести гибкую нагрузку минимум на {deficit} мин."
            )
        if unplaced:
            alternatives.append(
                "Изменить срок, длительность или пользовательское ограничение одной из невмещённых задач."
            )
        if hard_constraints:
            alternatives.append(
                "Проверить сон, дорогу, recovery и другие фиксированные обязательства; источниковые занятия не перемещаются автоматически."
            )
        if not alternatives:
            alternatives.append("Добавить доступное окно или сократить гибкую нагрузку.")
        return InfeasibleWeeklyPlan(
            capacity_deficit_minutes=deficit,
            unplaced_item_ids=unplaced,
            hard_constraints=hard_constraints,
            alternatives=alternatives,
        )

    def approve(self, plan: WeeklyPlan, approved_by: str = "user") -> WeeklyPlan:
        if plan.status != WeeklyPlanStatus.SELECTED or plan.selected_candidate is None:
            raise ValueError("A selected candidate is required before approval")
        plan.status = WeeklyPlanStatus.APPROVED
        plan.approved_at = datetime.now()
        plan.metadata['approved_by'] = approved_by
        return plan

    def commit(self, plan: WeeklyPlan) -> WeeklyPlan:
        if plan.status != WeeklyPlanStatus.APPROVED or plan.selected_candidate is None:
            raise ValueError("Weekly plan must be approved before commit")
        if self.projector is not None:
            plan.metadata['projection_result'] = self.projector.project(plan)
        plan.status = WeeklyPlanStatus.COMMITTED
        plan.committed_at = datetime.now()
        return plan

    def run_until_selection(
        self,
        plan: WeeklyPlan,
        granularity_minutes: int = 30,
    ) -> WeeklyPlan:
        self.generate(plan, granularity_minutes)
        self.validate(plan)
        self.score(plan)
        self.select(plan)
        return plan
