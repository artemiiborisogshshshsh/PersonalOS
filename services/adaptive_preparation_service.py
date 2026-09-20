"""Deterministic preparation planning with draft/confirm/rollback semantics."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
from typing import Dict, Iterable, List, Optional, Tuple
import uuid
from zoneinfo import ZoneInfo

from models import PersonalEventState, PersonalUniversityEvent
from planning_engine import (
    MaxContinuousWorkConstraint,
    PlanningEngine,
    PlanningItem,
    PlanningItemType,
)
from services.weekly_plan_service import CommitmentType, FixedCommitment


class CalendarRoute(Enum):
    STUDY = 'Учёба'
    WORK = 'Работа'
    PERSONAL = 'Личное'
    SLEEP = 'Сон'


def is_manual_conflict(block: object) -> bool:
    """Only the literal boolean marker authorizes a conflict projection."""
    return getattr(block, 'manual_conflict', None) is True


PERSONAL_UNIVERSITY_CALENDAR = 'Personal University Schedule'


def calendar_route_for(activity_type: str) -> tuple[CalendarRoute, str]:
    """Return the user-visible calendar and Google color category."""
    mapping = {
        'lecture': (CalendarRoute.STUDY, 'ЛК'),
        'practical': (CalendarRoute.STUDY, 'ПР'),
        'lab': (CalendarRoute.STUDY, 'ЛБ'),
        'preparation': (CalendarRoute.STUDY, 'PREPARATION'),
        'work_preparation': (CalendarRoute.WORK, 'WORK_PREPARATION'),
        'work_lesson': (CalendarRoute.WORK, 'WORK_LESSON'),
        'project_task': (CalendarRoute.WORK, 'PROJECT_TASK'),
        'reading': (CalendarRoute.PERSONAL, 'READING'),
        'social': (CalendarRoute.PERSONAL, 'SOCIAL'),
        'sleep': (CalendarRoute.SLEEP, 'SLEEP'),
    }
    return mapping[activity_type]


@dataclass(frozen=True)
class UserPlanningProfile:
    lecture_minutes: int = 20
    practical_minutes: int = 60
    lab_minutes: int = 60
    # Kept under its legacy name so saved profiles remain readable.  It is the
    # urgency threshold, not the size of the planning window.
    preparation_lead_hours: int = 36
    preparation_window_days: int = 7
    preparation_break_minutes: int = 15
    max_single_block_minutes: int = 120
    max_continuous_deep_work_minutes: int = 120
    travel_minutes_each_way: int = 60
    recovery_minutes_after_university: int = 60
    # Current calendar week plus the full following calendar week.
    planning_horizon_weeks: int = 2
    timezone: str = 'Asia/Tomsk'

    def minutes_for(self, session_type: str) -> int:
        return {
            'lecture': self.lecture_minutes,
            'practical': self.practical_minutes,
            'lab': self.lab_minutes,
        }.get(session_type, 0)

    def preparation_window(self, event_start: datetime) -> Tuple[datetime, datetime]:
        """Return the allowed window for a lesson's academic day.

        All lessons on one date share a 06:00 anchor.  A preparation block
        must fit entirely between ``anchor - preparation_window_days`` and the
        anchor itself; it is never placed after 06:00 on that date.  The final
        ``preparation_lead_hours`` are marked urgent, rather than becoming a
        separate hard deadline.
        """
        anchor = event_start.replace(hour=6, minute=0, second=0, microsecond=0)
        return anchor - timedelta(days=self.preparation_window_days), anchor

    def planning_horizon_end(self, now: datetime) -> datetime:
        """Return midnight after the next calendar week."""
        week_start = (
            now - timedelta(days=now.weekday())
        ).replace(hour=0, minute=0, second=0, microsecond=0)
        return week_start + timedelta(weeks=max(1, self.planning_horizon_weeks))


@dataclass(frozen=True)
class _PreparationGroup:
    """One attended session and its own preparation requirement."""

    id: str
    course: str
    anchor: datetime
    events: Tuple[PersonalUniversityEvent, ...]
    session_types: Tuple[str, ...]
    minutes: int
    priority: int

    @property
    def title(self) -> str:
        labels = {
            'lecture': 'ЛК',
            'practical': 'ПР',
            'lab': 'ЛБ',
        }
        rendered = labels[self.session_types[0]]
        event = self.events[0]
        return f'{self.course} — к {rendered} {event.start_time:%d.%m %H:%M}'


@dataclass(frozen=True)
class DraftPreparationBlock:
    id: str
    source_event_id: str
    title: str
    start: datetime
    end: datetime
    minutes: int
    reason: str
    calendar: CalendarRoute = CalendarRoute.STUDY
    color: str = 'purple'
    status: str = 'draft'
    manual_conflict: bool = False
    session_type: str = ''


@dataclass
class DraftOperation:
    id: str
    blocks: List[DraftPreparationBlock]
    version: int = 1
    content_hash: str = ''
    previous_blocks: List[DraftPreparationBlock] = field(default_factory=list)
    status: str = 'draft'
    explanations: List[str] = field(default_factory=list)
    calendar_event_ids: Dict[str, str] = field(default_factory=dict)
    created_calendar_block_ids: List[str] = field(default_factory=list)
    previous_calendar_event_ids: Dict[str, str] = field(default_factory=dict)
    calendar_id: Optional[str] = None
    completed_source_event_ids: List[str] = field(default_factory=list)
    carryover_minutes_by_course: Dict[str, int] = field(default_factory=dict)
    feedback_recorded_block_ids: List[str] = field(default_factory=list)
    # Operations used to have exactly one Calendar.  Keep ``calendar_id`` for
    # old snapshots, but record the actual target per route from now on.
    calendar_ids: Dict[str, str] = field(default_factory=dict)
    scope: str = 'university-preparation'
    projection_pending: bool = False
    # Published blocks omitted by a new calculation are not cancellation
    # evidence. Keep ownership until a source-aware decision removes them.
    retained_blocks: List[DraftPreparationBlock] = field(default_factory=list)
    retired_blocks: List[DraftPreparationBlock] = field(default_factory=list)
    no_slot_reasons: Dict[str, str] = field(default_factory=dict)
    # Calendar is authoritative for these explicit user actions. Values are
    # ISO timestamps captured from the owned remote event, never user text.
    manual_calendar_overrides: Dict[str, Dict[str, str]] = field(default_factory=dict)
    manually_deleted_block_ids: List[str] = field(default_factory=list)
    # Written before a provider mutation and cleared only after verification.
    pending_calendar_writes: Dict[str, str] = field(default_factory=dict)
    updated_calendar_block_ids: List[str] = field(default_factory=list)
    # A feedback-driven change stays inert until the user explicitly applies
    # or rejects it. It contains enum-like values and durations only.
    pending_feedback_proposal: Dict[str, object] = field(default_factory=dict)


class AdaptivePreparationService:
    """Creates preparation blocks only for personally attended sessions."""

    def __init__(self, profile: Optional[UserPlanningProfile] = None):
        self.profile = profile or UserPlanningProfile()

    def build_draft(
        self,
        personal_events: Iterable[PersonalUniversityEvent],
        now: Optional[datetime] = None,
        fixed_commitments: Iterable[FixedCommitment] = (),
        planning_engine: Optional[PlanningEngine] = None,
        flexible_items: Iterable[PlanningItem] = (),
        completed_source_event_ids: Iterable[str] = (),
        carryover_minutes_by_course: Optional[Dict[str, int]] = None,
    ) -> DraftOperation:
        now = now or datetime.now()
        events = list(personal_events)
        fixed_commitments = list(fixed_commitments)
        flexible_items = list(flexible_items)
        now, events, fixed_commitments, flexible_items = self._normalise_datetimes(
            now, events, fixed_commitments, flexible_items,
        )
        return self._build_with_planning_engine(
            events, now, fixed_commitments, planning_engine, flexible_items,
            set(completed_source_event_ids), carryover_minutes_by_course or {},
        )

    def _normalise_datetimes(
        self,
        now: datetime,
        events: List[PersonalUniversityEvent],
        commitments: List[FixedCommitment],
        items: List[PlanningItem],
    ) -> tuple[
        datetime, List[PersonalUniversityEvent], List[FixedCommitment], List[PlanningItem],
    ]:
        """Use one timeline whenever an ICS source provides timezone-aware dates.

        Naive values are interpreted as the user's local planning timezone.  A
        fully naive call remains naive for backwards-compatible domain usage.
        """
        values = [now]
        values.extend(value for event in events for value in (event.start_time, event.end_time))
        values.extend(value for commitment in commitments for value in (
            commitment.start, commitment.end,
        ))
        values.extend(
            value for item in items for value in (
                item.preferred_start, item.preferred_end,
                item.earliest_start, item.latest_end,
            ) if value is not None
        )
        if not any(value.tzinfo is not None for value in values):
            return now, events, commitments, items
        timezone = ZoneInfo(self.profile.timezone)

        def normalise(value: Optional[datetime]) -> Optional[datetime]:
            if value is None:
                return None
            return value.astimezone(timezone) if value.tzinfo else value.replace(tzinfo=timezone)

        return (
            normalise(now),
            [replace(event, start_time=normalise(event.start_time), end_time=normalise(event.end_time))
             for event in events],
            [replace(commitment, start=normalise(commitment.start), end=normalise(commitment.end))
             for commitment in commitments],
            [replace(
                item,
                preferred_start=normalise(item.preferred_start),
                preferred_end=normalise(item.preferred_end),
                earliest_start=normalise(item.earliest_start),
                latest_end=normalise(item.latest_end),
            ) for item in items],
        )

    def _build_with_planning_engine(
        self,
        personal_events: List[PersonalUniversityEvent],
        now: datetime,
        fixed_commitments: List[FixedCommitment],
        planning_engine: Optional[PlanningEngine],
        flexible_items: List[PlanningItem],
        completed_source_event_ids: set[str],
        carryover_minutes_by_course: Dict[str, int],
    ) -> DraftOperation:
        """Place independent preparation requirements into weekly windows."""
        # A preparation requirement needs attendance confirmation.  Reserving
        # a university day does not: a gap between any currently published
        # TPU classes is campus time, even when the student chose not to
        # attend one particular class.  Otherwise excluded classes vanish
        # from the scheduler and it puts preparation between lessons.
        campus_events = [
            event for event in personal_events
            if event.state != PersonalEventState.CANCELLED
            and str(event.metadata.get('source_status', '')).casefold() != 'cancelled'
        ]
        active_events = [
            event for event in personal_events
            if event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
            and self.profile.minutes_for(str(event.metadata.get('session_type', '')))
            and event.id not in completed_source_event_ids
        ]
        horizon_limit = self.profile.planning_horizon_end(now)
        groups = [
            group for group in self._preparation_groups(active_events)
            if self.profile.preparation_window(group.anchor)[1] <= horizon_limit
        ]
        groups.extend(self._carryover_groups(
            personal_events, carryover_minutes_by_course, now,
        ))
        groups = [
            group for group in groups
            if self.profile.preparation_window(group.anchor)[1] <= horizon_limit
        ]
        if not groups:
            return DraftOperation(id=str(uuid.uuid4()), blocks=[])

        windows = {
            group.id: self.profile.preparation_window(group.anchor)
            for group in groups
        }
        expired = [group for group in groups if windows[group.id][1] <= now]
        groups = [group for group in groups if windows[group.id][1] > now]
        no_slot_reasons = {group.events[0].id: 'допустимое окно подготовки уже прошло'
                           for group in expired}
        past_expired = [group for group in expired if group.events[0].start_time <= now]
        expired = [group for group in expired if group.events[0].start_time > now]
        explanations = [
            f'{group.title}: подготовка не создана — окно до 06:00 '
            f'{windows[group.id][1]:%d.%m} уже прошло.'
            for group in past_expired
        ]
        blocks = []
        for group in expired:
            block = self._manual_conflict_block(
                group, now, windows[group.id], no_slot_reasons[group.events[0].id],
            )
            if block is None:
                explanations.append(
                    f'{group.title}: подготовка не создана — даже конфликтный блок '
                    'уже не помещается до занятия.'
                )
            else:
                blocks.append(block)
                explanations.append(block.reason)
        if not groups:
            return DraftOperation(
                id=str(uuid.uuid4()), blocks=blocks,
                content_hash=self._operation_hash(blocks), explanations=explanations,
                no_slot_reasons=no_slot_reasons,
            )

        # Telegram preview needs a bounded response time. Candidate comparison
        # remains part of the weekly-plan pipeline; preparation drafts use one
        # deterministic greedy pass on the precise five-minute grid.
        engine = planning_engine or PlanningEngine(
            num_candidates=1, strategy_weights={'greedy': 1.0},
        )
        for constraint in engine.constraints:
            if isinstance(constraint, MaxContinuousWorkConstraint):
                constraint.max_continuous_work_minutes = (
                    self.profile.max_continuous_deep_work_minutes
                )

        # TPU lesson times are commonly :25/:35.  A five-minute grid and a
        # floored horizon can represent them exactly, unlike a 15-minute grid
        # anchored at the current second.
        grid_start = now.replace(
            minute=now.minute - now.minute % 5, second=0, microsecond=0,
        )
        horizon_end = max(windows[group.id][1] for group in groups)
        relevant_fixed = [
            commitment for commitment in fixed_commitments
            if commitment.end > grid_start and commitment.start < horizon_end
        ]
        horizon_start = min([
            grid_start, *(commitment.start for commitment in relevant_fixed),
        ])
        all_commitments = [
            *relevant_fixed,
            *self._university_commitments(campus_events, horizon_start, horizon_end),
        ]
        items = [commitment.to_planning_item() for commitment in all_commitments]
        items.extend(flexible_items)
        request_ids: Dict[str, _PreparationGroup] = {}
        for group in groups:
            window_start, window_end = windows[group.id]
            request_id = f'draft-preparation:{group.id}'
            request_ids[request_id] = group
            earliest_start = max(now, window_start)
            urgent = window_end - now <= timedelta(hours=self.profile.preparation_lead_hours)
            items.append(PlanningItem(
                id=request_id,
                title=f'Подготовка: {group.title}',
                description='Автоматически созданный черновик подготовки',
                item_type=PlanningItemType.PREPARATION_BLOCK,
                preferred_start=self._preferred_start(
                    earliest_start, window_end, group.minutes,
                ),
                preferred_end=self._preferred_start(
                    earliest_start, window_end, group.minutes,
                ) + timedelta(minutes=group.minutes),
                duration_minutes=group.minutes,
                earliest_start=earliest_start,
                latest_end=window_end,
                flexible=True,
                priority=self._planning_priority(group, now),
                metadata={
                    'is_preparation': True,
                    'source_event_id': group.events[0].id,
                    'source_event_ids': [event.id for event in group.events],
                    'deep_work': group.minutes >= 60,
                    'cognitive_load': 'medium',
                    'urgent': urgent,
                    'session_type': group.session_types[0],
                },
            ))
        schedule = engine.schedule_items(items, horizon_start, horizon_end, 5)
        slots_by_item: Dict[str, List] = {}
        for slot in schedule.slots:
            if slot.scheduled_item_id in request_ids:
                slots_by_item.setdefault(slot.scheduled_item_id, []).append(slot)

        for request_id, group in request_ids.items():
            slots = slots_by_item.get(request_id, [])
            window_start, window_end = windows[group.id]
            urgent = window_end - now <= timedelta(hours=self.profile.preparation_lead_hours)
            if not slots:
                no_slot_reasons[group.events[0].id] = 'нет свободного слота в допустимом окне с учётом обязательств'
                block = self._manual_conflict_block(
                    group, now, (window_start, window_end),
                    no_slot_reasons[group.events[0].id],
                )
                if block is None:
                    explanations.append(
                        f'{group.title}: подготовка не создана — даже конфликтный блок '
                        'уже не помещается до занятия.'
                    )
                else:
                    blocks.append(block)
                    explanations.append(block.reason)
                continue
            start, end = min(slot.start for slot in slots), max(slot.end for slot in slots)
            if int((end - start).total_seconds() / 60) != group.minutes:
                no_slot_reasons[group.events[0].id] = 'нет непрерывного слота нужной длительности'
                block = self._manual_conflict_block(
                    group, now, (window_start, window_end),
                    no_slot_reasons[group.events[0].id],
                )
                if block is None:
                    explanations.append(
                        f'{group.title}: подготовка не создана — даже конфликтный блок '
                        'уже не помещается до занятия.'
                    )
                else:
                    blocks.append(block)
                    explanations.append(block.reason)
                continue
            if self._overlaps_university_day(start, end, campus_events):
                no_slot_reasons[group.events[0].id] = 'найденный слот пересекает университетский день'
                block = self._manual_conflict_block(
                    group, now, (window_start, window_end),
                    no_slot_reasons[group.events[0].id],
                )
                if block is None:
                    explanations.append(
                        f'{group.title}: подготовка не создана — даже конфликтный блок '
                        'уже не помещается до занятия.'
                    )
                else:
                    blocks.append(block)
                    explanations.append(block.reason)
                continue
            block = DraftPreparationBlock(
                id=self._block_id(group.id, start, end),
                source_event_id=group.events[0].id,
                title=f'Подготовка: {group.title}',
                start=start,
                end=end,
                minutes=group.minutes,
                reason=(
                    f'Свободный слот найден PlanningEngine: {start:%d.%m %H:%M}–'
                    f'{end:%H:%M}; окно подготовки {window_start:%d.%m}–'
                    f'06:00 {window_end:%d.%m}. '
                    + ('Срочная подготовка: до занятия осталось менее 36 ч.' if urgent else
                       'Выбрано раннее доступное время до занятия.')
                ),
                session_type=group.session_types[0],
            )
            blocks.append(block)
            explanations.append(block.reason)
        requested_ids = set(request_ids)
        for item in schedule.unscheduled_items:
            if item.id not in requested_ids and item.flexible:
                explanations.append(
                    f'«{item.title}» перенесено: более приоритетная подготовка '
                    'заняла единственный допустимый слот; сон и fixed commitments не менялись.'
                )
        return DraftOperation(
            id=str(uuid.uuid4()), blocks=blocks,
            content_hash=self._operation_hash(blocks), explanations=explanations,
            no_slot_reasons=no_slot_reasons,
            completed_source_event_ids=sorted(completed_source_event_ids),
            carryover_minutes_by_course=dict(carryover_minutes_by_course),
        )

    def _manual_conflict_block(
        self,
        group: _PreparationGroup,
        now: datetime,
        window: Tuple[datetime, datetime],
        no_slot_reason: str,
    ) -> Optional[DraftPreparationBlock]:
        """Create a visible marker without treating a conflict as free time."""
        window_start, window_end = window
        minutes = min(group.minutes, self.profile.max_single_block_minutes)
        # A visible conflict proposal is still preparation for this exact
        # lesson.  Once its full duration cannot finish before the lesson,
        # placing a marker afterwards would falsely imply useful work is
        # possible. Leave it unplaced and let the integrity report surface
        # the proven reason instead.
        if now + timedelta(minutes=minutes) > group.events[0].start_time:
            return None
        preferred = self._preferred_start(max(now, window_start), window_end, minutes)
        within_window = (
            preferred >= now
            and preferred + timedelta(minutes=minutes) <= window_end
        )
        start = preferred if within_window else self._next_five_minutes(now)
        end = start + timedelta(minutes=minutes)
        return DraftPreparationBlock(
            id=self._block_id(group.id, start, end, manual_conflict=True),
            source_event_id=group.events[0].id,
            title=f'Подготовка: {group.title}',
            start=start,
            end=end,
            minutes=minutes,
            reason=(
                'Конфликт — перенести вручную. '
                f'Причина: {no_slot_reason}. '
                + ('Окно подготовки уже прошло; маркер поставлен на ближайшую будущую '
                   'пятиминутную сетку для ручного переноса.' if not within_window else
                   'Свободный слот не найден; маркер поставлен в предпочтительное время '
                   'в допустимом окне без переноса обязательств.')
            ),
            manual_conflict=True,
            session_type=group.session_types[0],
        )

    @staticmethod
    def _next_five_minutes(value: datetime) -> datetime:
        candidate = value.replace(second=0, microsecond=0)
        candidate += timedelta(minutes=(-candidate.minute) % 5)
        if candidate < value:
            candidate += timedelta(minutes=5)
        return candidate

    def _university_commitments(
        self,
        events: Iterable[PersonalUniversityEvent],
        horizon_start: datetime,
        horizon_end: datetime,
    ) -> List[FixedCommitment]:
        commitments: List[FixedCommitment] = []
        events_by_day: Dict[object, List[PersonalUniversityEvent]] = {}

        def add_fixed(
            identifier: str,
            title: str,
            start: datetime,
            end: datetime,
            commitment_type: CommitmentType,
        ) -> None:
            """Reserve the intersection with the actual planning horizon."""
            start, end = max(start, horizon_start), min(end, horizon_end)
            if end > start:
                commitments.append(FixedCommitment(
                    id=identifier, title=title, start=start, end=end,
                    commitment_type=commitment_type,
                ))

        for event in events:
            if event.end_time <= horizon_start or event.start_time >= horizon_end:
                continue
            events_by_day.setdefault(event.start_time.date(), []).append(event)

        # A commute belongs to the university day, not to every individual
        # class.  It prevents double-counting and makes consecutive lessons
        # realistic: commute before the first, commute after the last, then
        # the required recovery hour.
        for day_events in events_by_day.values():
            first_start = min(event.start_time for event in day_events)
            last_end = max(event.end_time for event in day_events)
            day_key = first_start.date().isoformat()
            travel = timedelta(minutes=self.profile.travel_minutes_each_way)
            recovery = timedelta(minutes=self.profile.recovery_minutes_after_university)
            # Breaks between classes are campus time, not automatic deep-work
            # availability. Preparations may be placed only before the first
            # commute or after the final class, commute and recovery.
            add_fixed(
                f'university-day:{day_key}', 'Университетский день',
                first_start, last_end, CommitmentType.UNIVERSITY,
            )
            add_fixed(
                f'travel:to-university:{day_key}', 'Дорога в университет',
                first_start - travel, first_start, CommitmentType.TRAVEL,
            )
            add_fixed(
                f'travel:from-university:{day_key}', 'Дорога из университета',
                last_end, last_end + travel, CommitmentType.TRAVEL,
            )
            add_fixed(
                f'recovery:university:{day_key}', 'Восстановление после университета',
                last_end + travel, last_end + travel + recovery,
                CommitmentType.RECOVERY,
            )
        return commitments

    @staticmethod
    def _overlaps_university_day(
        start: datetime,
        end: datetime,
        events: Iterable[PersonalUniversityEvent],
    ) -> bool:
        """Final invariant: no preparation may use a gap between classes."""
        same_day = [event for event in events if event.start_time.date() == start.date()]
        if not same_day:
            return False
        campus_start = min(event.start_time for event in same_day)
        campus_end = max(event.end_time for event in same_day)
        return start < campus_end and end > campus_start

    @staticmethod
    def _priority_for(event: PersonalUniversityEvent) -> int:
        return {'lab': 5, 'practical': 4, 'lecture': 2}.get(
            str(event.metadata.get('session_type', '')), 1,
        )

    def _planning_priority(self, group: _PreparationGroup, now: datetime) -> int:
        """Nearest academic day wins; session type breaks only an exact tie."""
        minutes_until_anchor = max(
            0, int((group.anchor - now).total_seconds() // 60),
        )
        return 1_000_000 - minutes_until_anchor * 10 + group.priority

    def _preferred_start(
        self, earliest: datetime, latest_end: datetime, minutes: int,
    ) -> datetime:
        """Prefer Saturday after 10:00, otherwise the earliest legal moment."""
        candidate = earliest.replace(second=0, microsecond=0)
        candidate += timedelta(minutes=(-candidate.minute) % 5)
        if candidate < earliest:
            candidate += timedelta(minutes=5)
        cursor = candidate
        while cursor + timedelta(minutes=minutes) <= latest_end:
            if cursor.weekday() == 5:
                saturday_start = max(candidate, cursor.replace(hour=10, minute=0))
                if saturday_start + timedelta(minutes=minutes) <= latest_end:
                    return saturday_start
            cursor += timedelta(days=1)
            cursor = cursor.replace(hour=0, minute=0)
        return candidate

    def _preparation_groups(
        self, events: Iterable[PersonalUniversityEvent],
    ) -> List[_PreparationGroup]:
        """Return one requirement per attended event/session type.

        Lecture, practical and lab of the same course intentionally remain
        separate blocks: they represent different preparation work and have
        different target sessions.
        """
        result = []
        for event in events:
            if event.state not in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}:
                continue
            session_type = str(event.metadata.get('session_type', ''))
            if not self.profile.minutes_for(session_type):
                continue
            course = str(event.metadata.get('course') or event.title.split('(')[0]).strip()
            anchor = event.start_time.replace(hour=6, minute=0, second=0, microsecond=0)
            # Personal-event IDs survive source moves; preserving this ID makes
            # the Google projection an update rather than a duplicate.
            payload = event.id
            result.append(_PreparationGroup(
                id=f'prep-group:{sha256(payload.encode()).hexdigest()[:20]}',
                course=course, anchor=anchor, events=(event,),
                session_types=(session_type,),
                minutes=self.profile.minutes_for(session_type),
                priority=self._priority_for(event),
            ))
        return sorted(result, key=lambda group: (
            group.anchor, -group.priority, group.course.casefold(), group.id,
        ))

    def _carryover_groups(
        self,
        all_events: Iterable[PersonalUniversityEvent],
        carryovers: Dict[str, int],
        now: datetime,
    ) -> List[_PreparationGroup]:
        """Attach unfinished work to the nearest future attended session."""
        result = []
        for course_key, pending_minutes in carryovers.items():
            if pending_minutes <= 0:
                continue
            matching = [
                event for event in all_events
                if event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
                and str(event.metadata.get('course') or event.title.split('(')[0]).strip().casefold()
                == course_key.casefold()
                and event.start_time > now
            ]
            if not matching:
                continue
            target = min(matching, key=lambda event: event.start_time)
            session_type = str(target.metadata.get('session_type', 'practical'))
            course = str(target.metadata.get('course') or target.title.split('(')[0]).strip()
            minutes = min(pending_minutes, self.profile.max_single_block_minutes)
            result.append(_PreparationGroup(
                id=f'prep-carryover:{sha256(course_key.casefold().encode()).hexdigest()[:20]}',
                course=f'{course} — остаток',
                anchor=target.start_time.replace(hour=6, minute=0, second=0, microsecond=0),
                events=(target,),
                session_types=(session_type,),
                minutes=minutes,
                priority=self._priority_for(target) + 10,
            ))
        return result

    @staticmethod
    def _block_id(
        event_id: str, start: datetime, end: datetime, manual_conflict: bool = False,
    ) -> str:
        """Return a placement identity; conflict status is not identity data."""
        del manual_conflict
        payload = f'{event_id}|{start.isoformat()}|{end.isoformat()}'
        return f'prep:{sha256(payload.encode()).hexdigest()[:20]}'

    @staticmethod
    def _operation_hash(blocks: Iterable[DraftPreparationBlock]) -> str:
        payload = '\n'.join(
            f'{block.id}|{block.start.isoformat()}|{block.end.isoformat()}|{block.status}|'
            f'{is_manual_conflict(block)}'
            for block in blocks
        )
        return sha256(payload.encode()).hexdigest()


class DraftPlanSyncService:
    """Owns draft state, confirmation and reversible rollback snapshots."""

    VERSION = 1

    def __init__(self):
        self.operations: Dict[str, DraftOperation] = {}
        self.current_blocks: Dict[str, DraftPreparationBlock] = {}
        self.calendar_event_ids: Dict[str, str] = {}

    def stage(self, operation: DraftOperation) -> DraftOperation:
        replaced = {
            current.id: current
            for block in operation.blocks
            for current in self.current_blocks.values()
            if current.id == block.id or current.source_event_id == block.source_event_id
        }
        operation.previous_blocks = list(replaced.values())
        operation.previous_calendar_event_ids = {
            block_id: self.calendar_event_ids[block_id]
            for block_id in replaced
            if block_id in self.calendar_event_ids
        }
        for block_id in replaced:
            self.current_blocks.pop(block_id, None)
        for block in operation.blocks:
            self.current_blocks[block.id] = block
        self.operations[operation.id] = operation
        return operation

    def confirm(self, operation_id: str) -> DraftOperation:
        operation = self._operation(operation_id)
        if operation.status != 'draft':
            raise ValueError('Only a draft operation can be confirmed')
        operation.status = 'confirmed'
        for block in operation.blocks:
            self.current_blocks[block.id] = DraftPreparationBlock(
                **{**block.__dict__, 'status': 'confirmed'}
            )
        return operation

    def rollback(self, operation_id: str) -> DraftOperation:
        operation = self._operation(operation_id)
        if operation.status == 'rolled_back':
            return operation
        for block in operation.blocks:
            self.current_blocks.pop(block.id, None)
            self.calendar_event_ids.pop(block.id, None)
        for block in operation.previous_blocks:
            if any(retired.source_event_id == block.source_event_id
                   for retired in operation.retired_blocks):
                continue
            self.current_blocks[block.id] = block
            if block.id in operation.previous_calendar_event_ids:
                self.calendar_event_ids[block.id] = operation.previous_calendar_event_ids[block.id]
        operation.status = 'rolled_back'
        return operation

    def remove_missing_sources(self, active_source_event_ids: Iterable[str]) -> Dict[str, str]:
        """Drop only owned blocks whose university source no longer exists."""
        active = set(active_source_event_ids)
        removed: Dict[str, str] = {}
        for block_id, block in list(self.current_blocks.items()):
            if block.source_event_id in active:
                continue
            self.current_blocks.pop(block_id, None)
            event_id = self.calendar_event_ids.pop(block_id, None)
            if event_id:
                removed[block_id] = event_id
        return removed

    def preview(self, operation_id: str) -> str:
        operation = self._operation(operation_id)
        lines = ['Черновик подготовок:']
        lines.extend(
            f'• {block.start:%d.%m %H:%M}–{block.end:%H:%M}: {block.title}'
            for block in operation.blocks
        )
        return '\n'.join(lines + ['', *operation.explanations])

    def _operation(self, operation_id: str) -> DraftOperation:
        if operation_id not in self.operations:
            raise KeyError(f'Unknown draft operation: {operation_id}')
        return self.operations[operation_id]


class DraftCalendarProjector:
    """Projects only system-owned draft blocks and reverses them safely."""

    def __init__(
        self,
        calendar_adapter,
        calendar_name: str = PERSONAL_UNIVERSITY_CALENDAR,
    ):
        self.calendar_adapter = calendar_adapter
        self.calendar_name = calendar_name

    def _calendar_name_for(self, block: DraftPreparationBlock) -> str:
        if block.calendar == CalendarRoute.WORK:
            return 'Работа'
        if block.calendar == CalendarRoute.PERSONAL:
            return 'Личное'
        if block.calendar == CalendarRoute.SLEEP:
            return 'Сон'
        return self.calendar_name

    def _calendar_id_for(self, operation: DraftOperation, block: DraftPreparationBlock) -> str:
        route = block.calendar.value
        calendar_id = operation.calendar_ids.get(route)
        if calendar_id:
            return calendar_id
        calendar_id = self.calendar_adapter._get_or_create_calendar(
            self._calendar_name_for(block)
        )
        operation.calendar_ids[route] = calendar_id
        # Legacy readers still use this field for the university operation.
        if block.calendar == CalendarRoute.STUDY:
            operation.calendar_id = calendar_id
        return calendar_id

    def stage(self, operation: DraftOperation, checkpoint=None) -> DraftOperation:
        for block in operation.blocks:
            calendar_id = self._calendar_id_for(operation, block)
            event_data = self._event_data(block, operation)
            remote = self._owned_remote_event(calendar_id, block.id)
            if remote and not self._owns_remote_event(remote, block.id, operation):
                raise RuntimeError('Calendar: владелец события не подтверждён; запись остановлена.')
            existing_id = remote.get('id') if remote else None
            if existing_id:
                previous_block = next((item for item in operation.previous_blocks
                                       if item.id == block.id), None)
                baseline = previous_block or block
                if self._has_manual_time_override(remote, baseline):
                    operation.manual_calendar_overrides[block.id] = {
                        'start': str(remote['start']['dateTime']),
                        'end': str(remote['end']['dateTime']),
                    }
                    operation.calendar_event_ids[block.id] = existing_id
                    if checkpoint is not None:
                        checkpoint(operation)
                    continue
                if self._matches_projection(remote, event_data):
                    event_id = existing_id
                else:
                    event_id = self._write_verified(operation, block, calendar_id,
                                                    event_data, 'update', existing_id, checkpoint)
                # A failed update can mean a transport error. It is not
                # evidence that the event vanished and must not trigger insert.
            else:
                # A previously published, owned block disappearing from
                # Calendar is a user's deletion unless an explicit replan
                # authorizes recreating it. Never restore it blindly.
                if block.id in operation.calendar_event_ids:
                    if block.id not in operation.manually_deleted_block_ids:
                        operation.manually_deleted_block_ids.append(block.id)
                    if checkpoint is not None:
                        checkpoint(operation)
                    continue
                previous_id = next(
                    (
                        operation.previous_calendar_event_ids.get(previous.id)
                        for previous in operation.previous_blocks
                        if previous.source_event_id == block.source_event_id
                    ),
                    None,
                )
                if previous_id:
                    previous_remote = self._remote_event_by_id(calendar_id, previous_id)
                    if (callable(getattr(self.calendar_adapter, 'get_event_by_id', None))
                            and (previous_remote is None
                                 or not self._owns_previous_event(previous_remote, operation))):
                        raise RuntimeError('Calendar: владелец прежнего события не подтверждён; запись остановлена.')
                    event_id = self._write_verified(operation, block, calendar_id,
                                                    event_data, 'update', previous_id, checkpoint)
                    if block.id not in operation.updated_calendar_block_ids:
                        operation.updated_calendar_block_ids.append(block.id)
                else:
                    event_id = self._write_verified(operation, block, calendar_id,
                                                    event_data, 'insert', None, checkpoint)
                if event_id and not previous_id:
                    if block.id not in operation.created_calendar_block_ids:
                        operation.created_calendar_block_ids.append(block.id)
            if not event_id:
                raise RuntimeError('Calendar: запись подготовки не подтверждена; повтори действие для продолжения синхронизации.')
            operation.calendar_event_ids[block.id] = event_id
            if checkpoint is not None:
                checkpoint(operation)
        return operation

    def confirm(self, operation: DraftOperation, checkpoint=None) -> DraftOperation:
        for block in operation.blocks:
            if (block.id in operation.manual_calendar_overrides
                    or block.id in operation.manually_deleted_block_ids):
                continue
            event_id = operation.calendar_event_ids.get(block.id)
            if event_id:
                calendar_id = self._calendar_id_for(operation, block)
                confirmed = DraftPreparationBlock(
                    **{**block.__dict__, 'status': 'confirmed'}
                )
                reader = getattr(self.calendar_adapter, 'get_event_by_uid', None)
                try:
                    raw = reader(calendar_id, block.id, strict=True) if callable(reader) else None
                except Exception:
                    raise RuntimeError('Calendar: чтение не подтверждено; синхронизация остановлена.') from None
                reliable = callable(reader) and (raw is None or isinstance(raw, dict))
                remote = raw if isinstance(raw, dict) else None
                if reliable and remote is None:
                    if block.id not in operation.manually_deleted_block_ids:
                        operation.manually_deleted_block_ids.append(block.id)
                    if checkpoint is not None:
                        checkpoint(operation)
                    continue
                if remote:
                    if (remote.get('id') != event_id
                            or not self._owns_remote_event(remote, block.id, operation)):
                        raise RuntimeError('Calendar: владелец события не подтверждён; запись остановлена.')
                    if self._has_manual_time_override(remote, block):
                        operation.manual_calendar_overrides[block.id] = {
                            'start': str(remote['start']['dateTime']),
                            'end': str(remote['end']['dateTime']),
                        }
                        if checkpoint is not None:
                            checkpoint(operation)
                        continue
                    if self._matches_projection(remote, self._event_data(confirmed, operation)):
                        continue
                self._write_verified(operation, confirmed, calendar_id,
                                     self._event_data(confirmed, operation),
                                     'update', event_id, checkpoint)
            else:
                raise RuntimeError('Сначала заверши публикацию всех подготовок.')
        return operation

    def capture_manual_actions(self, operation: DraftOperation, checkpoint=None) -> bool:
        """Persist a remote manual move/delete before a new plan is calculated.

        This performs strict reads only. A failed Calendar read propagates to
        the caller rather than being misclassified as a deletion.
        """
        reader = getattr(self.calendar_adapter, 'get_event_by_uid', None)
        if not callable(reader):
            return False
        changed = False
        for block in [*operation.blocks, *operation.retained_blocks]:
            event_id = operation.calendar_event_ids.get(block.id)
            if not event_id:
                continue
            calendar_id = operation.calendar_ids.get(block.calendar.value)
            if not calendar_id and block.calendar == CalendarRoute.STUDY:
                calendar_id = operation.calendar_id
            if not calendar_id:
                continue
            try:
                event = reader(calendar_id, block.id, strict=True)
            except Exception:
                raise RuntimeError('Calendar: чтение не подтверждено; синхронизация остановлена.') from None
            if event is None:
                if block.id not in operation.manually_deleted_block_ids:
                    operation.manually_deleted_block_ids.append(block.id)
                    changed = True
                continue
            if not self._owns_remote_event(event, block.id, operation):
                raise RuntimeError('Calendar: владелец события не подтверждён; запись остановлена.')
            if self._has_manual_time_override(event if isinstance(event, dict) else None, block):
                override = {
                    'start': str(event['start']['dateTime']),
                    'end': str(event['end']['dateTime']),
                }
                if operation.manual_calendar_overrides.get(block.id) != override:
                    operation.manual_calendar_overrides[block.id] = override
                    changed = True
        if changed and checkpoint is not None:
            checkpoint(operation)
        return changed

    def _owned_remote_event(self, calendar_id: str, block_id: str) -> Optional[dict]:
        """Read an owned event when the adapter can provide it."""
        reader = getattr(self.calendar_adapter, 'get_event_by_uid', None)
        if not callable(reader):
            return None
        try:
            event = reader(calendar_id, block_id, strict=True)
        except TypeError:
            try:
                event = reader(calendar_id, block_id)
            except Exception:
                raise RuntimeError('Calendar: чтение не подтверждено; синхронизация остановлена.') from None
        except Exception:
            raise RuntimeError('Calendar: чтение не подтверждено; синхронизация остановлена.') from None
        return event if isinstance(event, dict) else None

    def _remote_event_by_id(self, calendar_id: str, event_id: str) -> Optional[dict]:
        reader = getattr(self.calendar_adapter, 'get_event_by_id', None)
        if not callable(reader):
            return None
        try:
            event = reader(calendar_id, event_id, strict=True)
        except Exception:
            raise RuntimeError('Calendar: чтение не подтверждено; синхронизация остановлена.') from None
        return event if isinstance(event, dict) else None

    @staticmethod
    def _owns_previous_event(event: dict, operation: DraftOperation) -> bool:
        for block in operation.previous_blocks:
            if event.get('id') != operation.previous_calendar_event_ids.get(block.id):
                continue
            private = event.get('extendedProperties', {}).get('private', {})
            if private.get('personal_os_block_id') == block.id:
                return True
            if f'AI Calendar Block: {block.id}\n' in str(event.get('description', '')):
                return True
        return False

    @staticmethod
    def _owns_remote_event(event: dict, block_id: str, operation: DraftOperation) -> bool:
        private = event.get('extendedProperties', {}).get('private', {})
        if private.get('personal_os_block_id') == block_id:
            return True
        # Older projections have no private marker. A locally persisted ID
        # together with the exact block marker is sufficient for migration.
        if (event.get('id') == operation.calendar_event_ids.get(block_id)
                and f'AI Calendar Block: {block_id}\n' in str(event.get('description', ''))):
            return True
        return False

    @staticmethod
    def _matches_projection(event: Optional[dict], desired) -> bool:
        if not event or not event.get('id'):
            return False
        private = event.get('extendedProperties', {}).get('private', {})
        if private.get('personal_os_block_id') != desired.system_block_id:
            return False
        if private.get('personal_os_operation_id') != desired.system_operation_id:
            return False
        try:
            start = datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
        except (KeyError, TypeError, ValueError):
            return False
        return (start == desired.dtstart and end == desired.dtend
                and event.get('summary') == desired.summary
                and event.get('description') == desired.description)

    def _write_verified(self, operation, block, calendar_id, desired, action,
                        event_id, checkpoint):
        from services.sync_retry import transient_error

        operation.pending_calendar_writes[block.id] = action
        if checkpoint is not None:
            checkpoint(operation)
        for attempt in range(3):
            try:
                if action == 'insert':
                    result = self.calendar_adapter._insert_event(desired, calendar_id, strict=True)
                else:
                    result = self.calendar_adapter._update_event(calendar_id, event_id, desired, strict=True)
                if result:
                    operation.pending_calendar_writes.pop(block.id, None)
                    return result
                error = None
            except TypeError as exc:
                # Existing lightweight adapters have no strict keyword.
                if 'strict' not in str(exc):
                    raise
                try:
                    result = (self.calendar_adapter._insert_event(desired, calendar_id)
                              if action == 'insert' else
                              self.calendar_adapter._update_event(calendar_id, event_id, desired))
                    if result:
                        operation.pending_calendar_writes.pop(block.id, None)
                        return result
                except Exception as nested:
                    error = nested
                else:
                    error = None
            except Exception as exc:
                error = exc
            # A timeout can follow a successful provider write. Verify before
            # another mutation, and fail closed when verification is uncertain.
            remote = self._owned_remote_event(calendar_id, block.id)
            if self._matches_projection(remote, desired):
                operation.pending_calendar_writes.pop(block.id, None)
                return remote['id']
            if error is None or not transient_error(error) or attempt == 2:
                raise RuntimeError('Calendar: запись не подтверждена; повтори синхронизацию.') from None
        raise RuntimeError('Calendar: запись не подтверждена; повтори синхронизацию.')

    @staticmethod
    def _has_manual_time_override(event: Optional[dict], block: DraftPreparationBlock) -> bool:
        if not event:
            return False
        try:
            start = datetime.fromisoformat(str(event['start']['dateTime']).replace('Z', '+00:00'))
            end = datetime.fromisoformat(str(event['end']['dateTime']).replace('Z', '+00:00'))
        except (KeyError, TypeError, ValueError):
            return False
        if (start.tzinfo is None) != (block.start.tzinfo is None):
            if start.tzinfo is None:
                start, end = start.replace(tzinfo=block.start.tzinfo), end.replace(tzinfo=block.end.tzinfo)
            else:
                expected_start = block.start.replace(tzinfo=start.tzinfo)
                expected_end = block.end.replace(tzinfo=end.tzinfo)
                return start != expected_start or end != expected_end
        return start != block.start or end != block.end

    def rollback(self, operation: DraftOperation, checkpoint=None) -> DraftOperation:
        from services.calendar.projection_state import delete_owned_verified
        created = set(operation.created_calendar_block_ids)
        created.update(block_id for block_id, action in operation.pending_calendar_writes.items()
                       if action == 'insert')
        for block_id in created:
            event_id = operation.calendar_event_ids.get(block_id)
            block = next((item for item in operation.blocks if item.id == block_id), None)
            if block is None:
                continue
            calendar_id = self._calendar_id_for(operation, block)
            remote = self._owned_remote_event(calendar_id, block_id)
            if remote is None or (event_id and remote.get('id') != event_id):
                continue
            if not self._owns_remote_event(remote, block_id, operation):
                continue
            private = remote.get('extendedProperties', {}).get('private', {})
            if private.get('personal_os_operation_id') != operation.id:
                continue
            if self._has_manual_time_override(remote, block):
                continue
            by_id = getattr(self.calendar_adapter, 'get_event_by_id', None)
            if callable(by_id):
                try:
                    exact = by_id(calendar_id, remote['id'], strict=True)
                except Exception:
                    raise RuntimeError('Calendar: чтение не подтверждено; откат остановлен.') from None
            else:
                exact = None
            if isinstance(exact, dict):
                delete_owned_verified(
                    self.calendar_adapter, calendar_id, remote['id'],
                    lambda value: self._owns_remote_event(value, block_id, operation)
                    and value.get('extendedProperties', {}).get('private', {})
                    .get('personal_os_operation_id') == operation.id,
                )
            elif not self.calendar_adapter._delete_event(calendar_id, remote['id']):
                raise RuntimeError('Calendar: откат не завершён; повтори действие.')
        for block in operation.previous_blocks:
            if any(retired.source_event_id == block.source_event_id
                   for retired in operation.retired_blocks):
                continue
            event_id = operation.previous_calendar_event_ids.get(block.id)
            if event_id:
                updated = set(operation.updated_calendar_block_ids)
                updated.update(block_id for block_id, action in operation.pending_calendar_writes.items()
                               if action == 'update')
                current = next((item for item in operation.blocks
                                if item.id in updated
                                and item.source_event_id == block.source_event_id), None)
                if current is None:
                    continue
                calendar_id = self._calendar_id_for(operation, block)
                remote = self._owned_remote_event(calendar_id, current.id)
                confirmed_current = DraftPreparationBlock(
                    **{**current.__dict__, 'status': 'confirmed'})
                if (remote is None or remote.get('id') != event_id
                        or not self._owns_remote_event(remote, current.id, operation)
                        or not (self._matches_projection(remote, self._event_data(current, operation))
                                or self._matches_projection(
                                    remote, self._event_data(confirmed_current, operation)))):
                    continue
                restored = self._write_verified(
                    operation, block, calendar_id, self._event_data(block, operation),
                    'update', event_id, checkpoint)
                if not restored:
                    raise RuntimeError('Calendar: прежняя версия подготовки пока не восстановлена.')
        return operation

    def delete_owned_events(self, calendar_id: Optional[str], event_ids: Iterable[str]) -> None:
        """Delete known system IDs only; never search/delete user events."""
        from services.calendar.projection_state import delete_owned_verified

        target_calendar = calendar_id or self.calendar_adapter._get_or_create_calendar(
            self.calendar_name,
        )
        for event_id in event_ids:
            delete_owned_verified(
                self.calendar_adapter, target_calendar, event_id,
                lambda event: bool(event.get('extendedProperties', {})
                                   .get('private', {}).get('personal_os_block_id'))
                and 'AI Calendar Block:' in str(event.get('description', '')),
            )

    def duplicate_draft_event_ids(self, operation: Optional[DraftOperation] = None) -> List[str]:
        """Find duplicate system drafts, without touching user calendar events."""
        calendar_id = self.calendar_adapter._get_or_create_calendar(self.calendar_name)
        now = datetime.now()
        events = self.calendar_adapter.list_events_in_calendar(
            calendar_id, now - timedelta(days=180), now + timedelta(days=365),
        )
        groups: Dict[str, List[dict]] = {}
        for event in events:
            if not self._owned_system_draft(event):
                continue
            private = event.get('extendedProperties', {}).get('private', {})
            source = private.get('personal_os_source_event_id')
            key = f'source:{source}' if source else f"title:{event.get('summary', '').removesuffix(' [черновик]')}"
            groups.setdefault(key, []).append(event)

        current_ids = set(operation.calendar_event_ids.values()) if operation else set()
        duplicates: List[str] = []
        for candidates in groups.values():
            if len(candidates) < 2:
                continue
            # Prefer the ID held by the current local snapshot; otherwise keep
            # the most recently updated Google event.
            ordered = sorted(
                candidates,
                key=lambda item: (
                    item.get('id') in current_ids,
                    item.get('updated', ''),
                ),
                reverse=True,
            )
            duplicates.extend(str(item['id']) for item in ordered[1:] if item.get('id'))
        return duplicates

    def system_draft_event_ids(self) -> List[str]:
        """List all Google events that are positively identified as our drafts."""
        calendar_id = self.calendar_adapter._get_or_create_calendar(self.calendar_name)
        now = datetime.now()
        events = self.calendar_adapter.list_events_in_calendar(
            calendar_id, now - timedelta(days=180), now + timedelta(days=365),
        )
        return [
            str(event['id']) for event in events
            if event.get('id') and self._owned_system_draft(event)
        ]

    @staticmethod
    def _owned_system_draft(event: dict) -> bool:
        private = event.get('extendedProperties', {}).get('private', {})
        block_id = private.get('personal_os_block_id')
        description = str(event.get('description', ''))
        return bool(block_id and f'AI Calendar Block: {block_id}\n' in description
                    and 'Status: draft' in description)

    def delete_duplicate_drafts(self, operation: Optional[DraftOperation] = None) -> int:
        """Delete only duplicates positively identified as our draft events."""
        from services.calendar.projection_state import delete_owned_verified
        calendar_id = self.calendar_adapter._get_or_create_calendar(self.calendar_name)
        duplicate_ids = self.duplicate_draft_event_ids(operation)
        for event_id in duplicate_ids:
            delete_owned_verified(self.calendar_adapter, calendar_id, event_id,
                                  self._owned_system_draft)
        return len(duplicate_ids)

    def delete_all_system_drafts(self) -> int:
        """Reset only owned drafts; personal events are never selected here."""
        from services.calendar.projection_state import delete_owned_verified
        calendar_id = self.calendar_adapter._get_or_create_calendar(self.calendar_name)
        event_ids = self.system_draft_event_ids()
        for event_id in event_ids:
            delete_owned_verified(self.calendar_adapter, calendar_id, event_id,
                                  self._owned_system_draft)
        return len(event_ids)

    @staticmethod
    def _event_data(block: DraftPreparationBlock, operation: DraftOperation):
        description = (
            f'{block.reason}\n\n'
            f'AI Calendar Operation: {operation.id}\n'
            f'AI Calendar Block: {block.id}\n'
            f'AI Calendar Source: {block.source_event_id}\n'
            f'AI Calendar Version: {operation.version}\n'
            f'AI Calendar Hash: {operation.content_hash}\n'
            + ('Manual conflict: true\n' if is_manual_conflict(block) else '')
            + f'Status: {block.status}'
        )
        return type('DraftEvent', (), {
            'uid': block.id,
            'system_block_id': block.id,
            'system_source_event_id': block.source_event_id,
            'system_operation_id': operation.id,
            'summary': (
                block.title
                + (' — Конфликт — перенести вручную' if is_manual_conflict(block) else '')
                + (' [черновик]' if block.status == 'draft' else '')
            ),
            'description': description,
            'location': '',
            'dtstart': block.start,
            'dtend': block.end,
            'event_type': 'WORK_PREPARATION' if block.calendar == CalendarRoute.WORK else 'PREPARATION',
        })()
