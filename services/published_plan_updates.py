"""Confirmed add/move updates using the existing draft and projection journals.

Source removal needs a separate explicit review. No migration of old publications.
A missing/edited baseline is a
conflict. Pending targets are immutable across restart and require fresh consent.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

from models import PersonalEventState
from services.adaptive_preparation_service import AdaptivePreparationService
from services.calendar_availability_service import CalendarAvailabilityService
from services.draft_operation_store import DraftOperationStore


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def encode(data):
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in data.items()}


def decode(data):
    return SimpleNamespace(**{key: datetime.fromisoformat(value) if key in {'dtstart', 'dtend'} else value
                              for key, value in data.items()})


class PlanConflict(ValueError):
    pass


class PublishedPlanUpdates:
    def __init__(self, app):
        self.app = app

    @staticmethod
    def matches(remote, data):
        if not remote or not remote.get('id') or remote.get('status') == 'cancelled':
            return False
        desired = decode(data)
        private = remote.get('extendedProperties', {}).get('private', {})
        from services.calendar.personal_event_sync_service import PersonalEventSyncService
        return (remote.get('iCalUID') == desired.uid
                and all(private.get(key) == getattr(desired, attribute) for key, attribute in (
                    ('personal_os_block_id', 'system_block_id'),
                    ('personal_os_operation_id', 'system_operation_id'),
                    ('personal_os_source_event_id', 'system_source_event_id')))
                and remote.get('summary', '') == desired.summary
                and remote.get('description', '') == desired.description
                and (remote.get('location') or '') == desired.location
                and PersonalEventSyncService._same_time(remote, desired.dtstart, desired.dtend))

    def rows(self, operation, events):
        rows = {event.id: {'kind': 'class', 'data': encode(self.app.classes.event_data(event))}
                for event in events}
        for block in operation.blocks:
            data = self.app.projector._event_data(block, operation)
            rows[block.id] = {'kind': 'preparation', 'data': encode({
                key: getattr(data, key) for key in ('uid', 'system_block_id', 'system_source_event_id',
                    'system_operation_id', 'summary', 'description', 'location', 'dtstart', 'dtend', 'event_type')})}
        return rows

    def capture(self, operation, events, calendar_id):
        """Record only projections reread after successful initial publication."""
        rows = self.rows(operation, events)
        for uid, row in rows.items():
            remote = self.app.adapter.get_event_by_uid(calendar_id, uid, strict=True)
            if not self.matches(remote, row['data']) or not remote.get('etag'):
                raise PlanConflict('Calendar не подтвердил опубликованный план и версии событий.')
            row.update(event_id=remote['id'], etag=remote.get('etag'))
        operation.published_plan = {'version': operation.version, 'calendar_id': calendar_id, 'rows': rows}

    def validate(self, operation, payload=None):
        base = operation.published_plan
        if not base:
            raise PlanConflict('У этого плана нет снимка опубликованной версии. Нужна проверка оператором; миграции нет.')
        calendar = base['calendar_id']
        # Identity is pinned, never rediscovered by a mutable calendar title.
        if calendar not in {item['id'] for item in self.app.adapter.list_visible_calendars()}:
            raise PlanConflict('Опубликованный календарь недоступен.')
        desired = payload['rows'] if payload else base['rows']
        if not set(base['rows']).issubset(desired):
            raise PlanConflict('Предложено удаление. Этот этап требует отдельного разрешения оператором.')
        remote_rows = {}
        for uid, row in desired.items():
            remote = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
            old = base['rows'].get(uid)
            if (old and not old.get('etag')) or (remote and not remote.get('etag')):
                raise PlanConflict('Calendar не предоставил версию события; запись остановлена.')
            attempted = payload and uid in payload.get('attempted', [])
            if old and remote and remote.get('id') != old['event_id']:
                raise PlanConflict('Идентичность события Calendar изменилась.')
            if attempted and self.matches(remote, row['data']):
                pass  # A timeout/crash may follow a successful journaled write.
            elif old:
                if (not self.matches(remote, old['data'])
                        or old.get('etag') and remote.get('etag') != old['etag']):
                    raise PlanConflict('Ручное изменение или удаление в Calendar. Сохранено; нужен разбор конфликта оператором.')
            elif remote is not None:
                raise PlanConflict('Новое событие столкнулось с существующим UID. Нужен разбор конфликта.')
            elif attempted:
                raise PlanConflict('Результат прежней вставки неоднозначен: событие отсутствует. Восстановление остановлено; нужен оператор.')
            remote_rows[uid] = remote
        return remote_rows

    def build(self, operation, settings, now, events, signature, busy):
        remotes = self.validate(operation)
        calendar = operation.published_plan['calendar_id']
        owned = {(calendar, remote['id']) for remote in remotes.values()}
        selected = [event for event in events if event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
                    and now <= event.start_time < settings.profile.planning_horizon_end(now)]
        external = [event for event in busy if (event.calendar_id, event.event_id) not in owned]
        commitments = [*settings.sleep_commitments(now, settings.profile.planning_horizon_end(now)),
                       *settings.routine_commitments(now, settings.profile.planning_horizon_end(now), events),
                       *CalendarAvailabilityService(self.app.adapter).hard_commitments(external)]
        candidate = AdaptivePreparationService(settings.profile).build_draft(
            events, now=now + timedelta(minutes=15), fixed_commitments=commitments)
        if candidate.no_slot_reasons or any(block.manual_conflict for block in candidate.blocks):
            raise PlanConflict('Не все подготовки помещаются. Обновление остановлено.')
        old_blocks = {block.source_event_id: block for block in operation.blocks}
        candidate.blocks = [replace(block, status='confirmed', id=old_blocks[block.source_event_id].id
                                    if block.source_event_id in old_blocks else block.id)
                            for block in candidate.blocks]
        candidate.id, candidate.version = operation.id, operation.version + 1
        candidate.content_hash = signature
        candidate.calendar_id = calendar
        candidate.calendar_ids = dict(operation.calendar_ids)
        candidate.calendar_event_ids = dict(operation.calendar_event_ids)
        rows = self.rows(candidate, selected)
        if not set(operation.published_plan['rows']).issubset(rows):
            raise PlanConflict('Из плана исчезли пары или подготовки. Удалений не будет; нужен разбор оператором.')
        return {'base_hash': digest(operation.published_plan), 'signature': signature,
                'operation': DraftOperationStore._serialize(candidate), 'rows': rows,
                'attempted': [], 'applied': {}}

    def guard(self, operation, payload, now, busy):
        if payload['base_hash'] != digest(operation.published_plan):
            raise PlanConflict('Версия опубликованного плана изменилась. Нужен новый preview.')
        remotes = self.validate(operation, payload)
        calendar = operation.published_plan['calendar_id']
        owned = {(calendar, remote['id']) for remote in remotes.values() if remote}
        for uid, row in payload['rows'].items():
            old = operation.published_plan['rows'].get(uid)
            data = decode(row['data'])
            if not old or old['data'] != row['data']:
                if data.dtstart <= now or old and decode(old['data']).dtstart <= now:
                    raise PlanConflict('Время изменяемого события уже наступило. Нужен разбор оператором.')
                if any((item.calendar_id, item.event_id) not in owned
                       and item.start < data.dtend and item.end > data.dtstart for item in busy):
                    raise PlanConflict('В Calendar появился конфликт. Запись остановлена; нужен новый preview.')
        return remotes

    def preview(self, operation, settings, now, events, signature, busy):
        if operation.pending_plan_update.get('kind') == 'delete':
            return self.app.reply('Удаление ещё не завершено. Открой /review_missing; не очищай журналы.')
        try:
            if not operation.pending_plan_update and operation.content_hash == signature:
                self.validate(operation)
                return self.app.reply('Этот план уже опубликован. Изменений и повторных записей нет.')
            payload = deepcopy(operation.pending_plan_update) or self.build(
                operation, settings, now, events, signature, busy)
            if payload['signature'] != signature:
                raise PlanConflict('Данные изменились после частичной записи. Продолжение остановлено; нужен оператор.')
            self.guard(operation, payload, now, busy)
            lines = [f"Обновление плана v{operation.version} → v{payload['operation']['version']} (пока без записи):"]
            for uid, row in payload['rows'].items():
                old = operation.published_plan['rows'].get(uid)
                if old and row['data'] == old['data']:
                    continue
                target = decode(row['data'])
                after = f'{target.dtstart:%d.%m %H:%M}–{target.dtend:%H:%M}'
                if not old:
                    lines.append(f'+ Добавить: {target.summary}, {after}')
                else:
                    before = decode(old['data'])
                    lines.append(f'↔ Изменить: {before.summary}, {before.dtstart:%d.%m %H:%M}–{before.dtend:%H:%M}'
                                 f' → {target.summary}, {after}')
                    if before.location != target.location:
                        lines.append(f'  Место: {before.location or "—"} → {target.location or "—"}')
                    marker = '\nСтабильный ID личного события:' if row['kind'] == 'class' else '\n\nAI Calendar Operation:'
                    before_text = before.description.split(marker)[0]
                    after_text = target.description.split(marker)[0]
                    if before_text != after_text:
                        lines.append(f'  Описание: {before_text or "—"} → {after_text or "—"}')
            lines.append('Удалений: 0. Ручные изменения не перезаписываются.')
            if operation.pending_plan_update:
                lines.append('Это продолжение частичной записи; уже записанное будет проверено без повторной записи.')
            token = uuid4().hex
            self.app.pending = (token, now + timedelta(minutes=10), 'plan-update', payload, operation.id)
            return self.app.reply('\n'.join(lines), [[
                {'text': 'Подтвердить обновление', 'callback_data': 'pilot:apply:' + token},
                {'text': 'Отмена', 'callback_data': 'pilot:cancel'},
            ]])
        except PlanConflict as error:
            return self.app.reply(str(error))

    def apply(self, pending):
        _, _, _, payload, operation_id = pending
        writing = False
        try:
            operation = self.app.operations.load(operation_id)
            settings, now, events, signature, busy = self.app._inputs()
            if signature != payload['signature']:
                raise PlanConflict('Данные изменились после preview. Нужен новый preview; записи не было.')
            if operation.pending_plan_update and operation.pending_plan_update != payload:
                raise PlanConflict('Сохранённая операция изменилась. Нужен новый preview.')
            self.guard(operation, payload, now, busy)
            operation.pending_plan_update = payload
            operation.projection_pending = True
            self.app.operations.save(operation)  # Journal before any remote mutation.
            writing = True
            candidate = DraftOperationStore._deserialize(payload['operation'])
            calendar = operation.published_plan['calendar_id']
            def checkpoint(target):
                payload['operation'] = DraftOperationStore._serialize(target)
                self.app.operations.save(operation)
            for uid, row in payload['rows'].items():
                remote = self.validate(operation, payload)[uid]
                desired = decode(row['data'])
                if not self.matches(remote, row['data']):
                    if remote and not remote.get('etag'):
                        raise PlanConflict('Calendar не предоставил версию события; обновление остановлено.')
                    if uid not in payload['attempted']:
                        payload['attempted'].append(uid)
                    self.app.operations.save(operation)
                    action = 'update' if remote else 'insert'
                    event_id = remote['id'] if remote else None
                    etag = remote.get('etag') if remote else None
                    if row['kind'] == 'class':
                        self.app.classes._write_verified(calendar, uid, desired, action, event_id, expected_etag=etag, allow_insert_retry=False)
                    else:
                        block = next(block for block in candidate.blocks if block.id == uid)
                        self.app.projector._write_verified(candidate, block, calendar, desired, action,
                                                          event_id, checkpoint, expected_etag=etag, allow_insert_retry=False)
                    remote = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                    if not self.matches(remote, row['data']):
                        raise PlanConflict('Результат записи не подтверждён.')
                candidate.pending_calendar_writes.pop(uid, None)
                payload['applied'][uid] = remote['id']
                if row['kind'] == 'preparation':
                    candidate.calendar_event_ids[uid] = remote['id']
                else:
                    self.app.classes.projection_state.put(uid, pending=None, event_id=remote['id'],
                                                          start=desired.dtstart.isoformat(), end=desired.dtend.isoformat(), override=None)
                checkpoint(candidate)
            # Verify the entire final result before marking the version published.
            final_rows = deepcopy(payload['rows'])
            for uid, row in final_rows.items():
                remote = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                if (not self.matches(remote, row['data']) or not remote.get('etag')
                        or remote['id'] != payload['applied'][uid]):
                    raise PlanConflict('Итоговый план не подтверждён; операция сохранена для проверки.')
                row.update(event_id=remote['id'], etag=remote.get('etag'))
            candidate.status, candidate.projection_pending = 'confirmed', False
            candidate.published_plan = {'version': candidate.version, 'calendar_id': calendar, 'rows': final_rows,
                                        'source_resolutions': operation.published_plan.get('source_resolutions', [])}
            candidate.pending_plan_update = {}
            self.app.operations.save(candidate)
            return self.app.reply(f'План v{candidate.version} обновлён и проверен. Повторный preview не выполняет запись.')
        except Exception as error:
            detail = str(error) if isinstance(error, PlanConflict) else 'Проверка провайдера не завершена.'
            return self.app.reply(('Обновление не завершено; часть изменений могла сохраниться. ' if writing else
                                   'Запись не начиналась. ') + detail + ' Открой /weekly_preview; не очищай журналы.')

    @staticmethod
    def reviewable(event):
        return (event.state in {PersonalEventState.POSSIBLY_CANCELLED, PersonalEventState.CANCELLED}
                and event.metadata.get('change_reason') in {
                    'missing_from_university_schedule', 'explicit_source_cancellation'})

    def deletion_targets(self, operation, events):
        """Only published classes with explicit source evidence and their preparations."""
        base = operation.published_plan.get('rows', {})
        source = {event.uid: event for event in self.app.snapshot._load()['source_events']}
        candidates = [event for event in events if self.reviewable(event)
                      and event.id in base and base[event.id]['kind'] == 'class']
        for event in candidates:
            current = source.get(event.university_event_uid)
            reason = event.metadata.get('change_reason')
            if not ((reason == 'missing_from_university_schedule' and current is None)
                    or (reason == 'explicit_source_cancellation' and current is not None and current.is_cancelled)):
                raise PlanConflict('Источник не подтверждает исчезновение/отмену. Нужен разбор оператором.')
        classes = {event.id for event in candidates}
        return classes, {uid: deepcopy(row) for uid, row in base.items()
                         if uid in classes or row['kind'] == 'preparation'
                         and row['data']['system_source_event_id'] in classes}

    @staticmethod
    def deleted(remote, expected_id):
        # Providers may retain a tombstone. It must still identify the exact
        # approved event; another event with the same UID is never adopted.
        return remote is None or (remote.get('status') == 'cancelled' and remote.get('id') == expected_id)

    def validate_deletion(self, operation, payload, now):
        if not operation.published_plan or payload['base_hash'] != digest(operation.published_plan):
            raise PlanConflict('Версия плана изменилась. Нужен новый /review_missing.')
        base = operation.published_plan
        calendar = base['calendar_id']
        if calendar not in {row['id'] for row in self.app.adapter.list_visible_calendars()}:
            raise PlanConflict('Опубликованный календарь недоступен.')
        for uid, old in base['rows'].items():
            remote = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
            removed = uid in payload['rows']
            if removed:
                # Both lookup routes must agree: never delete a replacement ID.
                by_id = self.app.adapter.get_event_by_id(calendar, old['event_id'], strict=True)
                absent = self.deleted(by_id, old['event_id'])
                if self.deleted(remote, old['event_id']) and absent and uid in payload['attempted']:
                    continue
                if absent or not remote or by_id.get('id') != remote.get('id'):
                    raise PlanConflict('Событие удалено/заменено вручную. Нужен разбор оператором.')
                if decode(old['data']).dtstart <= now:
                    raise PlanConflict('Время удаляемого события уже наступило. Нужен разбор оператором.')
                if (not self.matches(by_id, old['data']) or by_id.get('etag') != old.get('etag')):
                    raise PlanConflict('Событие изменилось вручную. Удаление остановлено.')
            if (not self.matches(remote, old['data']) or remote['id'] != old['event_id']
                    or not old.get('etag') or remote.get('etag') != old['etag']):
                raise PlanConflict('Ручное изменение или удаление в Calendar. Сохранено; нужен разбор оператором.')

    def review_missing(self):
        try:
            settings, now, events, signature, busy = self.app._inputs(allow_source_review=True)
            operations = list(self.app.operations.load_all().values())
            if len(operations) != 1 or not operations[0].published_plan:
                return self.app.reply('Нет проверенной опубликованной версии для разбора.')
            operation = operations[0]
            if operation.pending_plan_update and operation.pending_plan_update.get('kind') != 'delete':
                return self.app.reply('Сначала заверши или разбери начатое обновление плана с оператором.')
            classes, rows = self.deletion_targets(operation, events)
            previous = operation.pending_plan_update
            if previous and (previous['signature'] != signature or set(previous['rows']) != set(rows)):
                raise PlanConflict('Источник или настройки изменились. Начатое удаление остановлено; нужен оператор.')
            if not rows:
                return self.app.reply('Нет исчезнувших или отменённых опубликованных занятий для удаления.')
            payload = deepcopy(operation.pending_plan_update) or {
                'kind': 'delete', 'base_hash': digest(operation.published_plan), 'signature': signature,
                'classes': sorted(classes), 'rows': rows, 'attempted': [], 'deleted': [],
            }
            if payload['signature'] != signature or set(payload['rows']) != set(rows):
                raise PlanConflict('Источник или настройки изменились. Начатое удаление остановлено; нужен оператор.')
            self.validate_deletion(operation, payload, now)
            lines = [f'Разбор исчезнувших/отменённых занятий, план v{operation.version}.',
                     'Отсутствие в источнике само по себе не доказывает отмену. Можно оставить события.',
                     'Только после подтверждения будут удалены из Calendar:']
            for row in payload['rows'].values():
                data = decode(row['data'])
                lines.append(f'− {data.summary}: {data.dtstart:%d.%m %H:%M}–{data.dtend:%H:%M}')
            lines.append('Остальные события останутся на месте. Пересчёт подготовок — отдельным preview.')
            if operation.pending_plan_update:
                lines.append('Продолжение частичного удаления: уже отсутствующие события повторно не удаляются.')
            token = uuid4().hex
            self.app.pending = (token, now + timedelta(minutes=10), 'plan-delete', payload, operation.id)
            return self.app.reply('\n'.join(lines), [[
                {'text': 'Подтвердить удаление перечисленных', 'callback_data': 'pilot:apply:' + token},
                {'text': 'Оставить сейчас', 'callback_data': 'pilot:cancel'},
            ]])
        except PlanConflict as error:
            return self.app.reply(str(error))

    def apply_deletion(self, pending):
        from services.calendar.projection_state import delete_owned_verified
        _, _, _, payload, operation_id = pending
        writing = False
        try:
            operation = self.app.operations.load(operation_id)
            settings, now, events, signature, busy = self.app._inputs(allow_source_review=True)
            classes, rows = self.deletion_targets(operation, events)
            if (signature != payload['signature'] or set(rows) != set(payload['rows'])
                    or sorted(classes) != payload['classes']):
                raise PlanConflict('Источник или настройки изменились после preview. Удаление остановлено.')
            if operation.pending_plan_update and operation.pending_plan_update != payload:
                raise PlanConflict('Сохранённая операция изменилась. Нужен новый /review_missing.')
            self.validate_deletion(operation, payload, now)
            operation.pending_plan_update = payload
            operation.projection_pending = True
            self.app.operations.save(operation)
            writing = True
            calendar = operation.published_plan['calendar_id']
            for uid, row in payload['rows'].items():
                self.validate_deletion(operation, payload, now)
                remote = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                if not self.deleted(remote, row['event_id']):
                    if uid not in payload['attempted']:
                        payload['attempted'].append(uid)
                    self.app.operations.save(operation)
                    delete_owned_verified(self.app.adapter, calendar, row['event_id'],
                        lambda event: self.matches(event, row['data']) and event['id'] == row['event_id'],
                        expected_etag=row['etag'])
                if uid not in payload['deleted']:
                    payload['deleted'].append(uid)
                self.app.operations.save(operation)
            self.validate_deletion(operation, payload, now)
            for uid, row in payload['rows'].items():
                by_id = self.app.adapter.get_event_by_id(calendar, row['event_id'], strict=True)
                if (not self.deleted(self.app.adapter.get_event_by_uid(calendar, uid, strict=True), row['event_id'])
                        or not self.deleted(by_id, row['event_id'])):
                    raise PlanConflict('Удаление не подтверждено; операция сохранена для проверки.')
            # Deletion did not approve a replan of the retained rows. Invalidate
            # the full-plan signature so reappearance cannot look up-to-date.
            operation.version += 1
            operation.content_hash = ''
            operation.blocks = [block for block in operation.blocks if block.id not in payload['rows']]
            for uid in payload['rows']:
                operation.calendar_event_ids.pop(uid, None)
                operation.pending_calendar_writes.pop(uid, None)
            for uid in payload['classes']:
                self.app.classes.projection_state.put(uid, pending=None, event_id=None, override='cancelled')
            operation.published_plan['rows'] = {
                uid: row for uid, row in operation.published_plan['rows'].items() if uid not in payload['rows']}
            operation.published_plan['version'] = operation.version
            operation.published_plan['source_resolutions'] = sorted(
                set(operation.published_plan.get('source_resolutions', [])) | set(payload['classes']))
            operation.pending_plan_update = {}
            operation.projection_pending = False
            self.app.operations.save(operation)
            return self.app.reply(f'Удаление проверено. План v{operation.version}: удалено событий — {len(payload["rows"])}. '
                                  'Остальные сохранены. /weekly_preview предложит отдельный пересчёт без записи.')
        except Exception as error:
            detail = str(error) if isinstance(error, PlanConflict) else 'Проверка провайдера не завершена.'
            return self.app.reply(('Удаление не завершено; часть событий могла быть удалена. ' if writing else
                                   'Запись не начиналась. ') + detail + ' Открой /review_missing; не очищай журналы.')
