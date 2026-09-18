"""Application workflow for Telegram preparation preview, commit and rollback."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable, Optional

from models import PersonalUniversityEvent
from services.adaptive_preparation_service import (
    AdaptivePreparationService,
    DraftCalendarProjector,
    DraftOperation,
    DraftPlanSyncService,
    PERSONAL_UNIVERSITY_CALENDAR,
)
from services.draft_operation_store import DraftOperationStore
from services.adaptive_feedback_service import AdaptiveFeedbackService, PreparationFeedback
from services.weekly_plan_service import FixedCommitment


class PreparationDraftWorkflow:
    """Keeps one pending operation and applies only system-owned calendar changes."""

    def __init__(
        self,
        planner: AdaptivePreparationService,
        draft_sync: DraftPlanSyncService,
        calendar_projector: DraftCalendarProjector,
        operation_store: DraftOperationStore,
        events_provider: Callable[[], Iterable[PersonalUniversityEvent]],
        commitments_provider: Callable[[], Iterable[FixedCommitment]] = lambda: (),
        flexible_items_provider: Callable[[], Iterable] = lambda: (),
        now_provider: Callable[[], datetime] = datetime.now,
    ):
        self.planner = planner
        self.draft_sync = draft_sync
        self.calendar_projector = calendar_projector
        self.operation_store = operation_store
        self.events_provider = events_provider
        self.commitments_provider = commitments_provider
        self.flexible_items_provider = flexible_items_provider
        self.now_provider = now_provider
        self.current_operation: Optional[DraftOperation] = self._recover_pending()
        self.feedback_service = AdaptiveFeedbackService()
        if self.current_operation is not None:
            self.completed_source_event_ids = set(self.current_operation.completed_source_event_ids)
            self.carryover_minutes_by_course = dict(self.current_operation.carryover_minutes_by_course)
        else:
            self.completed_source_event_ids = set()
            self.carryover_minutes_by_course = {}

    def preview(self) -> str:
        if self.current_operation and self.current_operation.projection_pending:
            return self.stage()
        if (
            self.current_operation is not None
            and self.current_operation.status == 'draft'
            and self.current_operation.calendar_event_ids
        ):
            return (
                self.draft_sync.preview(self.current_operation.id)
                + '\n\nЧерновики уже созданы в Google Calendar. Нажми «Перепланировать», '
                'чтобы безопасно заменить их новым вариантом.'
            )
        self.current_operation = self.planner.build_draft(
            self.events_provider(), now=self.now_provider(),
            fixed_commitments=self.commitments_provider(),
            flexible_items=self.flexible_items_provider(),
            completed_source_event_ids=self.completed_source_event_ids,
            carryover_minutes_by_course=self.carryover_minutes_by_course,
        )
        self.draft_sync.stage(self.current_operation)
        self.operation_store.save(self.current_operation)
        return self.draft_sync.preview(self.current_operation.id)

    def stage(self) -> str:
        operation = self._current()
        if operation.status != 'draft':
            return 'Эта операция уже не является черновиком.'
        operation.projection_pending = True
        self.operation_store.save(operation)
        try:
            self.calendar_projector.stage(operation, checkpoint=self.operation_store.save)
            operation.projection_pending = False
        finally:
            self._record_projection(operation)
            self.operation_store.save(operation)
        return (
            f'Черновики ({len(operation.blocks)}) созданы в календаре '
            f'«{PERSONAL_UNIVERSITY_CALENDAR}».\n\n'
            f'{self.draft_sync.preview(operation.id)}'
        )

    def confirm(self) -> str:
        operation = self._current()
        self.calendar_projector.confirm(operation)
        self.draft_sync.confirm(operation.id)
        self._record_projection(operation)
        self.operation_store.save(operation)
        return 'Подготовки подтверждены. Автоматически будут меняться только новые черновики.'

    def rollback(self) -> str:
        operation = self._current()
        self.calendar_projector.rollback(operation)
        self.draft_sync.rollback(operation.id)
        operation.projection_pending = False
        self.operation_store.save(operation)
        return 'Черновики текущей операции удалены, прежние системные блоки восстановлены.'

    def reset_deleted_remote_drafts(self) -> None:
        """Forget a draft after the explicit global Calendar reset.

        ``delete_all_system_drafts`` intentionally operates only on Google
        events.  The local snapshot must be cleared as well: otherwise a
        later `/preparations` assumes the deleted IDs still exist and refuses
        to stage a fresh draft.
        """
        if self.current_operation is None:
            return
        operation = self.current_operation
        if operation.status == 'draft':
            self.draft_sync.rollback(operation.id)
        operation.calendar_event_ids.clear()
        operation.created_calendar_block_ids.clear()
        operation.previous_calendar_event_ids.clear()
        operation.previous_blocks.clear()
        operation.calendar_id = None
        operation.status = 'rolled_back'
        self.operation_store.save(operation)

    def replan(self, source_changed: bool = False) -> str:
        if self.current_operation and self.current_operation.projection_pending:
            return self.stage()
        if self.current_operation and self.current_operation.status != 'draft' and not source_changed:
            return (
                'Расписание изменилось, но подтверждённые подготовки не меняются '
                'автоматически. Открой preview и подтверди новое решение вручную.'
            )
        was_projected = bool(self.current_operation and self.current_operation.calendar_event_ids)
        if was_projected and not self._calendar_is_ready():
            return (
                'Расписание изменилось, но Google Calendar ещё не подключён. '
                'Черновики сохранены без изменений; подключи Calendar и запусти '
                '«Перепланировать».'
            )
        # Read and calculate before touching the previous operation. A source
        # or busy-calendar failure must leave the published draft intact.
        events = list(self.events_provider())
        owned_ids = set(self.current_operation.calendar_event_ids.values()) if self.current_operation else set()
        commitments = [item for item in self.commitments_provider()
                       if item.metadata.get('google_event_id') not in owned_ids]
        candidate = self.planner.build_draft(
            events, now=self.now_provider(), fixed_commitments=commitments,
            flexible_items=list(self.flexible_items_provider()),
            completed_source_event_ids=self.completed_source_event_ids,
            carryover_minutes_by_course=self.carryover_minutes_by_course,
        )
        if (self.current_operation and self.current_operation.status in {'draft', 'confirmed'}
                and candidate.content_hash
                and candidate.content_hash == self.current_operation.content_hash
                and [(b.source_event_id, b.title) for b in candidate.blocks]
                    == [(b.source_event_id, b.title) for b in self.current_operation.blocks]
                and all(block.id in self.current_operation.calendar_event_ids
                        for block in self.current_operation.blocks)):
            return self.draft_sync.preview(self.current_operation.id) + '\n\nПлан не изменился.'
        was_confirmed = bool(self.current_operation and self.current_operation.status == 'confirmed')
        if self.current_operation and self.current_operation.status == 'draft':
            self.rollback()
        if source_changed:
            removed = self.draft_sync.remove_missing_sources(event.id for event in events)
            if removed:
                self.calendar_projector.delete_owned_events(
                    self.current_operation.calendar_id if self.current_operation else None,
                    removed.values(),
                )
        self.current_operation = candidate
        self.draft_sync.stage(candidate)
        self.operation_store.save(candidate)
        preview = self.draft_sync.preview(candidate.id)
        if was_projected:
            self.stage()
            if was_confirmed:
                self.confirm()
                return (
                    'Подтверждённые подготовки обновлены после изменения TPU. '
                    'Старые времена заменены только у связанных занятий.\n\n' + preview
                )
            return 'Черновики автоматически обновлены после изменения.\n\n' + preview
        return preview

    def feedback(
        self,
        block_id: str,
        outcome: str,
        actual_minutes: Optional[int] = None,
        difficulty: int = 3,
        comment: str = '',
        energy: Optional[int] = None,
    ) -> str:
        operation = self._current()
        block = next((item for item in operation.blocks if item.id == block_id), None)
        if block is None:
            raise KeyError('Unknown preparation block')
        minutes = actual_minutes
        if minutes is None:
            minutes = block.minutes if outcome == 'done' else block.minutes // 2 if outcome == 'partial' else 0
        feedback = PreparationFeedback(
            block_id, outcome, minutes, difficulty, comment, energy,
        )
        if block_id in operation.feedback_recorded_block_ids:
            return 'Feedback для этого блока уже сохранён.'
        operation.feedback_recorded_block_ids.append(block_id)
        operation.completed_source_event_ids.append(block.source_event_id)
        self.completed_source_event_ids.add(block.source_event_id)
        if outcome in {'partial', 'skipped'}:
            remaining = max(0, block.minutes - minutes)
            course = self._course_from_block(block)
            if remaining:
                self.carryover_minutes_by_course[course] = min(
                    self.planner.profile.max_single_block_minutes,
                    self.carryover_minutes_by_course.get(course, 0) + remaining,
                )
                operation.carryover_minutes_by_course = dict(self.carryover_minutes_by_course)
        self.operation_store.save(operation)
        proposals = self.feedback_service.propose(operation, [feedback])
        explanation = proposals[0].explanation if proposals else (
            'Feedback сохранён; подтверждённые блоки не меняются автоматически.'
        )
        if operation.status == 'draft' and outcome in {'partial', 'skipped'}:
            return explanation + '\n\n' + self.replan()
        return explanation

    @staticmethod
    def _course_from_block(block) -> str:
        title = block.title.removeprefix('Подготовка: ')
        return title.split(' — к ', maxsplit=1)[0].replace(' — остаток', '').strip()

    def _current(self) -> DraftOperation:
        if self.current_operation is None:
            raise ValueError('Сначала постройте preview подготовок.')
        return self.current_operation

    def _record_projection(self, operation: DraftOperation) -> None:
        self.draft_sync.calendar_event_ids.update(operation.calendar_event_ids)

    def _calendar_is_ready(self) -> bool:
        adapter = self.calendar_projector.calendar_adapter
        # Real adapters expose both fields. Lightweight test adapters do not
        # need an async initialization phase and are treated as ready.
        if hasattr(adapter, 'service') and getattr(adapter, 'service') is None:
            return False
        if hasattr(adapter, 'is_initialized') and not adapter.is_initialized:
            return False
        return True

    def _recover_pending(self) -> Optional[DraftOperation]:
        """Recover the latest reversible operation after a bot restart."""
        operations = self.operation_store.load_all()
        for operation in reversed(list(operations.values())):
            if operation.status in {'draft', 'confirmed'}:
                self.draft_sync.operations[operation.id] = operation
                self.draft_sync.current_blocks.update({
                    block.id: block for block in [*operation.blocks, *operation.retained_blocks]
                })
                self.draft_sync.calendar_event_ids.update(operation.calendar_event_ids)
                return operation
        return None
