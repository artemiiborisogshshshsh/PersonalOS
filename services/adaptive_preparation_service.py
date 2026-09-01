"""Deterministic preparation planning with draft/confirm/rollback semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
from typing import Dict, Iterable, List, Optional
import uuid

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


def calendar_route_for(activity_type: str) -> tuple[CalendarRoute, str]:
    """Return the user-visible calendar and Google color category."""
    mapping = {
        'lecture': (CalendarRoute.STUDY, 'ЛК'),
        'practical': (CalendarRoute.STUDY, 'ПР'),
        'lab': (CalendarRoute.STUDY, 'ЛБ'),
        'preparation': (CalendarRoute.WORK, 'PREPARATION'),
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
    preparation_lead_hours: int = 36
    max_single_block_minutes: int = 120
    max_continuous_deep_work_minutes: int = 120
    travel_minutes_each_way: int = 60
    timezone: str = 'Asia/Tomsk'

    def minutes_for(self, session_type: str) -> int:
        return {
            'lecture': self.lecture_minutes,
            'practical': self.practical_minutes,
            'lab': self.lab_minutes,
        }.get(session_type, 0)


@dataclass(frozen=True)
class DraftPreparationBlock:
    id: str
    source_event_id: str
    title: str
    start: datetime
    end: datetime
    minutes: int
    reason: str
    calendar: CalendarRoute = CalendarRoute.WORK
    color: str = 'purple'
    status: str = 'draft'


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
    ) -> DraftOperation:
        now = now or datetime.now()
        events = list(personal_events)
        if fixed_commitments:
            return self._build_with_planning_engine(
                events, now, list(fixed_commitments), planning_engine,
            )
        blocks: List[DraftPreparationBlock] = []
        explanations: List[str] = []
        for event in events:
            if event.state not in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}:
                continue
            session_type = str(event.metadata.get('session_type', ''))
            minutes = self.profile.minutes_for(session_type)
            if not minutes:
                continue
            due = event.start_time - timedelta(hours=self.profile.preparation_lead_hours)
            if due <= now:
                explanations.append(
                    f'{event.title}: подготовка не создана — дедлайн в прошлом.'
                )
                continue
            start = due - timedelta(minutes=minutes)
            block_id = self._block_id(event.id, start, due)
            explanations.append(
                f'{event.title}: {minutes} мин до {due:%d.%m %H:%M} '
                f'(за {self.profile.preparation_lead_hours} ч до занятия).'
            )
            blocks.append(DraftPreparationBlock(
                id=block_id,
                source_event_id=event.id,
                title=f'Подготовка: {event.title}',
                start=start,
                end=due,
                minutes=minutes,
                reason=explanations[-1],
            ))
        return DraftOperation(
            id=str(uuid.uuid4()), blocks=blocks,
            content_hash=self._operation_hash(blocks), explanations=explanations,
        )

    def _build_with_planning_engine(
        self,
        personal_events: List[PersonalUniversityEvent],
        now: datetime,
        fixed_commitments: List[FixedCommitment],
        planning_engine: Optional[PlanningEngine],
    ) -> DraftOperation:
        """Place one preparation block in a real free slot before its 36h deadline."""
        active = [
            event for event in personal_events
            if event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
            and self.profile.minutes_for(str(event.metadata.get('session_type', '')))
        ]
        if not active:
            return DraftOperation(id=str(uuid.uuid4()), blocks=[])

        engine = planning_engine or PlanningEngine()
        for constraint in engine.constraints:
            if isinstance(constraint, MaxContinuousWorkConstraint):
                constraint.max_continuous_work_minutes = (
                    self.profile.max_continuous_deep_work_minutes
                )

        horizon_start = min(now, *(commitment.start for commitment in fixed_commitments))
        deadlines = {
            event.id: event.start_time - timedelta(hours=self.profile.preparation_lead_hours)
            for event in active
        }
        horizon_end = max(deadlines.values())
        all_commitments = [
            *fixed_commitments,
            *self._university_commitments(active, horizon_start, horizon_end),
        ]
        items = [commitment.to_planning_item() for commitment in all_commitments]
        request_ids: Dict[str, PersonalUniversityEvent] = {}
        for event in active:
            minutes = self.profile.minutes_for(str(event.metadata['session_type']))
            request_id = f'draft-preparation:{event.id}'
            request_ids[request_id] = event
            items.append(PlanningItem(
                id=request_id,
                title=f'Подготовка: {event.title}',
                description='Автоматически созданный черновик подготовки',
                item_type=PlanningItemType.PREPARATION_BLOCK,
                duration_minutes=minutes,
                earliest_start=now,
                latest_end=deadlines[event.id],
                flexible=True,
                priority=self._priority_for(event),
                metadata={
                    'is_preparation': True,
                    'target_event_id': f'university:{event.id}',
                    'source_event_uid': f'university:{event.id}',
                    'deep_work': minutes >= 60,
                    'cognitive_load': 'medium',
                },
            ))
        schedule = engine.schedule_items(items, horizon_start, horizon_end, 15)
        slots_by_item: Dict[str, List] = {}
        for slot in schedule.slots:
            if slot.scheduled_item_id in request_ids:
                slots_by_item.setdefault(slot.scheduled_item_id, []).append(slot)

        blocks: List[DraftPreparationBlock] = []
        explanations: List[str] = []
        for request_id, event in request_ids.items():
            slots = slots_by_item.get(request_id, [])
            due = deadlines[event.id]
            minutes = self.profile.minutes_for(str(event.metadata['session_type']))
            if not slots:
                explanations.append(
                    f'{event.title}: нет допустимого свободного слота до {due:%d.%m %H:%M}; '
                    'сон и fixed commitments не перенесены.'
                )
                continue
            start, end = min(slot.start for slot in slots), max(slot.end for slot in slots)
            if int((end - start).total_seconds() / 60) != minutes:
                explanations.append(
                    f'{event.title}: свободный слот оказался фрагментированным; '
                    'один блок не создан.'
                )
                continue
            block = DraftPreparationBlock(
                id=self._block_id(event.id, start, end),
                source_event_id=event.id,
                title=f'Подготовка: {event.title}',
                start=start,
                end=end,
                minutes=minutes,
                reason=(
                    f'Свободный слот найден PlanningEngine: {start:%d.%m %H:%M}–'
                    f'{end:%H:%M}; завершение за 36 ч до занятия.'
                ),
            )
            blocks.append(block)
            explanations.append(block.reason)
        return DraftOperation(
            id=str(uuid.uuid4()), blocks=blocks,
            content_hash=self._operation_hash(blocks), explanations=explanations,
        )

    def _university_commitments(
        self,
        events: Iterable[PersonalUniversityEvent],
        horizon_start: datetime,
        horizon_end: datetime,
    ) -> List[FixedCommitment]:
        commitments = []
        for event in events:
            commitments.append(FixedCommitment(
                id=f'university:{event.id}', title=event.title,
                start=event.start_time, end=event.end_time,
                commitment_type=CommitmentType.UNIVERSITY,
            ))
            for title, start, end in (
                ('Дорога в университет', event.start_time - timedelta(
                    minutes=self.profile.travel_minutes_each_way), event.start_time),
                ('Дорога из университета', event.end_time, event.end_time + timedelta(
                    minutes=self.profile.travel_minutes_each_way)),
            ):
                if start >= horizon_start and end <= horizon_end:
                    commitments.append(FixedCommitment(
                        id=f'travel:{event.id}:{start.isoformat()}', title=title,
                        start=start, end=end, commitment_type=CommitmentType.TRAVEL,
                    ))
        return commitments

    @staticmethod
    def _priority_for(event: PersonalUniversityEvent) -> int:
        return {'lab': 5, 'practical': 4, 'lecture': 2}.get(
            str(event.metadata.get('session_type', '')), 1,
        )

    @staticmethod
    def _block_id(event_id: str, start: datetime, end: datetime) -> str:
        payload = f'{event_id}|{start.isoformat()}|{end.isoformat()}'
        return f'prep:{sha256(payload.encode()).hexdigest()[:20]}'

    @staticmethod
    def _operation_hash(blocks: Iterable[DraftPreparationBlock]) -> str:
        payload = '\n'.join(
            f'{block.id}|{block.start.isoformat()}|{block.end.isoformat()}|{block.status}'
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
            self.current_blocks[block.id] = block
        self.calendar_event_ids.update(operation.previous_calendar_event_ids)
        operation.status = 'rolled_back'
        return operation

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

    def __init__(self, calendar_adapter, calendar_name: str = 'Работа'):
        self.calendar_adapter = calendar_adapter
        self.calendar_name = calendar_name

    def stage(self, operation: DraftOperation) -> DraftOperation:
        calendar_id = self.calendar_adapter._get_or_create_calendar(self.calendar_name)
        operation.calendar_id = calendar_id
        for block in operation.blocks:
            event_data = self._event_data(block, operation)
            existing_id = self.calendar_adapter.event_exists_by_uid(calendar_id, block.id)
            if existing_id:
                event_id = self.calendar_adapter._update_event(
                    calendar_id, existing_id, event_data,
                )
            else:
                previous_id = next(
                    (
                        operation.previous_calendar_event_ids.get(previous.id)
                        for previous in operation.previous_blocks
                        if previous.source_event_id == block.source_event_id
                    ),
                    None,
                )
                if previous_id:
                    event_id = self.calendar_adapter._update_event(
                        calendar_id, previous_id, event_data,
                    )
                else:
                    event_id = self.calendar_adapter._insert_event(event_data, calendar_id)
                if event_id and not previous_id:
                    if block.id not in operation.created_calendar_block_ids:
                        operation.created_calendar_block_ids.append(block.id)
            if event_id:
                operation.calendar_event_ids[block.id] = event_id
        return operation

    def confirm(self, operation: DraftOperation) -> DraftOperation:
        calendar_id = self.calendar_adapter._get_or_create_calendar(self.calendar_name)
        for block in operation.blocks:
            event_id = operation.calendar_event_ids.get(block.id)
            if event_id:
                confirmed = DraftPreparationBlock(
                    **{**block.__dict__, 'status': 'confirmed'}
                )
                self.calendar_adapter._update_event(
                    calendar_id, event_id, self._event_data(confirmed, operation),
                )
        return operation

    def rollback(self, operation: DraftOperation) -> DraftOperation:
        calendar_id = operation.calendar_id or self.calendar_adapter._get_or_create_calendar(
            self.calendar_name
        )
        for block_id in operation.created_calendar_block_ids:
            event_id = operation.calendar_event_ids.get(block_id)
            if event_id:
                self.calendar_adapter._delete_event(calendar_id, event_id)
        for block in operation.previous_blocks:
            event_id = operation.previous_calendar_event_ids.get(block.id)
            if event_id:
                self.calendar_adapter._update_event(
                    calendar_id, event_id, self._event_data(block, operation),
                )
        return operation

    @staticmethod
    def _event_data(block: DraftPreparationBlock, operation: DraftOperation):
        description = (
            f'{block.reason}\n\n'
            f'AI Calendar Operation: {operation.id}\n'
            f'AI Calendar Block: {block.id}\n'
            f'AI Calendar Version: {operation.version}\n'
            f'AI Calendar Hash: {operation.content_hash}\n'
            f'Status: {block.status}'
        )
        return type('DraftEvent', (), {
            'uid': block.id,
            'summary': ('[Черновик] ' if block.status == 'draft' else '') + block.title,
            'description': description,
            'location': '',
            'dtstart': block.start,
            'dtend': block.end,
            'event_type': 'PREPARATION',
        })()
