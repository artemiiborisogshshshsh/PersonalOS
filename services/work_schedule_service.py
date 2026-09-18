"""Read-only AlfaCRM work projection, transitions and preparation drafts.

This module deliberately owns only events carrying Personal OS metadata.  It
does not contain AlfaCRM mutation calls and it never treats a user's Google
event as a work lesson.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Optional
import json
import os
import re
import uuid

from models import PersonalEventState, PersonalUniversityEvent
from services.alfacrm_schedule_source import AlfaCRMLesson
from services.adaptive_preparation_service import (
    CalendarRoute, DraftCalendarProjector, DraftOperation, DraftPlanSyncService,
    DraftPreparationBlock, is_manual_conflict,
)
from services.draft_operation_store import DraftOperationStore
from services.weekly_plan_service import CommitmentType, FixedCommitment


WORK_CALENDAR = 'Работа'
WORK_PREPARATION_MINUTES = 20
WORK_RECOVERY_MINUTES = 30
WORK_FROM_HOME_TRAVEL_MINUTES = 20
WORK_FROM_UNIVERSITY_TRAVEL_MINUTES = 60
WORK_SETUP_MINUTES = 20
DEFAULT_WORK_MODE = 'offline'


@dataclass
class WorkPlanningState:
    source_hash: str = ''
    lessons: dict[str, dict[str, Any]] = field(default_factory=dict)
    modes: dict[str, str] = field(default_factory=dict)
    routes: dict[str, str] = field(default_factory=dict)
    feedback: dict[str, dict[str, Any]] = field(default_factory=dict)
    feedback_prompted: dict[str, str] = field(default_factory=dict)
    preparation_feedback: dict[str, dict[str, Any]] = field(default_factory=dict)
    cancelled_lessons: dict[str, dict[str, Any]] = field(default_factory=dict)


class WorkPlanningStateStore:
    """Small portable state file; credentials are intentionally absent."""

    VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> WorkPlanningState:
        if not self.path.exists():
            return WorkPlanningState()
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if payload.get('version') != self.VERSION:
            raise ValueError('Unsupported work planning storage version')
        return WorkPlanningState(**payload.get('state', {}))

    def save(self, state: WorkPlanningState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Optional[Path] = None
        try:
            with NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.path.parent, delete=False,
                prefix=f'.{self.path.name}.', suffix='.tmp',
            ) as output:
                json.dump({'version': self.VERSION, 'state': state.__dict__}, output,
                          ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
                temporary = Path(output.name)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


@dataclass(frozen=True)
class _CalendarEvent:
    uid: str
    summary: str
    description: str
    dtstart: datetime
    dtend: datetime
    event_type: str
    system_block_id: str
    system_source_event_id: str
    system_operation_id: str
    location: str = ''


class WorkScheduleService:
    """Projects AlfaCRM lessons to ``Работа`` and keeps source state durable."""

    def __init__(self, calendar_adapter: Any, state_store: WorkPlanningStateStore):
        self.calendar_adapter = calendar_adapter
        self.state_store = state_store
        self.state = state_store.load()

    @staticmethod
    def _source_hash(lessons: Iterable[AlfaCRMLesson]) -> str:
        return sha256('\n'.join(sorted(
            f'{lesson.id}:{lesson.content_hash}' for lesson in lessons
        )).encode()).hexdigest()

    def sync(self, lessons: Iterable[AlfaCRMLesson], *, verified_horizon=None) -> dict[str, Any]:
        """Create/update only owned projections; delete owned missing lessons."""
        lessons = list(lessons)
        calendar_id = self.calendar_adapter._get_or_create_calendar(WORK_CALENDAR)
        active_ids = {lesson.id for lesson in lessons}
        created = updated = deleted = 0
        deleted_source_ids: list[str] = []
        for lesson in lessons:
            if lesson.id in self.state.cancelled_lessons:
                self.state.cancelled_lessons.pop(lesson.id)
                self.state_store.save(self.state)
            current = self.state.lessons.get(lesson.id, {})
            event_data = self._event_data(lesson)
            projection_hash = sha256((lesson.content_hash + '|' + (self.mode_for(lesson) or '') + '|'
                                      + json.dumps(self.state.feedback.get(lesson.id, {}),
                                                   ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
            event_id = current.get('calendar_event_id')
            if event_id and current.get('projection_hash') == projection_hash:
                continue
            existing_id = self.calendar_adapter.event_exists_by_uid(calendar_id, event_data.uid)
            if existing_id:
                event_id = self.calendar_adapter._update_event(calendar_id, existing_id, event_data)
                updated += 1
            else:
                event_id = self.calendar_adapter._insert_event(event_data, calendar_id)
                created += 1
            if not event_id:
                raise RuntimeError(f'Google Calendar did not save work lesson {lesson.id}')
            self.state.lessons[lesson.id] = {
                'content_hash': lesson.content_hash,
                'projection_hash': projection_hash,
                'calendar_event_id': event_id,
                'mode_key': lesson.mode_key,
                'title': lesson.display_name,
                'start': lesson.start.isoformat(),
                'end': lesson.end.isoformat(),
            }
            self.state_store.save(self.state)
        for lesson_id, snapshot in list(self.state.lessons.items()):
            if lesson_id in active_ids:
                continue
            # A rolling fetch is not the entire CRM history. Without explicit
            # coverage absence never authorizes deletion.
            if verified_horizon is None:
                continue
            try:
                source_start = datetime.fromisoformat(snapshot['start'])
                left, right = verified_horizon
                if source_start.tzinfo is None and left.tzinfo is not None:
                    source_start = source_start.replace(tzinfo=left.tzinfo)
                if not left <= source_start < right:
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            # Keep verified cancellation evidence even if a later Calendar
            # delete or checkpoint fails. Feedback remains in its own history.
            self.state.cancelled_lessons[lesson_id] = {
                **snapshot, 'verified_from': left.isoformat(), 'verified_until': right.isoformat(),
            }
            self.state_store.save(self.state)
            event_id = snapshot.get('calendar_event_id')
            if event_id:
                if not self.calendar_adapter._delete_event(calendar_id, event_id):
                    raise RuntimeError('Google Calendar did not confirm work lesson deletion')
            prep_uid = 'work-prep:' + sha256(lesson_id.encode()).hexdigest()[:20]
            prep_event_id = self.calendar_adapter.event_exists_by_uid(calendar_id, prep_uid)
            if prep_event_id and self.state.preparation_feedback.get(prep_uid, {}).get('outcome') != 'done':
                if not self.calendar_adapter._delete_event(calendar_id, prep_event_id):
                    raise RuntimeError('Google Calendar did not confirm preparation deletion')
            self.state.lessons.pop(lesson_id, None)
            self.state.routes.pop(lesson_id, None)
            deleted += 1
            deleted_source_ids.append(lesson_id)
            self.state_store.save(self.state)
        self.state.source_hash = self._source_hash(lessons)
        self.state_store.save(self.state)
        return {'created': created, 'updated': updated, 'deleted': deleted,
                'deleted_source_ids': deleted_source_ids,
                'changed': bool(created or updated or deleted), 'calendar_id': calendar_id}

    def remove_overlapping_work_preparations(self, lessons: Iterable[AlfaCRMLesson]) -> int:
        """Count owned work preparations that need an explicit replan.

        An overlap can be caused by a user moving a system block in Google
        Calendar. Ownership proves that this app created the event, not that
        it may delete the user's chosen placement. The caller reports these
        candidates and leaves them untouched until an explicit replan.
        """
        lessons_by_id = {lesson.id: lesson for lesson in lessons}
        if not lessons_by_id or not hasattr(self.calendar_adapter, 'list_events_in_calendar'):
            return 0
        calendar_id = self.calendar_adapter._get_or_create_calendar(WORK_CALENDAR)
        now = datetime.now().astimezone()
        try:
            events = self.calendar_adapter.list_events_in_calendar(
                calendar_id, now - timedelta(days=30), now + timedelta(days=60),
            )
        except Exception:
            # Repair is optional; inability to list does not license a broad
            # delete and must not prevent normal source synchronization.
            return 0
        conflicts = 0
        for event in events:
            description = str(event.get('description') or '')
            private = event.get('extendedProperties', {}).get('private', {})
            source_id = private.get('personal_os_source_event_id')
            if not source_id:
                match = re.search(r'AI Calendar Source: ([^\n]+)', description)
                source_id = match.group(1).strip() if match else None
            lesson = lessons_by_id.get(str(source_id))
            if lesson is None or 'AI Calendar Block:' not in description:
                continue
            start, end = self._google_interval(event)
            if start is None or end is None or not (start < lesson.end and end > lesson.start):
                continue
            if event.get('id'):
                conflicts += 1
        return conflicts

    @staticmethod
    def _google_interval(event: dict) -> tuple[Optional[datetime], Optional[datetime]]:
        def parse(value: Any) -> Optional[datetime]:
            if not value:
                return None
            try:
                return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            except ValueError:
                return None
        return parse(event.get('start', {}).get('dateTime')), parse(event.get('end', {}).get('dateTime'))

    def unknown_mode_lessons(self, lessons: Iterable[AlfaCRMLesson]) -> list[AlfaCRMLesson]:
        # New work sessions are safely planned as offline by default.  The
        # method is retained for callers that want to show only explicitly
        # unresolved records from an older state schema.
        return []

    def set_mode(self, lesson: AlfaCRMLesson, mode: str) -> None:
        if mode not in {'online', 'offline'}:
            raise ValueError('Work mode must be online or offline')
        self.state.modes[lesson.mode_key] = mode
        self.state_store.save(self.state)

    def set_route(self, lesson_id: str, route: str) -> None:
        if route not in {'direct', 'home'}:
            raise ValueError('Work route must be direct or home')
        self.state.routes[lesson_id] = route
        self.state_store.save(self.state)

    def record_feedback(self, lesson_id: str, outcome: str, homework: str = '',
                        deadline: str = '', comment: str = '') -> None:
        if outcome not in {'done', 'partial', 'cancelled'}:
            raise ValueError('Unknown work feedback outcome')
        self.state.feedback[lesson_id] = {
            'outcome': outcome, 'homework': homework, 'deadline': deadline,
            'comment': comment, 'recorded_at': datetime.now().isoformat(),
        }
        self.state_store.save(self.state)

    def due_feedback(self, lessons: Iterable[AlfaCRMLesson], now: Optional[datetime] = None) -> list[AlfaCRMLesson]:
        now = now or datetime.now()
        due = [lesson for lesson in lessons if lesson.end <= now
               and lesson.id not in self.state.feedback
               and lesson.id not in self.state.feedback_prompted]
        for lesson in due:
            self.state.feedback_prompted[lesson.id] = now.isoformat()
        if due:
            self.state_store.save(self.state)
        return due

    def record_preparation_feedback(self, block_id: str, outcome: str, minutes: int,
                                    difficulty: int, comment: str = '') -> None:
        if outcome not in {'done', 'partial', 'skipped'}:
            raise ValueError('Unknown work preparation outcome')
        if minutes < 0 or not 1 <= difficulty <= 5:
            raise ValueError('Invalid preparation feedback values')
        self.state.preparation_feedback[block_id] = {
            'outcome': outcome, 'minutes': minutes, 'difficulty': difficulty,
            'comment': comment, 'recorded_at': datetime.now().isoformat(),
        }
        self.state_store.save(self.state)

    def mode_for(self, lesson: AlfaCRMLesson) -> Optional[str]:
        return self.state.modes.get(lesson.mode_key, DEFAULT_WORK_MODE)

    def _event_data(self, lesson: AlfaCRMLesson) -> _CalendarEvent:
        feedback = self.state.feedback.get(lesson.id, {})
        students = ', '.join(lesson.students) or 'не указаны AlfaCRM'
        feedback_text = '\n'.join([
            f'Статус: {feedback.get("outcome", "ещё не получен")}',
            f'ДЗ: {feedback.get("homework", "—")}',
            f'Дедлайн: {feedback.get("deadline", "—")}',
            f'Комментарий: {feedback.get("comment", "—")}',
        ])
        description = (
            'Personal OS Work Event\n'
            f'AlfaCRM source: {lesson.id}\n'
            f'Version hash: {lesson.content_hash}\n'
            f'Предмет: {lesson.subject or "не указан"}\n'
            f'Группа: {lesson.group or "не указана"}\n'
            f'Ученики: {students}\n'
            f'Формат: {self.mode_for(lesson) or "нужно уточнить"}\n\n'
            'Feedback\n' + feedback_text
        )
        return _CalendarEvent(
            uid=f'personal-os:work:{lesson.id}',
            system_block_id=f'work-lesson:{lesson.id}', system_source_event_id=lesson.id,
            system_operation_id='work-source-sync',
            summary=f'Работа: {lesson.display_name}', description=description,
            dtstart=lesson.start, dtend=lesson.end, location=lesson.location,
            event_type='WORK_LESSON',
        )


class WorkPreparationPlanner:
    """Place one 20-minute preparation per work lesson on a five-minute grid."""

    def __init__(
        self, state_service: WorkScheduleService, break_minutes: int = 0,
        max_continuous_minutes: int = 120, series_break_minutes: int = 15,
    ):
        if break_minutes < 0 or max_continuous_minutes <= 0 or series_break_minutes < 0:
            raise ValueError('Work preparation timing settings must be non-negative')
        self.state_service = state_service
        self.break_minutes = break_minutes
        self.max_continuous_minutes = max_continuous_minutes
        self.series_break_minutes = series_break_minutes

    def _within_continuous_series_limit(
        self, start: datetime, end: datetime, blocks: Iterable[DraftPreparationBlock],
    ) -> bool:
        """Allow adjacent preparations, but require a real break after a series.

        A short gap below ``series_break_minutes`` is not recovery.  It stays
        part of the same series and therefore counts toward the 120-minute
        cap; an exactly-long-enough gap starts a new series.
        """
        intervals = sorted(
            [(block.start, block.end, block.id) for block in blocks]
            + [(start, end, '__candidate__')],
        )
        component_start, component_end = intervals[0][0], intervals[0][1]
        contains_candidate = intervals[0][2] == '__candidate__'
        for item_start, item_end, item_id in intervals[1:]:
            if item_start - component_end < timedelta(minutes=self.series_break_minutes):
                component_end = max(component_end, item_end)
                contains_candidate = contains_candidate or item_id == '__candidate__'
                continue
            if contains_candidate:
                return component_end - component_start <= timedelta(
                    minutes=self.max_continuous_minutes,
                )
            component_start, component_end = item_start, item_end
            contains_candidate = item_id == '__candidate__'
        return (component_end - component_start <= timedelta(
            minutes=self.max_continuous_minutes,
        ) if contains_candidate else True)

    @staticmethod
    def _attended(events: Iterable[PersonalUniversityEvent]) -> list[PersonalUniversityEvent]:
        return [event for event in events if event.state in {
            PersonalEventState.CONFIRMED, PersonalEventState.MOVED,
        }]

    def _university_and_transition_commitments(
        self, lessons: Iterable[AlfaCRMLesson], university: Iterable[PersonalUniversityEvent],
        start: datetime, end: datetime,
    ) -> tuple[list[FixedCommitment], set[str]]:
        lessons = list(lessons)
        grouped: dict[object, list[PersonalUniversityEvent]] = {}
        for event in self._attended(university):
            grouped.setdefault(event.start_time.date(), []).append(event)
        commitments: list[FixedCommitment] = []
        waiting_for_route: set[str] = set()
        for day, day_events in grouped.items():
            first = min(event.start_time for event in day_events)
            last = max(event.end_time for event in day_events)
            key = day.isoformat()
            commitments.extend([
                FixedCommitment(f'work-campus:{key}', 'Университетский день', first, last,
                                CommitmentType.UNIVERSITY),
                FixedCommitment(f'work-to-campus:{key}', 'Дорога в университет',
                                first - timedelta(minutes=60), first, CommitmentType.TRAVEL),
            ])
            after = sorted((lesson for lesson in lessons if lesson.start.date() == day and lesson.start >= last),
                           key=lambda lesson: lesson.start)
            if not after:
                commitments.extend([
                    FixedCommitment(f'work-from-campus:{key}', 'Дорога из университета', last,
                                    last + timedelta(minutes=60), CommitmentType.TRAVEL),
                    FixedCommitment(f'work-campus-recovery:{key}', 'Восстановление после университета',
                                    last + timedelta(minutes=60), last + timedelta(minutes=120),
                                    CommitmentType.RECOVERY),
                ])
                continue
            lesson = after[0]
            mode = self.state_service.mode_for(lesson)
            if mode is None or (mode == 'offline' and lesson.id not in self.state_service.state.routes):
                # Until the user selects a route, no apparently-free part of
                # this transition may become a preparation slot.
                commitments.append(FixedCommitment(
                    f'work-transition-pending:{lesson.id}', 'Переход к работе: ждём маршрут',
                    last, lesson.start, CommitmentType.TRAVEL,
                ))
                waiting_for_route.add(lesson.id)
                continue
            minutes = 80 if mode == 'online' else (
                60 if self.state_service.state.routes.get(lesson.id) == 'direct' else 80
            )
            label = 'Дорога домой и подготовка места' if mode == 'online' else 'Дорога к работе'
            commitments.append(FixedCommitment(
                f'work-transition:{lesson.id}', label,
                lesson.start - timedelta(minutes=minutes), lesson.start,
                CommitmentType.TRAVEL,
            ))
        return commitments, waiting_for_route

    @staticmethod
    def _previous_saturday(lesson_start: datetime) -> datetime:
        """Return 10:00 on the Saturday immediately before lesson's week."""
        week_start = (lesson_start - timedelta(days=lesson_start.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        return week_start - timedelta(days=2) + timedelta(hours=10)

    def build_draft(
        self, lessons: Iterable[AlfaCRMLesson], university: Iterable[PersonalUniversityEvent],
        fixed_commitments: Iterable[FixedCommitment] = (), now: Optional[datetime] = None,
    ) -> DraftOperation:
        now = now or datetime.now()
        lessons = sorted(list(lessons), key=lambda lesson: (lesson.start, lesson.id))
        # AlfaCRM timestamps are timezone-aware while profile commitments may
        # be local naive datetimes. One timeline is mandatory: comparing them
        # directly raises TypeError and can leave a Telegram command hanging.
        timezone = next((lesson.start.tzinfo for lesson in lessons if lesson.start.tzinfo), now.tzinfo)

        def normalise(value: datetime) -> datetime:
            if timezone is None:
                return value
            return value.astimezone(timezone) if value.tzinfo else value.replace(tzinfo=timezone)

        now = normalise(now)
        university = [
            type('UniversityInterval', (), {
                'state': event.state,
                'start_time': normalise(event.start_time),
                'end_time': normalise(event.end_time),
            })()
            for event in university
        ]
        fixed_commitments = [
            replace(commitment, start=normalise(commitment.start), end=normalise(commitment.end))
            for commitment in fixed_commitments
        ]
        horizon = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ) + timedelta(weeks=2)
        commitments, waiting_for_route = self._university_and_transition_commitments(
            lessons, university, now, horizon,
        )
        attended_university = self._attended(university)
        for lesson in lessons:
            if lesson.start <= now or lesson.start > horizon:
                continue
            # The source lesson itself is immutable.  Its recovery is equally
            # unavailable to preparations, even though neighbouring source
            # lessons may make that recovery impossible in practice.
            commitments.append(FixedCommitment(
                f'work-lesson:{lesson.id}', f'Работа: {lesson.display_name}',
                lesson.start, lesson.end, CommitmentType.TUTORING,
            ))
            commitments.append(FixedCommitment(
                f'work-recovery:{lesson.id}', 'Восстановление после работы',
                lesson.end, lesson.end + timedelta(minutes=WORK_RECOVERY_MINUTES),
                CommitmentType.RECOVERY,
            ))
            prior_university = any(
                event.start_time.date() == lesson.start.date()
                and event.end_time <= lesson.start for event in attended_university
            )
            # A post-university transition is added above. From home the
            # source needs either a 20-min commute or a 20-min setup block.
            if not prior_university and self.state_service.mode_for(lesson) is not None:
                label = ('Подготовка рабочего места' if self.state_service.mode_for(lesson) == 'online'
                         else 'Дорога к работе')
                commitments.append(FixedCommitment(
                    f'work-from-home:{lesson.id}', label,
                    lesson.start - timedelta(minutes=WORK_FROM_HOME_TRAVEL_MINUTES), lesson.start,
                    CommitmentType.TRAVEL,
                ))
        commitments.extend(fixed_commitments)
        # University preparations have priority over the entire Saturday
        # session, including its breaks. Do not fill a gap before the last
        # study block even if the gap is otherwise free.
        study_ends = {}
        for commitment in fixed_commitments:
            if (commitment.start.weekday() == 5
                    and commitment.metadata.get('preparation_scope') == 'university-preparation'):
                day = commitment.start.date()
                study_ends[day] = max(study_ends.get(day, commitment.end), commitment.end)
        eligible: list[AlfaCRMLesson] = []
        explanations: list[str] = []
        no_slot_reasons = {}
        for lesson in lessons:
            if lesson.start <= now or lesson.start > horizon:
                continue
            prep_id = 'work-prep:' + sha256(lesson.id.encode()).hexdigest()[:20]
            if self.state_service.state.preparation_feedback.get(prep_id, {}).get('outcome') == 'done':
                continue
            if self.state_service.mode_for(lesson) is None:
                explanations.append(f'{lesson.display_name}: нужен ответ online/offline; подготовка ждёт.')
            elif lesson.id in waiting_for_route:
                explanations.append(f'{lesson.display_name}: нужен ответ о маршруте после университета.')
            else:
                eligible.append(lesson)
        busy = [(commitment.start, commitment.end) for commitment in commitments]
        blocks: list[DraftPreparationBlock] = []
        for lesson in eligible:
            earliest = max(now, lesson.start - timedelta(days=7))
            candidate = (lesson.start - timedelta(minutes=WORK_PREPARATION_MINUTES)).replace(
                second=0, microsecond=0,
            )
            candidate -= timedelta(minutes=candidate.minute % 5)
            selected: Optional[tuple[datetime, datetime]] = None

            def is_free(start: datetime) -> bool:
                block_end = start + timedelta(minutes=WORK_PREPARATION_MINUTES)
                if start < earliest or block_end > lesson.start:
                    return False
                if start.weekday() >= 5 and start.hour < 10:
                    return False
                if start.weekday() == 6 and lesson.start.date() != (start + timedelta(days=1)).date():
                    return False
                if start.date() in study_ends and start < study_ends[start.date()]:
                    return False
                padding_start = start - timedelta(minutes=self.break_minutes)
                padding_end = block_end + timedelta(minutes=self.break_minutes)
                conflicts = any(start < end and block_end > begin for begin, end in busy)
                close_to_prep = any(padding_start < item.end and padding_end > item.start for item in blocks)
                return (not conflicts and not close_to_prep
                        and self._within_continuous_series_limit(start, block_end, blocks))

            # Saturday is the primary preparation day for classes in the
            # coming week.  Starting at 10:00 packs several 20-minute work
            # preparations into one session and preserves Sunday and weekday
            # evenings.  It is only a preference: source lessons, sleep and
            # every fixed Calendar event remain hard constraints.
            saturday = self._previous_saturday(lesson.start)
            saturday_start = max(earliest, saturday)
            saturday_end = min(
                lesson.start,
                saturday.replace(hour=23, minute=0),
            )
            if saturday_start >= now and saturday_start + timedelta(
                minutes=WORK_PREPARATION_MINUTES,
            ) <= saturday_end:
                saturday_candidate = saturday_start.replace(second=0, microsecond=0)
                saturday_candidate += timedelta(minutes=(-saturday_candidate.minute) % 5)
                while saturday_candidate + timedelta(minutes=WORK_PREPARATION_MINUTES) <= saturday_end:
                    if is_free(saturday_candidate):
                        selected = (
                            saturday_candidate,
                            saturday_candidate + timedelta(minutes=WORK_PREPARATION_MINUTES),
                        )
                        break
                    saturday_candidate += timedelta(minutes=5)

            # A preparation belongs to this exact lesson.  Search backwards
            # from the lesson first; an earlier free day must not win merely
            # because it would make a visually neat bundle with another
            # preparation.  Consecutive work preparations still naturally
            # form a single session when their closest valid slots touch.
            while selected is None and candidate >= earliest:
                block_end = candidate + timedelta(minutes=WORK_PREPARATION_MINUTES)
                if is_free(candidate):
                    selected = candidate, block_end
                    break
                candidate -= timedelta(minutes=5)
            if selected is None:
                no_slot_reasons[lesson.id] = 'нет свободных 20 минут до работы с учётом дороги и обязательств'
                block_start = self._manual_conflict_start(
                    now, earliest, lesson.start, saturday,
                )
                block_end = block_start + timedelta(minutes=WORK_PREPARATION_MINUTES)
                if block_end > lesson.start:
                    explanations.append(
                        f'{lesson.display_name}: подготовка не создана — даже конфликтный '
                        '20-минутный блок уже не помещается до рабочей пары.'
                    )
                    continue
                block = DraftPreparationBlock(
                    id=self._block_id(lesson.id, manual_conflict=True),
                    source_event_id=lesson.id,
                    title=f'Подготовка к работе: {lesson.display_name} — {lesson.start:%d.%m %H:%M}',
                    start=block_start, end=block_end, minutes=WORK_PREPARATION_MINUTES,
                    reason=(
                        'Конфликт — перенести вручную. '
                        f'Причина: {no_slot_reasons[lesson.id]}. '
                        + 'Свободный слот не найден; маркер поставлен в предпочтительное время '
                        'без переноса обязательств.'
                    ),
                    calendar=CalendarRoute.WORK, color='basil', manual_conflict=True,
                )
                blocks.append(block)
                busy.append((block_start, block_end))
                explanations.append(block.reason)
                continue
            block_start, block_end = selected
            block_id = self._block_id(lesson.id)
            block = DraftPreparationBlock(
                id=block_id, source_event_id=lesson.id,
                title=f'Подготовка к работе: {lesson.display_name} — {lesson.start:%d.%m %H:%M}',
                start=block_start, end=block_end, minutes=WORK_PREPARATION_MINUTES,
                reason=(
                    (f'Субботний блок подготовки для следующей недели: '
                     f'{block_start:%d.%m %H:%M}–{block_end:%H:%M}; '
                     'другие подготовки собраны в этот же свободный день.')
                    if block_start.date() == saturday.date() else
                    (f'Ближайший свободный слот перед рабочей парой: '
                     f'{block_start:%d.%m %H:%M}–{block_end:%H:%M}; '
                     'университет, дорога, сон и fixed commitments сохранены.')
                ),
                calendar=CalendarRoute.WORK, color='basil',
            )
            blocks.append(block)
            busy.append((block_start, block_end))
            explanations.append(block.reason)
        payload = '\n'.join(
            f'{block.id}:{block.start.isoformat()}:{block.end.isoformat()}:{is_manual_conflict(block)}'
            for block in blocks
        )
        return DraftOperation(
            id=str(uuid.uuid4()), blocks=blocks, scope='work-preparation',
            content_hash=sha256(payload.encode()).hexdigest(), explanations=explanations,
            no_slot_reasons=no_slot_reasons,
        )

    @staticmethod
    def _block_id(lesson_id: str, manual_conflict: bool = False) -> str:
        """Return the durable preparation identity for one AlfaCRM lesson.

        ``manual_conflict`` describes a placement state, not a new task.  It
        is intentionally accepted for compatibility with older callers but
        never participates in the identity: feedback, a manually moved
        Calendar projection and a later normal placement must all remain
        linked to the same lesson preparation.
        """
        del manual_conflict
        return 'work-prep:' + sha256(lesson_id.encode()).hexdigest()[:20]

    @staticmethod
    def _next_five_minutes(value: datetime) -> datetime:
        candidate = value.replace(second=0, microsecond=0)
        candidate += timedelta(minutes=(-candidate.minute) % 5)
        if candidate < value:
            candidate += timedelta(minutes=5)
        return candidate

    def _manual_conflict_start(
        self, now: datetime, earliest: datetime, lesson_start: datetime, saturday: datetime,
    ) -> datetime:
        """Use the normal Saturday preference before exposing a manual marker."""
        preferred = self._next_five_minutes(max(earliest, saturday))
        saturday_end = min(lesson_start, saturday.replace(hour=23, minute=0))
        if preferred + timedelta(minutes=WORK_PREPARATION_MINUTES) <= saturday_end:
            return preferred
        preferred = (lesson_start - timedelta(minutes=WORK_PREPARATION_MINUTES)).replace(
            second=0, microsecond=0,
        )
        preferred -= timedelta(minutes=preferred.minute % 5)
        if preferred >= now and preferred >= earliest:
            return preferred
        return self._next_five_minutes(now)


class WorkPreparationWorkflow:
    """Independent draft lifecycle so a work replan cannot roll back study."""

    def __init__(self, planner: WorkPreparationPlanner, projector: DraftCalendarProjector,
                 store: DraftOperationStore, lessons_provider, university_provider,
                 commitments_provider=lambda: (), now_provider=datetime.now):
        self.planner, self.projector, self.store = planner, projector, store
        self.lessons_provider, self.university_provider = lessons_provider, university_provider
        self.commitments_provider, self.now_provider = commitments_provider, now_provider
        self.sync = DraftPlanSyncService()
        self.current_operation: Optional[DraftOperation] = self._recover()

    def preview(self) -> str:
        if self.current_operation and self.current_operation.projection_pending:
            return self.stage()
        if self.current_operation and self.current_operation.status == 'draft' and self.current_operation.calendar_event_ids:
            return self.sync.preview(self.current_operation.id) + '\n\nЧерновики уже созданы в Google Calendar.'
        operation = self.planner.build_draft(self.lessons_provider(), self.university_provider(),
                                             self.commitments_provider(), self.now_provider())
        self.sync.stage(operation)
        self.current_operation = operation
        self.store.save(operation)
        return self.sync.preview(operation.id)

    def stage(self) -> str:
        operation = self._current()
        if operation.status != 'draft':
            return 'Эта операция уже не является черновиком.'
        operation.projection_pending = True
        self.store.save(operation)
        try:
            self.projector.stage(operation, checkpoint=self.store.save)
            operation.projection_pending = False
        finally:
            self.sync.calendar_event_ids.update(operation.calendar_event_ids)
            self.store.save(operation)
        return f'Черновики ({len(operation.blocks)}) созданы в календаре «{WORK_CALENDAR}».\n\n{self.sync.preview(operation.id)}'

    def confirm(self) -> str:
        operation = self._current()
        self.projector.confirm(operation)
        self.sync.confirm(operation.id)
        self.sync.calendar_event_ids.update(operation.calendar_event_ids)
        self.store.save(operation)
        return 'Рабочие подготовки подтверждены.'

    def rollback(self) -> str:
        operation = self._current()
        self.projector.rollback(operation)
        self.sync.rollback(operation.id)
        operation.projection_pending = False
        self.store.save(operation)
        return 'Рабочие черновики удалены; прежние системные блоки восстановлены.'

    def replan(self) -> str:
        if self.current_operation and self.current_operation.projection_pending:
            return self.stage()
        lessons = list(self.lessons_provider())
        university = list(self.university_provider())
        owned_ids = set(self.current_operation.calendar_event_ids.values()) if self.current_operation else set()
        commitments = [item for item in self.commitments_provider()
                       if item.metadata.get('google_event_id') not in owned_ids]
        candidate = self.planner.build_draft(lessons, university, commitments, self.now_provider())
        if (self.current_operation and self.current_operation.status in {'draft', 'confirmed'}
                and candidate.content_hash == self.current_operation.content_hash
                and [(b.source_event_id, b.title) for b in candidate.blocks]
                    == [(b.source_event_id, b.title) for b in self.current_operation.blocks]
                and all(block.id in self.current_operation.calendar_event_ids
                        for block in self.current_operation.blocks)):
            return self.sync.preview(self.current_operation.id) + '\n\nПлан не изменился.'
        if self.current_operation is not None:
            active = {lesson.id for lesson in lessons}
            # Calendar deletion is normally performed by WorkScheduleService
            # during source sync.  This additionally removes stale local
            # ownership so a vanished lesson cannot return after restart.
            self.sync.remove_missing_sources(active)
        if self.current_operation and self.current_operation.status == 'draft' and self.current_operation.calendar_event_ids:
            self.rollback()
        self.sync.stage(candidate)
        self.current_operation = candidate
        self.store.save(candidate)
        return self.sync.preview(candidate.id)

    def _current(self) -> DraftOperation:
        if self.current_operation is None:
            raise ValueError('Сначала построй черновик рабочих подготовок.')
        return self.current_operation

    def _recover(self) -> Optional[DraftOperation]:
        for operation in reversed(list(self.store.load_all().values())):
            if operation.scope == 'work-preparation' and operation.status in {'draft', 'confirmed'}:
                self.sync.operations[operation.id] = operation
                self.sync.current_blocks.update({block.id: block for block in
                                                 [*operation.blocks, *operation.retained_blocks]})
                self.sync.calendar_event_ids.update(operation.calendar_event_ids)
                return operation
        return None
