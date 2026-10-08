"""Resumable rebinding after an app preparation calendar was removed."""

from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import uuid

from services.draft_operation_store import DraftOperationStore
from services.update_all_workflow import UpdateAllBlocked


def _save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                prefix='.calendar-recovery-', delete=False) as output:
            temporary = Path(output.name)
            json.dump(state, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _rebound(original, replacements):
    operation = deepcopy(original)
    affected_routes = {route for route, identifier in operation.calendar_ids.items()
                       if identifier in replacements}
    if operation.calendar_id in replacements:
        affected_routes.add('Учёба')
    blocks = [*operation.blocks, *operation.retained_blocks, *operation.previous_blocks]
    affected = {block.id for block in blocks if block.calendar.value in affected_routes}
    for route in affected_routes:
        old = operation.calendar_ids.get(route) or operation.calendar_id
        operation.calendar_ids[route] = replacements[old]
    if operation.calendar_id in replacements:
        operation.calendar_id = replacements[operation.calendar_id]
    for mapping in (operation.calendar_event_ids, operation.previous_calendar_event_ids,
                    operation.pending_calendar_writes):
        for block_id in affected:
            mapping.pop(block_id, None)
    operation.created_calendar_block_ids = [key for key in operation.created_calendar_block_ids if key not in affected]
    operation.updated_calendar_block_ids = [key for key in operation.updated_calendar_block_ids if key not in affected]
    # A manual move must be rebuilt at its chosen time, not forgotten merely
    # because the enclosing calendar disappeared. Individual deletions persist.
    for block_id in affected & operation.manual_calendar_overrides.keys():
        if block_id not in operation.manually_deleted_block_ids:
            operation.pending_calendar_writes[block_id] = 'insert'
    # Retain local history, but do not claim vanished events are still published.
    history = {block.id: block for block in operation.retired_blocks}
    history.update({block.id: block for block in blocks if block.id in affected})
    completed = set(operation.completed_source_event_ids)
    completed.update(block.source_event_id for block in blocks if block.id in affected and block.status == 'completed')
    operation.completed_source_event_ids = sorted(completed)
    operation.blocks = [block for block in operation.blocks if block.id not in affected]
    if affected:
        operation.projection_pending = True
    operation.retired_blocks = list(history.values())
    operation.retained_blocks = [block for block in operation.retained_blocks if block.id not in affected]
    return operation


def _class_checkpoint_rows(class_sync):
    """Return rows that prove ownership of the class calendar binding."""
    state = getattr(class_sync, 'projection_state', None)
    if state is None:
        return {}
    rows = getattr(state, 'rows', {})
    return {key: deepcopy(row) for key, row in rows.items()
            if isinstance(row, dict) and row.get('calendar_id')}


def recover_preparation_calendars(queue, adapter, work_service=None, class_sync=None):
    """Rebind only unavailable routes, then let the normal planners validate writes.

    A backup precedes any provider mutation. An uncertain create is discovered
    by name on retry, never blindly repeated. No old Calendar event is deleted.
    """
    path = queue.path.with_name(queue.path.stem + '.calendar-recovery.json')
    record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    workflows = ((queue.study, queue.study.operation_store, queue.study.draft_sync),
                 (queue.work, queue.work.store, queue.work.sync))
    if record is None or record['complete']:
        missing, checked = {}, set()
        for workflow, _, _ in workflows:
            operation = workflow.current_operation
            if operation is None:
                continue
            projector = getattr(workflow, 'calendar_projector', None) or workflow.projector
            for block in [*operation.blocks, *operation.retained_blocks, *operation.previous_blocks]:
                identifier = operation.calendar_ids.get(block.calendar.value)
                if not identifier and block.calendar.value == 'Учёба':
                    identifier = operation.calendar_id
                if not identifier or identifier in checked:
                    continue
                checked.add(identifier)
                checker = getattr(adapter, 'calendar_is_accessible', None)
                if not callable(checker):
                    continue
                try:
                    available = checker(identifier)
                except Exception as error:
                    projector._raise_safe_read_block(error)
                if available is False:
                    missing[identifier] = {'name': projector._calendar_name_for(block),
                                           'new_id': None, 'creation_requested': False}
        class_missing = {}
        class_rows = _class_checkpoint_rows(class_sync) if class_sync is not None else {}
        class_ids = sorted({row['calendar_id'] for row in class_rows.values()
                            if row.get('calendar_id')})
        for identifier in class_ids:
            if identifier in checked:
                continue
            checked.add(identifier)
            checker = getattr(adapter, 'calendar_is_accessible', None)
            if not callable(checker):
                continue
            try:
                available = checker(identifier)
            except Exception as error:
                raise UpdateAllBlocked(
                    'Не удалось проверить календарь пар. Сохранённые отметки не изменены.') from error
            if available is False:
                class_missing[identifier] = {
                    'name': 'Personal University Schedule', 'new_id': None,
                    'creation_requested': False,
                }
        if not missing and not class_missing:
            return False
        if record is not None:
            os.replace(path, path.with_name(path.stem + '-' + uuid.uuid4().hex + '.json'))
        record = dict(version=1, complete=False, calendars=missing,
                      class_calendars=class_missing,
                      class_projection_before=deepcopy(
                          class_sync.projection_state.rows) if class_missing else None,
                      queue_before=json.loads(queue.path.read_text(encoding='utf-8')) if queue.path.exists() else None,
                      work_before=deepcopy(work_service.state.__dict__) if work_service else None,
                      operations=[DraftOperationStore._serialize(workflow.current_operation)
                                  if workflow.current_operation else None for workflow, _, _ in workflows])
        _save(path, record)
    replacements = {}
    for old_id, item in {**record.get('calendars', {}),
                         **record.get('class_calendars', {})}.items():
        if item['new_id'] is None:
            allow_create = not item['creation_requested']
            def before_create():
                item['creation_requested'] = True
                _save(path, record)
            try:
                item['new_id'] = adapter.replacement_calendar(
                    old_id, item['name'], allow_create=allow_create, before_create=before_create)
            except Exception as error:
                raise UpdateAllBlocked(
                    'Не удалось подтвердить восстановление календаря. Сохранённый план не удалён. '
                    'Проверь доступ и отсутствие нескольких календарей с одинаковым названием; '
                    'повторная команда продолжит восстановление без повторного создания вслепую.') from error
            _save(path, record)
        replacements[old_id] = item['new_id']
    class_old_ids = {old_id for old_id, item in record.get('calendars', {}).items()
                     if item.get('name') == 'Personal University Schedule'}
    class_old_ids.update(record.get('class_calendars', {}).keys())
    if class_sync is not None and class_old_ids:
        state = class_sync.projection_state
        saved_rows = record.get('class_projection_before')
        if saved_rows is None:
            # Backward-compatible recovery of an older preparation journal.
            saved_rows = deepcopy(state.rows)
            record['class_projection_before'] = saved_rows
            _save(path, record)
        current_rows = deepcopy(state.rows)
        expected_rows = deepcopy(saved_rows)
        target_ids = {replacements[old] for old in class_old_ids if old in replacements}
        if len(target_ids) != 1:
            raise UpdateAllBlocked(
                'Новая привязка календаря пар неоднозначна; отметки не изменены.')
        target_id = next(iter(target_ids))
        for row in expected_rows.values():
            bound = row.get('calendar_id')
            if bound and bound not in class_old_ids:
                continue
            new_id = replacements.get(bound) or next(iter(
                replacements[old] for old in class_old_ids if old in replacements), None)
            if not new_id:
                continue
            row.pop('event_id', None)
            row.pop('pending', None)
            row['calendar_id'] = new_id
            row['calendar_recovery'] = new_id
        if current_rows == saved_rows:
            class_sync.rebind_recovered_calendar(class_old_ids, target_id)
        elif current_rows != expected_rows:
            # CalendarProjectionState persists each checkpoint update. Accept
            # a crash midway through this known row-by-row rebind, then finish it.
            partial_rebind = (current_rows.keys() == saved_rows.keys()
                              and all(current_rows[key] in (saved_rows[key], expected_rows[key])
                                      for key in saved_rows))
            if partial_rebind:
                class_sync.rebind_recovered_calendar(class_old_ids, target_id)
            else:
                raise UpdateAllBlocked(
                    'Отметки календаря пар изменились во время восстановления; проверь сохранённое состояние.')
    for saved, (workflow, store, sync) in zip(record['operations'], workflows):
        if saved is None:
            continue
        original = DraftOperationStore._deserialize(saved)
        rebound = _rebound(original, replacements)
        current = workflow.current_operation
        if current is None or DraftOperationStore._serialize(current) not in (
                saved, DraftOperationStore._serialize(rebound)):
            raise UpdateAllBlocked('План изменился во время восстановления календаря; сохранённая копия требует проверки.')
        store.save(rebound)
        workflow.current_operation = rebound
        sync.operations[rebound.id] = rebound
        sync.current_blocks = {block.id: block for block in [*rebound.blocks, *rebound.retained_blocks]}
        sync.calendar_event_ids = dict(rebound.calendar_event_ids)
    if work_service is not None and any(item['name'] == 'Работа' for item in record.get('calendars', {}).values()):
        # Do this before WorkScheduleService treats a missing old event as a
        # manual deletion. Keep real individual overrides and feedback intact.
        for lesson in work_service.state.lessons.values():
            lesson.pop('calendar_event_id', None)
        work_service.state.pending_calendar_writes.clear()
        work_service.state_store.save(work_service.state)
    # Candidates calculated around vanished projections must be recalculated.
    # Preserve the complete previous journal above, including partial progress;
    # the unaffected scope still owns its real Google IDs and is reconciled normally.
    queue._save({'version': 2, 'phase': 'complete', 'calendar_recovery': path.name})
    record['complete'] = True
    _save(path, record)
    return True
