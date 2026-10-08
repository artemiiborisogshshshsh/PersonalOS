"""Confirmed, class-only recovery after the pinned University calendar vanished."""

from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from services.draft_operation_store import DraftOperationStore
from services.published_plan_updates import PlanConflict, decode, digest


class ClassCalendarRecoveryError(PlanConflict):
    """Recovery cannot prove that a class-only replacement is safe."""


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                prefix='.class-calendar-recovery-', delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, sort_keys=True)
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


def journal_path(app) -> Path:
    return app.directory / 'class-calendar-recovery.json'


def _class_rows(plan):
    rows = plan.get('rows', {})
    return {uid: row for uid, row in rows.items() if row.get('kind') == 'class'}


def saved_row(plan, uid):
    decision = plan.get('manual_resolutions', {}).get(uid, {})
    return deepcopy(decision.get('row') or plan['rows'][uid])


def _validate_class_only(app, operation, old_id):
    if (operation.status != 'confirmed' or operation.projection_pending
            or operation.pending_plan_update or not operation.published_plan
            or operation.content_hash == ''):
        raise ClassCalendarRecoveryError('Опубликованный план нельзя восстановить автоматически; нужен оператор.')
    plan = operation.published_plan
    if plan.get('calendar_id') != old_id:
        raise ClassCalendarRecoveryError('Привязка календаря не совпала со снимком плана; нужен оператор.')
    if any(row.get('kind') != 'class' for row in plan.get('rows', {}).values()):
        raise ClassCalendarRecoveryError(
            'В удалённом календаре есть подготовки. Их восстановление требует проверки оператором.')
    if (operation.blocks or operation.retained_blocks or operation.previous_blocks
            or operation.calendar_ids
            or operation.calendar_event_ids or operation.previous_calendar_event_ids
            or operation.pending_calendar_writes):
        raise ClassCalendarRecoveryError(
            'Найдены активные проекции подготовок; восстановление требует проверки оператором.')
    if any(identifier == old_id for identifier in operation.calendar_ids.values()):
        raise ClassCalendarRecoveryError(
            'Снимок плана содержит привязку подготовки к удалённому календарю; нужен оператор.')
    class_rows = _class_rows(plan)
    if not class_rows:
        raise ClassCalendarRecoveryError('Нет сохранённых пар для проверки владельца календаря.')
    checkpoints = app.classes.projection_state.rows
    for uid in class_rows:
        checkpoint = checkpoints.get(uid)
        if not isinstance(checkpoint, dict) or checkpoint.get('calendar_id') != old_id:
            raise ClassCalendarRecoveryError(
                'Владение календарём парами не подтверждено сохранёнными отметками.')
    for uid, checkpoint in checkpoints.items():
        if checkpoint.get('calendar_id') not in (None, old_id):
            raise ClassCalendarRecoveryError(
                'Есть неоднозначные привязки в отметках пар; нужен оператор.')


def _existing_record(app, operation):
    path = journal_path(app)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('version') != 1 or record.get('kind') != 'class-only-calendar-recovery':
        raise ClassCalendarRecoveryError('Формат журнала восстановления неизвестен; нужен оператор.')
    if record.get('complete'):
        return None
    saved = record.get('operation_before')
    after = record.get('operation_after')
    current = DraftOperationStore._serialize(operation)
    if current == after and after is not None:
        # Preview must remain read-only; explicit apply can finish this state.
        return None
    if current != saved:
        raise ClassCalendarRecoveryError(
            'План изменился во время восстановления календаря; нужен оператор.')
    return record


def _eligible_ids(app, plan, selected, now):
    deleted = {uid for uid, decision in plan.get('manual_resolutions', {}).items()
               if decision.get('kind') == 'deleted'}
    deleted.update(uid for uid, checkpoint in app.classes.projection_state.rows.items()
                   if checkpoint.get('override') == 'deleted')
    current = set(selected) - deleted
    classes = _class_rows(plan)
    if not current.issubset(classes):
        raise ClassCalendarRecoveryError(
            'Есть выбранные занятия без привязки в опубликованном снимке; нужен оператор.')
    horizon = app.profile.load().profile.planning_horizon_end(now)
    return {uid for uid in current if now < decode(saved_row(plan, uid)['data']).dtstart < horizon}


def inspect_recovery(app, operation, events, selected, signature, now):
    """Return a read-only proposal payload when a confirmed class-only calendar is 404."""
    if operation is None or not operation.published_plan:
        return None
    path = journal_path(app)
    record = _existing_record(app, operation)
    if record is not None:
        if record['signature'] != signature:
            raise ClassCalendarRecoveryError(
                'Источник или настройки изменились во время восстановления; нужен оператор.')
        selected_ids = set(record['selected_ids'])
        if selected_ids != _eligible_ids(app, record['plan_before'], selected, now):
            raise ClassCalendarRecoveryError(
                'Список пар изменился во время восстановления; нужен новый разбор оператором.')
        return {'recovery_id': record['recovery_id'], 'base_hash': record['base_hash'],
                'signature': signature, 'selected_ids': sorted(selected_ids), 'resuming': True,
                'operation_snapshot': record['operation_before']}

    old_id = operation.published_plan.get('calendar_id')
    if not old_id:
        return None
    checker = getattr(app.adapter, 'calendar_is_accessible', None)
    if not callable(checker):
        return None
    try:
        available = checker(old_id)
    except Exception as error:
        raise ClassCalendarRecoveryError(
            'Не удалось проверить доступ к календарю пар; восстановление остановлено.') from error
    # Existing-calendar previews belong to the ordinary plan-update flow;
    # recovery-only source guards must not intercept manual update decisions.
    if available is not False:
        return None
    if operation.content_hash != signature:
        raise ClassCalendarRecoveryError(
            'Источник или настройки отличаются от подтверждённого снимка; нужен оператор.')
    _validate_class_only(app, operation, old_id)
    base = operation.published_plan
    selected_ids = _eligible_ids(app, base, selected, now)
    class_rows = _class_rows(base)
    if not selected_ids:
        raise ClassCalendarRecoveryError(
            'Выбранные будущие пары не совпали с опубликованным снимком; нужен оператор.')
    # Only rows whose saved, possibly manually moved time is still future and
    # inside the already approved horizon are eligible for republishing.
    return {'recovery_id': None, 'base_hash': digest(base), 'signature': signature,
            'selected_ids': sorted(selected_ids), 'old_id': old_id, 'resuming': False,
            'operation_snapshot': DraftOperationStore._serialize(operation)}


def _expected_rebound(rows, old_id, new_id):
    result = deepcopy(rows)
    for row in result.values():
        bound = row.get('calendar_id')
        if bound != old_id:
            continue
        row.pop('event_id', None)
        row.pop('pending', None)
        row['calendar_id'] = new_id
        row['calendar_recovery'] = new_id
    return result


def _rebind_checkpoints(app, record):
    state = app.classes.projection_state
    before = record['checkpoint_before']
    current = deepcopy(state.rows)
    expected = _expected_rebound(before, record['old_id'], record['new_id'])
    if current == expected:
        return
    if (current.keys() != before.keys()
            or not all(current[key] in (before[key], expected[key]) for key in before)):
        raise ClassCalendarRecoveryError(
            'Отметки пар изменились во время восстановления; нужен оператор.')
    app.classes.rebind_recovered_calendar({record['old_id']}, record['new_id'])


def _finish_plan(app, operation, record, signature):
    old_plan = deepcopy(record['plan_before'])
    historical = deepcopy(old_plan.get('recovery_history', {}))
    manual = old_plan.get('manual_resolutions', {})
    archived = {}
    active_rows = {}
    for uid, row in old_plan['rows'].items():
        row = saved_row(old_plan, uid)
        if uid in record['applied']:
            remote = app.adapter.get_event_by_uid(record['new_id'], uid, strict=True)
            if not app.updates.matches(remote, row['data']) or not remote.get('etag'):
                raise ClassCalendarRecoveryError(
                    'Опубликованная пара не прошла итоговую проверку; нужен оператор.')
            updated = deepcopy(row)
            updated.update(event_id=remote['id'], etag=remote['etag'])
            active_rows[uid] = updated
        else:
            archived[uid] = {'row': deepcopy(row),
                             'resolution': deepcopy(manual.get(uid)),
                             'reason': 'manual-deletion' if uid in manual and manual[uid].get('kind') == 'deleted'
                             else 'frozen-or-not-selected'}
    historical.update(archived)
    plan = deepcopy(old_plan)
    plan['calendar_id'] = record['new_id']
    plan['rows'] = active_rows
    plan['recovery_history'] = historical
    plan['version'] = operation.version + 1
    plan['source_resolutions'] = sorted(set(plan.get('source_resolutions', [])) | set(archived))
    plan['manual_resolutions'] = {
        uid: deepcopy(decision) for uid, decision in manual.items() if uid in active_rows
    }
    candidate = DraftOperationStore._deserialize(record['operation_before'])
    candidate.status = 'confirmed'
    candidate.projection_pending = False
    candidate.pending_plan_update = {}
    candidate.version = plan['version']
    candidate.content_hash = signature
    candidate.calendar_id = record['new_id']
    candidate.calendar_ids = {route: (record['new_id'] if value == record['old_id'] else value)
                              for route, value in candidate.calendar_ids.items()}
    candidate.calendar_event_ids = {}
    candidate.published_plan = plan
    record['operation_after'] = DraftOperationStore._serialize(candidate)
    _save(journal_path(app), record)
    app.operations.save(candidate)
    operation.__dict__.update(candidate.__dict__)


def apply_recovery(app, pending):
    """Resolve a fresh explicit proposal, then durably republish approved future class rows."""
    _, _, _, payload, operation_id = pending
    path = journal_path(app)
    operation = app.operations.load(operation_id)
    settings, now, events, signature, _ = app._inputs()
    if signature != payload['signature'] or digest(operation.published_plan) != payload['base_hash']:
        raise ClassCalendarRecoveryError(
            'Источник или опубликованный план изменился после preview; записи не было.')
    if operation.content_hash != signature:
        raise ClassCalendarRecoveryError(
            'Источник отличается от подтверждённого снимка; записи не было.')
    if DraftOperationStore._serialize(operation) != payload.get('operation_snapshot'):
        raise ClassCalendarRecoveryError(
            'Операция изменилась после preview; записи не было.')
    if operation.pending_plan_update or operation.projection_pending:
        raise ClassCalendarRecoveryError('Есть незавершённая запись; нужен оператор.')
    selected = {event.id for event in events
                if event.state.value in {'confirmed', 'moved'}
                and now <= event.start_time < settings.profile.planning_horizon_end(now)}
    allowed = set(payload['selected_ids'])
    if not allowed.issubset(selected):
        raise ClassCalendarRecoveryError(
            'Будущие пары изменились после preview; записи не было.')
    record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if (record is not None and not record.get('complete')
            and DraftOperationStore._serialize(operation) == record.get('operation_after')
            and record.get('operation_after') is not None):
        # The previous recovery's final operation save succeeded but its final
        # journal marker did not. This is a new explicit recovery proposal.
        record['complete'] = True
        _save(path, record)
        record = None
    if record is None or record.get('complete'):
        old_id = payload.get('old_id')
        if not old_id:
            raise ClassCalendarRecoveryError('В журнале отсутствует исходная привязка календаря; нужен оператор.')
        _validate_class_only(app, operation, old_id)
        checker = getattr(app.adapter, 'calendar_is_accessible', None)
        if not callable(checker) or checker(old_id) is not False:
            raise ClassCalendarRecoveryError(
                'Удаление календаря не подтверждено; записи не было.')
        record = {
            'version': 1, 'kind': 'class-only-calendar-recovery', 'complete': False,
            'recovery_id': payload.get('recovery_id') or os.urandom(12).hex(),
            'old_id': old_id, 'new_id': None, 'creation_requested': False,
            'base_hash': payload['base_hash'], 'signature': signature,
            'selected_ids': sorted(allowed), 'attempted': [], 'applied': {},
            'checkpoint_before': deepcopy(app.classes.projection_state.rows),
            'operation_before': DraftOperationStore._serialize(operation),
            'operation_after': None, 'plan_before': deepcopy(operation.published_plan),
        }
        _save(path, record)
    elif (record.get('recovery_id') != payload.get('recovery_id')
          or record.get('base_hash') != payload['base_hash']
          or record.get('signature') != signature
          or set(record.get('selected_ids', [])) != allowed):
        raise ClassCalendarRecoveryError('Журнал восстановления не совпал с подтверждением; нужен оператор.')
    else:
        old_id = record.get('old_id')
        if DraftOperationStore._serialize(operation) != record.get('operation_before'):
            raise ClassCalendarRecoveryError(
                'Операция изменилась после начала восстановления; дальнейшая запись остановлена.')
        if set(record.get('selected_ids', [])) != _eligible_ids(
                app, record['plan_before'], selected, now):
            raise ClassCalendarRecoveryError(
                'Будущие выбранные пары изменились; дальнейшая запись остановлена.')
        checker = getattr(app.adapter, 'calendar_is_accessible', None)
        if not callable(checker) or checker(old_id) is not False:
            raise ClassCalendarRecoveryError(
                'Удаление исходного календаря не подтверждено; дальнейшая запись остановлена.')

    if record.get('new_id') is None:
        def before_create():
            record['creation_requested'] = True
            _save(path, record)
        new_id = app.adapter.replacement_calendar(
            record['old_id'], 'Personal University Schedule',
            allow_create=not record['creation_requested'], before_create=before_create)
        record['new_id'] = new_id
        _save(path, record)
    checker = getattr(app.adapter, 'calendar_is_accessible', None)
    try:
        replacement_available = checker(record['new_id']) if callable(checker) else None
    except Exception as error:
        raise ClassCalendarRecoveryError(
            'Не удалось проверить новый календарь; запись остановлена.') from error
    if replacement_available is not True:
        raise ClassCalendarRecoveryError(
            'Новый календарь не подтверждён как доступный; запись остановлена.')
    _rebind_checkpoints(app, record)
    horizon = settings.profile.planning_horizon_end(now)
    rows = record['plan_before']['rows']
    for uid in record['selected_ids']:
        row = saved_row(record['plan_before'], uid)
        data = decode(row['data'])
        if data.dtstart <= now or data.dtstart >= horizon:
            raise ClassCalendarRecoveryError(
                'Начало пары изменилось до записи; дальнейшее восстановление остановлено.')
        if uid in record['applied']:
            continue
        if uid in record['attempted']:
            remote = app.adapter.get_event_by_uid(record['new_id'], uid, strict=True)
            if not app.updates.matches(remote, row['data']):
                raise ClassCalendarRecoveryError(
                    'Результат прежней записи не подтверждён; повторная вставка запрещена.')
            result = remote['id']
        else:
            record['attempted'].append(uid)
            _save(path, record)
            def before_write():
                app._check_write_time([data.dtstart], settings.profile, horizon)
            result = app.classes._write_verified(
                record['new_id'], uid, data, 'insert', None,
                allow_insert_retry=False, before_write=before_write)
        record['applied'][uid] = result
        _save(path, record)
    _finish_plan(app, operation, record, signature)
    record['complete'] = True
    _save(path, record)
    return app.reply('Удалённый календарь восстановлен после подтверждения. Будущие выбранные пары опубликованы; проверь новый /weekly_preview.')
