"""Compose one user's internal project, personal and tutoring inputs into a preview."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models import ProjectStatus, TaskStatus
from planning_engine import create_planning_item_from_task
from services.natural_text_application import PerUserNaturalCommandExecutor
from services.user_planning_profile_store import UserPlanningProfileStore
from services.user_project_store import UserProjectStore
from services.user_tutoring_store import UserTutoringStore
from services.tutoring_service import TutoringService
from services.weekly_plan_service import (
    CommitmentType,
    FixedCommitment,
    WeeklyPlan,
    WeeklyPlanPipeline,
    WeeklyPlanStatus,
)


PlanProvider = Callable[[], WeeklyPlan]
PipelineFactory = Callable[[], WeeklyPlanPipeline]
PREVIEW_GRANULARITY_MINUTES = 15


class PerUserWeeklyPreview:
    """Build an uncommitted weekly preview from a router-owned state directory.

    The provider remains the authority for the current base plan.  This bridge
    adds confirmed project tasks, personal events and tutoring from local state and
    always works on a deep-copied draft. Store mutation is a separate action.
    """

    def __init__(
        self,
        state_directory: Path,
        plan_provider: PlanProvider,
        pipeline_factory: PipelineFactory = WeeklyPlanPipeline,
    ) -> None:
        self.state_directory = Path(state_directory)
        self.plan_provider = plan_provider
        self.pipeline_factory = pipeline_factory

    def preview(self) -> WeeklyPlan:
        """Return a selected or infeasible local draft without approving it."""
        try:
            settings = UserPlanningProfileStore(
                self.state_directory / "planning_profile.json",
            ).load()
        except (AttributeError, KeyError, TypeError) as error:
            raise ValueError("Invalid planning profile state") from error
        try:
            profile_timezone = ZoneInfo(settings.profile.timezone)
        except (TypeError, ZoneInfoNotFoundError) as error:
            raise ValueError("Invalid planning profile timezone") from error

        plan = self._fresh_draft(self.plan_provider())
        horizon_timezone = self._horizon_timezone(plan)
        self._normalise_plan_datetimes(plan, profile_timezone, horizon_timezone)
        blockers = self._personal_event_blockers(
            plan, profile_timezone, horizon_timezone,
        )
        tutoring_commitments, tutoring_work = self._tutoring_inputs(
            plan, profile_timezone, horizon_timezone,
        )
        project_work = self._project_inputs(plan, profile_timezone, horizon_timezone)
        self._assert_no_id_collisions(plan, [*blockers, *tutoring_commitments, *tutoring_work, *project_work])
        plan.fixed_commitments.extend([*blockers, *tutoring_commitments])
        plan.items.extend([*tutoring_work, *project_work])

        # A factory may return a reusable configured pipeline.  Clone it so
        # mutable engine capacity and metrics never leak to a later preview.
        configured_pipeline = self.pipeline_factory()
        if not isinstance(configured_pipeline, WeeklyPlanPipeline):
            raise ValueError("Invalid weekly plan pipeline")
        pipeline = WeeklyPlanPipeline(engine=deepcopy(configured_pipeline.engine))
        pipeline.engine.weekly_capacity.fixed_commitments += sum(
            item.duration_minutes for item in blockers
        )
        pipeline.engine.weekly_capacity.teaching_load += sum(
            item.duration_minutes for item in tutoring_commitments
            if item.commitment_type is CommitmentType.TUTORING
        )
        pipeline.engine.weekly_capacity.travel_load += sum(
            item.duration_minutes for item in tutoring_commitments
            if item.commitment_type is CommitmentType.TRAVEL
        )
        return pipeline.run_until_selection(plan, granularity_minutes=PREVIEW_GRANULARITY_MINUTES)

    @staticmethod
    def _fresh_draft(source: WeeklyPlan) -> WeeklyPlan:
        if not isinstance(source, WeeklyPlan):
            raise ValueError("Invalid weekly plan")
        metadata = {
            key: deepcopy(value)
            for key, value in source.metadata.items()
            if key not in {"approved_by", "projection_result"}
        }
        return WeeklyPlan(
            week_start=deepcopy(source.week_start),
            week_end=deepcopy(source.week_end),
            items=deepcopy(source.items),
            fixed_commitments=deepcopy(source.fixed_commitments),
            metadata=metadata,
        )

    def __call__(self) -> str:
        """Render a credential-safe Russian reply for Telegram's callback."""
        try:
            return self._render(self.preview())
        except (OSError, ValueError):
            return (
                "Не удалось безопасно собрать черновик недели из локальных данных. "
                "Проверь настройки профиля и повтори /weekly_preview."
            )

    @staticmethod
    def _aware(value: datetime) -> bool:
        return value.tzinfo is not None and value.utcoffset() is not None

    def _horizon_timezone(self, plan: WeeklyPlan):
        if not self._aware(plan.week_start) or not self._aware(plan.week_end):
            raise ValueError("Weekly preview requires a timezone-aware horizon")
        return plan.week_start.tzinfo

    def _normalise_datetime(
        self,
        value: datetime | None,
        profile_timezone: ZoneInfo,
        horizon_timezone,
    ) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise ValueError("Invalid stored timestamp")
        if not self._aware(value):
            value = value.replace(tzinfo=profile_timezone)
        return value.astimezone(horizon_timezone)

    def _normalise_plan_datetimes(
        self,
        plan: WeeklyPlan,
        profile_timezone: ZoneInfo,
        horizon_timezone,
    ) -> None:
        plan.week_start = self._normalise_datetime(
            plan.week_start, profile_timezone, horizon_timezone,
        )
        plan.week_end = self._normalise_datetime(
            plan.week_end, profile_timezone, horizon_timezone,
        )
        plan.fixed_commitments = [
            replace(
                commitment,
                start=self._normalise_datetime(
                    commitment.start, profile_timezone, horizon_timezone,
                ),
                end=self._normalise_datetime(
                    commitment.end, profile_timezone, horizon_timezone,
                ),
            )
            for commitment in plan.fixed_commitments
        ]
        for item in plan.items:
            item.preferred_start = self._normalise_datetime(
                item.preferred_start, profile_timezone, horizon_timezone,
            )
            item.preferred_end = self._normalise_datetime(
                item.preferred_end, profile_timezone, horizon_timezone,
            )
            item.earliest_start = self._normalise_datetime(
                item.earliest_start, profile_timezone, horizon_timezone,
            )
            item.latest_end = self._normalise_datetime(
                item.latest_end, profile_timezone, horizon_timezone,
            )

    def _personal_event_blockers(
        self,
        plan: WeeklyPlan,
        profile_timezone: ZoneInfo,
        horizon_timezone,
    ) -> list[FixedCommitment]:
        try:
            state = PerUserNaturalCommandExecutor(
                self.state_directory / "natural_commands.json",
            ).snapshot()
        except (AttributeError, KeyError, TypeError) as error:
            raise ValueError("Invalid natural command state") from error
        if not isinstance(state, dict):
            raise ValueError("Invalid natural command state")
        events = state.get("personal_events")
        if (
            not isinstance(state.get("tasks"), list)
            or not isinstance(events, list)
            or not isinstance(state.get("executed"), dict)
        ):
            raise ValueError("Invalid natural command state")

        blockers: list[FixedCommitment] = []
        seen_ids: set[str] = set()
        for record in events:
            if not isinstance(record, dict):
                raise ValueError("Invalid natural command record")
            record_id = record.get("id")
            summary = record.get("summary")
            start_raw = record.get("start")
            end_raw = record.get("end")
            if (
                not isinstance(record_id, str) or not record_id
                or not isinstance(summary, str) or not summary.strip()
                or not isinstance(start_raw, str) or not isinstance(end_raw, str)
            ):
                raise ValueError("Invalid personal event")
            source_id = f"natural-command:personal-event:{record_id}"
            if source_id in seen_ids:
                raise ValueError("Duplicate personal event")
            seen_ids.add(source_id)
            try:
                start = datetime.fromisoformat(start_raw)
                end = datetime.fromisoformat(end_raw)
            except (TypeError, ValueError) as error:
                raise ValueError("Invalid personal event timestamp") from error
            start = self._normalise_datetime(start, profile_timezone, horizon_timezone)
            end = self._normalise_datetime(end, profile_timezone, horizon_timezone)
            if end <= start:
                raise ValueError("Invalid personal event interval")
            clipped_start = max(start, plan.week_start)
            clipped_end = min(end, plan.week_end)
            if clipped_end <= clipped_start:
                continue
            blockers.append(FixedCommitment(
                id=source_id,
                title=summary.strip(),
                start=clipped_start,
                end=clipped_end,
                commitment_type=CommitmentType.OTHER,
                metadata={
                    "source": "natural_commands.personal_events",
                    "source_id": record_id,
                    "original_start": start.isoformat(),
                    "original_end": end.isoformat(),
                },
            ))
        return sorted(blockers, key=lambda item: (item.start, item.end, item.id))

    def _project_inputs(self, plan, profile_timezone, horizon_timezone):
        projects, tasks = UserProjectStore(self.state_directory).load()
        projects_by_id = {project.id: project for project in projects}
        tasks_by_id = {task.id: task for task in tasks}
        base_ids = {item.id for item in [*plan.items, *plan.fixed_commitments]}
        if base_ids.intersection(f'project-task:{task.id}' for task in tasks):
            # Reserve even excluded task IDs: a stale base-plan item must not
            # satisfy an INBOX/paused prerequisite or revive completed work.
            raise ValueError('Owned project task identifier collides with base plan')
        completed = {TaskStatus.DONE, TaskStatus.COMPLETED}
        items = []
        # The store validates all references/cycles. Topological input order
        # also gives the existing backtracking strategy a dependency-first
        # candidate without changing priorities or the scheduler algorithm.
        for task_id in TopologicalSorter({task.id: task.dependencies for task in tasks}).static_order():
            task = tasks_by_id[task_id]
            if task.status not in {TaskStatus.TODO, TaskStatus.IN_PROGRESS}:
                continue
            project = projects_by_id.get(task.project_id)
            if project is not None and project.status is not ProjectStatus.ACTIVE:
                continue
            earliest = max(
                plan.week_start,
                self._normalise_datetime(task.created_at, profile_timezone, horizon_timezone),
            )
            if project is not None and project.start_date is not None:
                project_start = self._normalise_datetime(project.start_date, profile_timezone, horizon_timezone)
                if project_start >= plan.week_end:
                    continue
                earliest = max(earliest, project_start)
            due_date = self._normalise_datetime(task.due_date, profile_timezone, horizon_timezone)
            owned_task = replace(
                task, id=f'project-task:{task.id}', due_date=due_date,
                project_id=f'project:{task.project_id}' if task.project_id is not None else None,
                dependencies=[
                    f'project-task:{dependency}' for dependency in task.dependencies
                    if tasks_by_id[dependency].status not in completed
                ],
            )
            item = create_planning_item_from_task(owned_task, earliest_start=earliest)
            item.latest_end = min(due_date, plan.week_end) if due_date is not None else plan.week_end
            items.append(item)
        # Reserve the minimum duration of each dependent before its deadline.
        # Otherwise a prerequisite's soft due-date preference can consume the
        # entire remaining window even when a dependency chain fits. These are
        # necessary bounds on copied inputs, not edits to operational deadlines.
        by_id = {item.id: item for item in items}
        for item in reversed(items):
            occupied_minutes = (
                (item.duration_minutes + PREVIEW_GRANULARITY_MINUTES - 1)
                // PREVIEW_GRANULARITY_MINUTES * PREVIEW_GRANULARITY_MINUTES
            )
            if occupied_minutes > (item.latest_end - plan.week_start).total_seconds() / 60:
                # The dependent itself cannot fit; let the pipeline report it
                # unplaced without overflowing datetime on an oversized estimate.
                continue
            prerequisite_end = item.latest_end - timedelta(minutes=occupied_minutes)
            for dependency in sorted(item.dependencies):
                prerequisite = by_id.get(dependency)
                if prerequisite is None:
                    continue  # An incomplete excluded dependency still blocks placement.
                prerequisite.latest_end = min(prerequisite.latest_end, prerequisite_end)
                if prerequisite.preferred_end is not None:
                    prerequisite.preferred_end = min(prerequisite.preferred_end, prerequisite.latest_end)
                if prerequisite.preferred_start is not None:
                    prerequisite.preferred_start = min(
                        prerequisite.preferred_start,
                        prerequisite.latest_end - timedelta(minutes=prerequisite.duration_minutes),
                    )
        return items

    def _tutoring_inputs(self, plan, profile_timezone, horizon_timezone):
        commitments = []
        work = []
        for session in UserTutoringStore(self.state_directory).load():
            # Namespace the domain ID before conversion: lesson, travel and
            # preparation IDs then stay stable together across restarts.
            session = replace(
                session, id=f'tutoring:{session.id}',
                start=self._normalise_datetime(session.start, profile_timezone, horizon_timezone),
            )
            session_commitments, session_work = TutoringService().build_weekly_inputs(
                [session], plan.week_start,
            )
            for commitment in session_commitments:
                start = max(commitment.start, plan.week_start)
                end = min(commitment.end, plan.week_end)
                if start < end:
                    commitments.append(replace(commitment, start=start, end=end))
            for item in session_work:
                if item.metadata['activity_type'] == 'material_preparation':
                    # Material preparation belongs to the lesson's horizon.
                    # A lesson at the horizon start has no available prep
                    # window and must remain visibly unplaced.
                    if not plan.week_start <= session.start < plan.week_end:
                        continue
                elif not (item.earliest_start < plan.week_end and item.latest_end > plan.week_start):
                    # Homework may carry into this horizon within its existing
                    # two-day domain window, including from a prior lesson.
                    continue
                item.earliest_start = max(item.earliest_start, plan.week_start)
                item.latest_end = min(item.latest_end, plan.week_end)
                work.append(item)
        return (
            sorted(commitments, key=lambda item: (item.start, item.end, item.id)),
            sorted(work, key=lambda item: item.id),
        )

    @staticmethod
    def _assert_no_id_collisions(plan: WeeklyPlan, additions) -> None:
        base_ids = {item.id for item in plan.items}
        base_ids.update(item.id for item in plan.fixed_commitments)
        added_ids = [item.id for item in additions]
        if len(set(added_ids)) != len(added_ids) or base_ids.intersection(added_ids):
            raise ValueError("Input identifier collides with plan item")

    @staticmethod
    def _render(plan: WeeklyPlan) -> str:
        timezone_name = getattr(plan.week_start.tzinfo, "key", None) or str(plan.week_start.tzinfo)
        if plan.status is WeeklyPlanStatus.INFEASIBLE:
            unplaced = plan.infeasibility.unplaced_item_ids if plan.infeasibility else []
            detail = ", ".join(unplaced) if unplaced else "доступные ограничения"
            return (
                f"Не удалось подобрать полный черновик недели с шагом {PREVIEW_GRANULARITY_MINUTES} мин.: "
                f"{detail}. Фиксированные события сохранены без изменений."
            )

        if plan.status is not WeeklyPlanStatus.SELECTED or plan.selected_candidate is None:
            raise ValueError("Weekly preview was not selected")
        schedule = plan.selected_candidate.schedule
        slots = sorted(
            (slot for slot in schedule.slots if slot.scheduled_item_id),
            key=lambda item: (item.start, item.end, item.scheduled_item_id or ""),
        )
        entries = []
        for slot in slots:
            item = schedule.items[slot.scheduled_item_id]
            if entries and entries[-1][0] == item.id and entries[-1][2] == slot.start:
                entries[-1] = (item.id, entries[-1][1], slot.end, item.title)
            else:
                entries.append((item.id, slot.start, slot.end, item.title))
        rendered = []
        for _, start, end, title in entries:
            rendered.append(f"{start.strftime('%d.%m %H:%M')}–{end.strftime('%H:%M')} {title}")
        return "Черновик недели ({}):\n{}".format(timezone_name, "\n".join(rendered) or "Нет блоков.")
