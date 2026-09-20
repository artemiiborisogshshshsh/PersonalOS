"""Durable progress across the study and work Calendar projections."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from services.weekly_plan_service import FixedCommitment
from services.calendar_integrity_service import CalendarIntegrityService
from services.draft_operation_store import DraftOperationStore
import uuid
from dataclasses import replace
from copy import deepcopy
from services.update_all_workflow import UpdateAllBlocked
from services.calendar.projection_state import delete_owned_verified


class SharedPreparationWorkflow:
    """Resume an interrupted queue without rebuilding its completed half.

    This is a resumable sequence, not an atomic Google transaction. The
    individual workflows retain their block snapshots and partial writes.
    """

    def __init__(self, path: Path, study, work):
        self.path = path
        self.study = study
        self.work = work

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                    prefix='.shared-preparation-', delete=False) as output:
                temporary = Path(output.name)
                json.dump(state, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def retire_retained(workflow, authorize, horizon, *, study=False):
        """Retire explicitly cancelled/excluded current or retained projections.

        authorize(scope, source_id) must check a verified source or explicit
        user rule, returning source_cancelled/user_excluded, never infer from
        absence in a candidate. Each successful deletion is checkpointed.
        """
        operation = workflow.current_operation
        if operation is None:
            return 0
        sync = workflow.draft_sync if study else workflow.sync
        store = workflow.operation_store if study else workflow.store
        projector = workflow.calendar_projector if study else workflow.projector
        removed = 0
        for block in [*operation.blocks, *operation.retained_blocks]:
            if (block.status == 'completed'
                    or block.source_event_id in operation.completed_source_event_ids):
                continue
            if not CalendarIntegrityService._overlaps((block.start, block.end), horizon):
                continue
            reason = authorize(operation.scope, block.source_event_id)
            if reason not in {'source_cancelled', 'user_excluded'}:
                continue
            identifier = operation.calendar_event_ids.get(block.id)
            calendar = operation.calendar_ids.get(block.calendar.value)
            if not calendar and study:
                calendar = operation.calendar_id
            if not identifier or not calendar:
                raise RuntimeError('Удаление подготовки не подтверждено: нет сохранённого адреса события.')
            reader = getattr(projector.calendar_adapter, 'get_event_by_id', None)
            if not callable(reader):
                raise RuntimeError('Удаление подготовки не подтверждено: строгая проверка недоступна.')
            try:
                remote = reader(calendar, identifier, strict=True)
            except Exception:
                raise RuntimeError('Удаление подготовки не подтверждено: Calendar недоступен.') from None
            if remote is None:
                deleted = True
            else:
                private = remote.get('extendedProperties', {}).get('private', {})
                if (private.get('personal_os_block_id') != block.id
                        or private.get('personal_os_operation_id') != operation.id):
                    raise RuntimeError('Удаление подготовки остановлено: владелец не подтверждён.')
                if projector._has_manual_time_override(remote, block):
                    operation.manual_calendar_overrides[block.id] = {
                        'start': str(remote['start']['dateTime']),
                        'end': str(remote['end']['dateTime']),
                    }
                    store.save(operation)
                    continue
                deleted = delete_owned_verified(
                    projector.calendar_adapter, calendar, identifier,
                    lambda event: event.get('extendedProperties', {}).get('private', {})
                    .get('personal_os_block_id') == block.id
                    and event.get('extendedProperties', {}).get('private', {})
                    .get('personal_os_operation_id') == operation.id,
                )
            if not deleted:
                raise RuntimeError('Удаление подготовки не подтверждено Google Calendar.')
            candidate = deepcopy(operation)
            candidate.blocks = [item for item in candidate.blocks if item.id != block.id]
            candidate.retained_blocks = [item for item in candidate.retained_blocks if item.id != block.id]
            candidate.retired_blocks.append(block)
            candidate.calendar_event_ids.pop(block.id, None)
            store.save(candidate)
            workflow.current_operation = operation = candidate
            sync.operations[candidate.id] = candidate
            sync.current_blocks.pop(block.id, None)
            sync.calendar_event_ids.pop(block.id, None)
            removed += 1
        return removed

    @staticmethod
    def _id(workflow):
        return workflow.current_operation.id if workflow.current_operation else None

    def _publish(self, workflow, previous_id, candidate, study=False):
        operation = workflow.current_operation
        sync = workflow.draft_sync if study else workflow.sync
        store = workflow.operation_store if study else workflow.store
        if operation is None or operation.id == previous_id:
            if operation is not None and self._same_blocks(operation, candidate) and not operation.projection_pending:
                if all(block.id in operation.calendar_event_ids for block in operation.blocks):
                    return sync.preview(operation.id)
            # Preserve original event IDs and snapshots. No rollback or second
            # calculation: publish precisely the validated candidate.
            sync.stage(candidate)
            if operation is not None:
                candidate.calendar_ids = dict(operation.calendar_ids)
                candidate.calendar_id = operation.calendar_id
                candidate.retired_blocks = list(operation.retired_blocks)
                replaced_sources = {block.source_event_id for block in candidate.blocks}
                candidate.retained_blocks = [block for block in
                    [*operation.blocks, *operation.retained_blocks]
                    if block.source_event_id not in replaced_sources
                    and block.id in operation.calendar_event_ids]
                candidate.calendar_event_ids.update({block.id: operation.calendar_event_ids[block.id]
                                                      for block in candidate.retained_blocks})
                if operation.status == 'confirmed':
                    candidate.blocks = [replace(block, status='confirmed') for block in candidate.blocks]
            candidate.projection_pending = True
            store.save(candidate)
            workflow.current_operation = candidate
        operation = workflow.current_operation
        if operation is None:
            raise RuntimeError('Не удалось сохранить часть общего плана.')
        if operation.projection_pending or (
            operation.status == 'draft'
            and any(block.id not in operation.calendar_event_ids for block in operation.blocks)
        ):
            reply = workflow.stage()
            if operation.blocks and all(block.status == 'confirmed' for block in operation.blocks):
                sync.confirm(operation.id)
                store.save(operation)
            return reply
        return sync.preview(operation.id)

    @staticmethod
    def _same_blocks(left, right):
        def signature(operation):
            return sorted((b.id, b.source_event_id, b.title, b.start, b.end,
                           b.minutes, b.calendar.value, b.color,
                           getattr(b, 'manual_conflict', False)) for b in operation.blocks)
        return signature(left) == signature(right)

    @staticmethod
    def _preserve_manual_actions(previous, candidate):
        """Carry a user's Calendar choice into an automatic source refresh."""
        if previous is None:
            return
        prior = {block.source_event_id: block for block in [*previous.blocks, *previous.retained_blocks]}
        retained = []
        for block in candidate.blocks:
            old = prior.get(block.source_event_id)
            if old is None:
                retained.append(block)
            elif old.id in previous.manually_deleted_block_ids:
                candidate.no_slot_reasons[block.source_event_id] = 'подготовка удалена пользователем вручную'
                candidate.explanations.append(
                    f'{block.title}: пользователь удалил подготовку вручную; автоматически она не восстановлена.'
                )
            elif old.id in previous.manual_calendar_overrides:
                retained.append(old)
                candidate.explanations.append(
                    f'{old.title}: сохранён ручной перенос в Calendar; автоматическое время не заменено.'
                )
            else:
                retained.append(block)
        candidate.blocks = retained

    @staticmethod
    def _is_manual_conflict(block):
        """Return true only for the explicit, serialized fallback marker."""
        return getattr(block, 'manual_conflict', None) is True

    @staticmethod
    def _as_manual_conflict(block):
        """Mark an existing proposal without changing its stable identity."""
        if SharedPreparationWorkflow._is_manual_conflict(block):
            return block
        return replace(block, manual_conflict=True)

    @classmethod
    def _classify_conflicts(cls, candidates, protected):
        """Turn unavoidable overlaps into visible, manual-only proposals.

        A planner may find a locally valid slot which is occupied by the
        other scope or by a fresh Calendar read.  Such a block must never be
        silently published as an ordinary preparation.  Marking keeps its
        source, duration, time and ID intact so feedback and existing Calendar
        ownership continue to point at the same block.
        """
        for candidate in candidates:
            candidate.blocks = [
                cls._as_manual_conflict(block)
                if (not cls._is_manual_conflict(block)
                    and any(CalendarIntegrityService._overlaps((block.start, block.end), other)
                            for other in protected))
                else block
                for block in candidate.blocks
            ]

        # A normal block may collide with a proposal made conflicting by the
        # previous pass.  Propagate the explicit classification until there is
        # no ordinary block hidden behind a conflict proposal.
        changed = True
        while changed:
            changed = False
            manual_intervals = [
                (block.start, block.end)
                for candidate in candidates for block in candidate.blocks
                if cls._is_manual_conflict(block)
            ]
            for candidate in candidates:
                updated = []
                for block in candidate.blocks:
                    if (not cls._is_manual_conflict(block)
                            and any(CalendarIntegrityService._overlaps(
                                (block.start, block.end), interval)
                                for interval in manual_intervals)):
                        updated.append(cls._as_manual_conflict(block))
                        changed = True
                    else:
                        updated.append(block)
                candidate.blocks = updated

    def calculate(self):
        """Calculate both halves without saving operations or touching Calendar."""
        operations = (self.study.current_operation, self.work.current_operation)
        for workflow, study in ((self.study, True), (self.work, False)):
            operation = workflow.current_operation
            if operation is None:
                continue
            projector = workflow.calendar_projector if study else workflow.projector
            store = workflow.operation_store if study else workflow.store
            projector.capture_manual_actions(operation, checkpoint=store.save)
        owned_ids = {identifier for op in operations if op is not None
                     for identifier in op.calendar_event_ids.values()}

        def external(provider):
            return [item for item in provider()
                    if item.metadata.get('google_event_id') not in owned_ids]

        study_commitments = external(self.study.commitments_provider)
        work_commitments = external(self.work.commitments_provider)
        study = self.study.planner.build_draft(
            list(self.study.events_provider()), now=self.study.now_provider(),
            fixed_commitments=study_commitments,
            flexible_items=list(self.study.flexible_items_provider()),
            completed_source_event_ids=self.study.completed_source_event_ids,
            carryover_minutes_by_course=self.study.carryover_minutes_by_course,
        )
        self._preserve_manual_actions(operations[0], study)
        # Use the freshly calculated study plan, not a Calendar write, as
        # the work planner's occupancy and Saturday ordering boundary.
        study_busy = [FixedCommitment(
            'shared-study:' + block.id, block.title, block.start, block.end,
            metadata={'preparation_scope': 'university-preparation'},
        ) for block in study.blocks if not self._is_manual_conflict(block)]
        work = self.work.planner.build_draft(
            list(self.work.lessons_provider()), list(self.work.university_provider()),
            [*work_commitments, *study_busy], self.work.now_provider(),
        )
        self._preserve_manual_actions(operations[1], work)
        blocks = [*study.blocks, *work.blocks]
        protected = [(item.start, item.end)
                     for item in [*study_commitments, *work_commitments]]
        # Omitted old blocks have not been authorised for deletion here.
        # Keep their occupancy until a source-aware cleanup handles them.
        for old, candidate in zip(operations, (study, work)):
            if old is None:
                continue
            replaced_sources = {block.source_event_id for block in candidate.blocks}
            protected.extend((block.start, block.end) for block in [*old.blocks, *old.retained_blocks]
                             if block.source_event_id not in replaced_sources
                             and not self._is_manual_conflict(block)
                             and block.id in old.calendar_event_ids)
        self._classify_conflicts((study, work), protected)
        blocks = [*study.blocks, *work.blocks]
        for index, block in enumerate(blocks):
            interval = (block.start, block.end)
            if block.end <= block.start:
                raise RuntimeError('Общий план: недопустимая длительность подготовки.')
            # Explicitly authorized fallback proposals are visible conflicts,
            # not successful free-slot placements. Only those may overlap.
            if self._is_manual_conflict(block):
                continue
            if any(CalendarIntegrityService._overlaps(interval, other) for other in protected):
                raise RuntimeError('Общий план: подготовка пересекает обязательное занятое время.')
            if any(CalendarIntegrityService._overlaps(interval, (other.start, other.end))
                   for other in blocks[:index] if not self._is_manual_conflict(other)):
                raise RuntimeError('Общий план: подготовки пересекаются; Calendar не изменён.')
        # Validate the transition as well as the final layout. Within each
        # scope move blocks that have a free destination first. Cycles and
        # cross-scope dependencies requiring work-before-study fail closed.
        occupied = [(scope, block) for scope, old in enumerate(operations) if old
                    for block in [*old.blocks, *old.retained_blocks,
                                  *(old.previous_blocks if old.projection_pending else [])]
                    if block.id in old.calendar_event_ids or block.id in old.previous_calendar_event_ids]
        for scope, candidate in enumerate((study, work)):
            pending, ordered = list(candidate.blocks), []
            while pending:
                ready = next((block for block in pending if self._is_manual_conflict(block) or not any(
                    (other_scope != scope or other.source_event_id != block.source_event_id)
                    and not self._is_manual_conflict(other)
                    and CalendarIntegrityService._overlaps((block.start, block.end), (other.start, other.end))
                    for other_scope, other in occupied)), None)
                if ready is None:
                    raise UpdateAllBlocked('переносы зависят друг от друга; безопасный порядок не найден, подготовки не изменены')
                occupied = [(other_scope, other) for other_scope, other in occupied
                            if other_scope != scope or other.source_event_id != ready.source_event_id]
                occupied.append((scope, ready))
                ordered.append(ready)
                pending.remove(ready)
            candidate.blocks = ordered
        return study, work

    def _unchanged(self, candidates=None):
        operations = (self.study.current_operation, self.work.current_operation)
        if any(op is None or op.status not in {'draft', 'confirmed'} or op.projection_pending
               or any(b.id not in op.calendar_event_ids for b in op.blocks)
               for op in operations):
            return False
        candidates = candidates or self.calculate()
        return all(self._same_blocks(candidate, operation)
                   for candidate, operation in zip(candidates, operations))

    def run(self):
        # In particular, a work-source or busy-read failure cannot roll back
        # or publish the study half before the work half has been calculated.
        candidates = self.calculate()
        state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else None
        if state is not None and state.get('version') == 1:
            if state.get('phase') != 'complete':
                raise RuntimeError('Незавершённый старый план: требуется проверка перед миграцией; Calendar не изменён.')
            state = None
        if state is not None and (state.get('version') != 2 or state.get('phase') not in {
            'study', 'work', 'complete',
        }):
            raise ValueError('Неизвестный формат журнала общего плана.')
        # A user may have edited/rebuilt the study plan while work was
        # interrupted. Start against that new baseline, not the stale half.
        if (state and state['phase'] == 'work' and state.get('study_result')
                and state['study_result'] != self._id(self.study)):
            state = None
        if state is None or state['phase'] == 'complete':
            if self._unchanged(candidates):
                return (
                    self.study.draft_sync.preview(self.study.current_operation.id) + '\n\nУчебный план не изменился.',
                    self.work.sync.preview(self.work.current_operation.id) + '\n\nРабочий план не изменился.',
                )
            state = dict(version=2, run_id=uuid.uuid4().hex, phase='study',
                         study_before=self._id(self.study), work_before=self._id(self.work),
                         study_reply='', work_reply='', candidates=[
                             DraftOperationStore._serialize(item) for item in candidates])
            self._save(state)
        saved = [DraftOperationStore._deserialize(item) for item in state['candidates']]
        if not all(self._same_blocks(old, fresh) for old, fresh in zip(saved, candidates)):
            raise RuntimeError('Условия незавершённого плана изменились; нужна повторная проверка перед записью.')
        if state['phase'] == 'study':
            state['study_reply'] = self._publish(self.study, state['study_before'], saved[0], study=True)
            state['study_result'] = self._id(self.study)
            state['phase'] = 'work'
            self._save(state)
        if state['phase'] == 'work':
            state['work_reply'] = self._publish(self.work, state['work_before'], saved[1])
            state['phase'] = 'complete'
            self._save(state)
        return state['study_reply'], state['work_reply']
