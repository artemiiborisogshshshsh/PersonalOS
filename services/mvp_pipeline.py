"""End-to-end deterministic Personal OS MVP orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from models import PersonalEventState, PersonalUniversityEvent, UniversityEvent
from planning_engine import PlanningItem, create_planning_item_from_preparation_requirement
from services.attendance_service import AttendanceRuleService
from services.obsidian_projection_service import ObsidianProjectionService
from services.preparation.preparation_integration_service import PreparationIntegrationService
from services.sync_coordinator import ScheduleSyncCoordinator, SyncOutcome
from services.tutoring_service import TutoringService, TutoringSession
from services.weekly_plan_service import (
    CommitmentType,
    FixedCommitment,
    WeeklyPlan,
    WeeklyPlanPipeline,
    WeeklyPlanStatus,
)


@dataclass(frozen=True)
class ExecutionRecord:
    id: str
    item_id: str
    planned_start: datetime
    planned_end: datetime
    status: str = 'planned'
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    supersedes_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MvpPipelineResult:
    sync: SyncOutcome
    personal_events: List[PersonalUniversityEvent]
    preparation_requirements: List[Any]
    weekly_plan: WeeklyPlan
    execution_records: List[ExecutionRecord]


class PersonalOSMvpPipeline:
    def __init__(
        self,
        attendance_service: AttendanceRuleService,
        weekly_plan_pipeline: WeeklyPlanPipeline,
        preparation_service: Optional[PreparationIntegrationService] = None,
        tutoring_service: Optional[TutoringService] = None,
        obsidian_service: Optional[ObsidianProjectionService] = None,
        notification_adapter: Any = None,
    ):
        self.attendance_service = attendance_service
        self.weekly_plan_pipeline = weekly_plan_pipeline
        self.preparation_service = preparation_service
        self.tutoring_service = tutoring_service or TutoringService()
        self.obsidian_service = obsidian_service
        self.notification_adapter = notification_adapter
        self.sync_coordinator = ScheduleSyncCoordinator(attendance_service)

    def run(
        self,
        old_university_events: List[UniversityEvent],
        new_university_events: List[UniversityEvent],
        personal_events: List[PersonalUniversityEvent],
        week_start: datetime,
        week_end: datetime,
        planning_items: Optional[List[PlanningItem]] = None,
        fixed_commitments: Optional[List[FixedCommitment]] = None,
        tutoring_sessions: Optional[List[TutoringSession]] = None,
        approved_by: Optional[str] = None,
        obsidian_notes: Optional[Dict[str, Any]] = None,
    ) -> MvpPipelineResult:
        if not approved_by:
            raise PermissionError("Weekly plan requires explicit user approval")

        sync = self.sync_coordinator.sync(
            'university',
            old_university_events,
            new_university_events,
            personal_events,
        )
        active_personal = [
            event for event in personal_events
            if event.state in (PersonalEventState.CONFIRMED, PersonalEventState.MOVED)
            and week_start <= event.start_time < week_end
        ]

        commitments = list(fixed_commitments or [])
        commitments.extend(
            FixedCommitment(
                id=f"university:{event.id}",
                title=event.title,
                start=event.start_time,
                end=event.end_time,
                commitment_type=CommitmentType.UNIVERSITY,
                location=event.location or '',
                metadata={
                    **event.metadata,
                    'personal_event_id': event.id,
                    'university_event_uid': event.university_event_uid,
                },
            )
            for event in active_personal
        )

        requirements = []
        items = list(planning_items or [])
        if self.preparation_service is not None:
            for event in active_personal:
                requirement = self.preparation_service.build_preparation_requirement(event)
                if requirement is not None:
                    requirements.append(requirement)
                    items.append(create_planning_item_from_preparation_requirement(
                        requirement, earliest_start=week_start
                    ))

        tutoring_sessions = list(tutoring_sessions or [])
        tutoring_commitments, tutoring_items = self.tutoring_service.build_weekly_inputs(
            tutoring_sessions, week_start
        )
        commitments.extend(tutoring_commitments)
        items.extend(tutoring_items)

        capacity = self.weekly_plan_pipeline.engine.weekly_capacity
        capacity.fixed_commitments = sum(
            commitment.duration_minutes for commitment in commitments
            if commitment.commitment_type == CommitmentType.OTHER
        )
        capacity.university_load = sum(
            commitment.duration_minutes
            for commitment in commitments
            if commitment.commitment_type == CommitmentType.UNIVERSITY
        )
        capacity.teaching_load = sum(
            commitment.duration_minutes for commitment in commitments
            if commitment.commitment_type == CommitmentType.TUTORING
        )
        capacity.travel_load = sum(
            commitment.duration_minutes for commitment in commitments
            if commitment.commitment_type == CommitmentType.TRAVEL
        )
        for commitment_type, field_name in (
            (CommitmentType.SLEEP, 'sleep_block'),
            (CommitmentType.RECOVERY, 'recovery_block'),
            (CommitmentType.BUFFER, 'buffer'),
        ):
            explicit_minutes = sum(
                commitment.duration_minutes for commitment in commitments
                if commitment.commitment_type == commitment_type
            )
            if explicit_minutes:
                setattr(capacity, field_name, explicit_minutes)

        weekly_plan = WeeklyPlan(
            week_start=week_start,
            week_end=week_end,
            items=items,
            fixed_commitments=commitments,
        )
        self.weekly_plan_pipeline.run_until_selection(
            weekly_plan, granularity_minutes=15
        )
        if weekly_plan.status == WeeklyPlanStatus.INFEASIBLE:
            # An impossible plan is a valid deterministic outcome.  Do not
            # project a partial schedule or pretend that approval can repair
            # it; the caller receives the structured alternatives instead.
            if self.notification_adapter is not None:
                self.notification_adapter.send_notification(
                    "Weekly plan is infeasible; review the proposed alternatives."
                )
            return MvpPipelineResult(
                sync=sync,
                personal_events=personal_events,
                preparation_requirements=requirements,
                weekly_plan=weekly_plan,
                execution_records=[],
            )
        self.weekly_plan_pipeline.approve(weekly_plan, approved_by=approved_by)
        self.weekly_plan_pipeline.commit(weekly_plan)

        if self.obsidian_service is not None and obsidian_notes:
            events_by_course: Dict[str, List[PersonalUniversityEvent]] = {}
            for event in active_personal:
                course = str(event.metadata.get('course') or event.title)
                events_by_course.setdefault(course, []).append(event)
            for course, note_path in obsidian_notes.items():
                course_events = events_by_course.get(course, [])
                course_requirements = [
                    requirement for requirement in requirements
                    if requirement.metadata.get('course_id') == course
                ]
                self.obsidian_service.project_course(
                    note_path,
                    course,
                    course_events,
                    items,
                    course_requirements,
                )

        if self.notification_adapter is not None:
            self.notification_adapter.send_notification(
                f"Weekly plan committed: {weekly_plan.id}"
            )

        records = self._execution_records(weekly_plan)
        return MvpPipelineResult(
            sync=sync,
            personal_events=personal_events,
            preparation_requirements=requirements,
            weekly_plan=weekly_plan,
            execution_records=records,
        )

    def _execution_records(self, plan: WeeklyPlan) -> List[ExecutionRecord]:
        schedule = plan.selected_candidate.schedule
        grouped: Dict[str, List[Any]] = {}
        for slot in schedule.slots:
            if slot.scheduled_item_id:
                grouped.setdefault(slot.scheduled_item_id, []).append(slot)
        return [
            ExecutionRecord(
                id=str(uuid.uuid4()),
                item_id=item_id,
                planned_start=min(slot.start for slot in slots),
                planned_end=max(slot.end for slot in slots),
                metadata={'weekly_plan_id': plan.id},
            )
            for item_id, slots in grouped.items()
        ]

    def record_feedback(
        self,
        record: ExecutionRecord,
        actual_start: datetime,
        actual_end: datetime,
        status: str,
    ) -> ExecutionRecord:
        """Create a correction record; the original execution fact stays immutable."""
        if actual_end <= actual_start:
            raise ValueError("actual_end must be after actual_start")
        return ExecutionRecord(
            id=str(uuid.uuid4()),
            item_id=record.item_id,
            planned_start=record.planned_start,
            planned_end=record.planned_end,
            actual_start=actual_start,
            actual_end=actual_end,
            status=status,
            supersedes_id=record.id,
            metadata=dict(record.metadata),
        )
