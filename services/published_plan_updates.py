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
from services.weekly_plan_service import FixedCommitment


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

    @staticmethod
    def _interval(remote):
        try:
            start = datetime.fromisoformat(remote['start']['dateTime'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(remote['end']['dateTime'].replace('Z', '+00:00'))
            if start.tzinfo and end.tzinfo and start < end:
                return start, end
        except (KeyError, TypeError, ValueError):
            pass
        raise PlanConflict('Время Calendar неоднозначно или событие на весь день; нужен оператор.')

    def _remote(self, calendar, uid, row):
        """A UID lookup alone cannot prove that the saved Google event survived."""
        by_uid = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
        if hasattr(self.app.adapter, 'get_event_by_id'):
            by_id = self.app.adapter.get_event_by_id(calendar, row['event_id'], strict=True)
        else:
            # Some synthetic adapters expose only calendar listing; still
            # inspect the exact saved ID independently of the UID lookup.
            matches = [item for item in self.app.adapter.list_events_in_calendar(calendar)
                       if item.get('id') == row['event_id']]
            if len(matches) > 1:
                raise PlanConflict('Повторяющийся ID Calendar; нужен оператор.')
            by_id = matches[0] if matches else None
        if by_uid and by_uid.get('id') != row['event_id']:
            raise PlanConflict('UID Calendar теперь у другого события; нужен оператор.')
        if by_id and by_id.get('id') != row['event_id']:
            raise PlanConflict('ID Calendar изменился; нужен оператор.')
        expected = decode(row['data'])
        for candidate in (by_uid, by_id):
            if candidate is None:
                continue
            private = candidate.get('extendedProperties', {}).get('private', {})
            if (candidate.get('iCalUID') != uid
                    or private.get('personal_os_block_id') != expected.system_block_id
                    or private.get('personal_os_operation_id') != expected.system_operation_id
                    or private.get('personal_os_source_event_id') != expected.system_source_event_id):
                raise PlanConflict('UID или маркеры принадлежности Calendar изменились; нужен оператор.')
        if bool(by_uid) != bool(by_id):
            # A cancelled tombstone may be returned on one lookup route only.
            survivor = by_uid or by_id
            if survivor.get('status') != 'cancelled':
                raise PlanConflict('UID и ID Calendar расходятся; нужен оператор.')
        if by_uid and by_id and by_uid.get('status') != by_id.get('status'):
            raise PlanConflict('UID и ID Calendar расходятся; нужен оператор.')
        return by_uid, by_id

    def _resolution(self, uid, row, remote, by_id, now, horizon):
        if not row.get('etag'):
            raise PlanConflict('Нет сохранённой версии Calendar; решение остановлено.')
        deleted = (remote is None or remote.get('status') == 'cancelled') and (
            by_id is None or by_id.get('status') == 'cancelled')
        if deleted:
            if remote and remote.get('id') != row['event_id'] or by_id and by_id.get('id') != row['event_id']:
                raise PlanConflict('Удалённое событие заменено; нужен оператор.')
            if any(item and not item.get('etag') for item in (remote, by_id)):
                raise PlanConflict('Calendar не предоставил версию удалённого события.')
            # Confirmation must see the same absence/tombstone on each lookup
            # route. In particular, a tombstone ETag can change without the
            # status or event ID changing.
            def observed(item):
                return ({'id': item['id'], 'status': item['status'], 'etag': item['etag']}
                        if item is not None else None)
            return {'kind': 'deleted', 'observed': {'by_uid': observed(remote), 'by_id': observed(by_id)}}
        if not remote or not by_id or remote.get('status') == 'cancelled' or by_id.get('status') == 'cancelled':
            raise PlanConflict('UID и ID Calendar расходятся; нужен оператор.')
        if not remote.get('etag') or not by_id.get('etag') or remote['etag'] != by_id['etag']:
            raise PlanConflict('Calendar не предоставил согласованную версию события.')
        start, end = self._interval(remote)
        if start <= now or end > horizon:
            raise PlanConflict('Перенос в прошлом или вне горизонта планирования; нужен оператор.')
        data = deepcopy(row['data'])
        data['dtstart'], data['dtend'] = start.isoformat(), end.isoformat()
        if not self.matches(remote, data) or not self.matches(by_id, data):
            raise PlanConflict('Кроме времени изменены данные или маркеры события; нужен оператор.')
        if data == row['data']:
            raise PlanConflict('Изменилась версия без переноса времени; нужен оператор.')
        accepted = deepcopy(row)
        accepted['data'], accepted['etag'] = data, remote['etag']
        return {'kind': 'moved', 'row': accepted}

    def review_calendar(self):
        try:
            settings, now, events, signature, busy = self.app._inputs(allow_source_review=True)
            operations = list(self.app.operations.load_all().values())
            if len(operations) != 1 or not operations[0].published_plan:
                return self.app.reply('Нет проверенной опубликованной версии для разбора Calendar.')
            operation = operations[0]
            if operation.pending_plan_update or operation.projection_pending:
                return self.app.reply('Сначала заверши начатое обновление или удаление плана.')
            base = operation.published_plan
            calendar = base['calendar_id']
            if calendar not in {item['id'] for item in self.app.adapter.list_visible_calendars()}:
                raise PlanConflict('Опубликованный календарь недоступен.')
            changes = {}
            for uid, row in base['rows'].items():
                remote, by_id = self._remote(calendar, uid, row)
                prior = base.get('manual_resolutions', {}).get(uid)
                if prior and prior['kind'] == 'deleted':
                    if not self.deleted(remote, row['event_id']) or not self.deleted(by_id, row['event_id']):
                        raise PlanConflict('Ранее удалённое событие снова появилось; нужен оператор.')
                    continue
                if (self.matches(remote, row['data']) and self.matches(by_id, row['data'])
                        and remote.get('etag') == row.get('etag') == by_id.get('etag')):
                    continue
                changes[uid] = self._resolution(uid, row, remote, by_id, now,
                                                settings.profile.planning_horizon_end(now))
            if not changes:
                return self.app.reply('Новых ручных переносов или удалений в Calendar нет.')
            self._safe_resolutions(base, changes, now, busy)
            payload = {'base_hash': digest(base), 'signature': signature, 'changes': changes,
                       'calendar_id': calendar}
            lines = [f'Ручные изменения Calendar, план v{operation.version} (без записи в Calendar):']
            for uid, decision in changes.items():
                old = decode(base['rows'][uid]['data'])
                kind = 'занятие' if base['rows'][uid]['kind'] == 'class' else 'подготовка'
                before = f'{old.dtstart:%d.%m %H:%M}–{old.dtend:%H:%M}'
                if decision['kind'] == 'moved':
                    new = decode(decision['row']['data'])
                    lines.append(f'↔ Перенесена {kind}: {old.summary}, {before} → '
                                 f'{new.dtstart:%d.%m %H:%M}–{new.dtend:%H:%M}')
                else:
                    lines.append(f'− Удалена {kind}: {old.summary}, {before}')
                if kind == 'подготовка':
                    lines.append('  Для этого занятия новая подготовка автоматически не создастся.')
                elif decision['kind'] == 'deleted':
                    lines.append('  Занятие не восстановится; существующие связанные подготовки останутся без изменений.')
                else:
                    lines.append('  Подготовки будут пересчитаны отдельно с учётом нового времени занятия.')
            lines.append('Принятие сохранит решение локально. Пересчёт остальных событий — отдельный /weekly_preview.')
            token = uuid4().hex
            self.app.pending = (token, now + timedelta(minutes=10), 'calendar-resolution', payload, operation.id)
            return self.app.reply('\n'.join(lines), [[
                {'text': 'Принять ручные изменения', 'callback_data': 'pilot:apply:' + token},
                {'text': 'Отмена', 'callback_data': 'pilot:cancel'},
            ]])
        except PlanConflict as error:
            return self.app.reply(str(error))

    def _safe_resolutions(self, base, changes, now, busy):
        decisions = {**base.get('manual_resolutions', {}), **changes}
        intervals = {}
        for uid, row in base['rows'].items():
            decision = decisions.get(uid, {})
            if decision.get('kind') == 'deleted':
                continue
            data = decode(decision.get('row', row)['data'])
            if uid in changes and decision['kind'] == 'moved':
                if data.dtstart <= now:
                    raise PlanConflict('Перенос уже в прошлом; нужен оператор.')
            intervals[uid] = (data.dtstart, data.dtend)
        for uid, decision in changes.items():
            if decision['kind'] != 'moved':
                continue
            start, end = intervals[uid]
            if any(other != uid and start < finish and end > begin
                   for other, (begin, finish) in intervals.items()):
                raise PlanConflict('Ручной перенос пересекается с опубликованным событием; нужен оператор.')
            owned = {(base['calendar_id'], row['event_id']) for row in base['rows'].values()}
            if any((item.calendar_id, item.event_id) not in owned and start < item.end and end > item.start
                   for item in busy):
                raise PlanConflict('Ручной перенос пересекается с другим событием; нужен оператор.')

    def accept_calendar(self, pending):
        _, _, _, payload, operation_id = pending
        try:
            operation = self.app.operations.load(operation_id)
            if operation.pending_plan_update or operation.projection_pending or digest(operation.published_plan) != payload['base_hash']:
                raise PlanConflict('Версия плана изменилась. Нужен новый /review_calendar.')
            settings, now, events, signature, busy = self.app._inputs(allow_source_review=True)
            if signature != payload['signature'] or operation.published_plan['calendar_id'] != payload['calendar_id']:
                raise PlanConflict('Источник или настройки изменились. Нужен новый /review_calendar.')
            base = operation.published_plan
            calendar = base['calendar_id']
            for uid, row in base['rows'].items():
                remote, by_id = self._remote(calendar, uid, row)
                previous = base.get('manual_resolutions', {}).get(uid)
                if uid in payload['changes']:
                    fresh = self._resolution(uid, row, remote, by_id, now,
                                             settings.profile.planning_horizon_end(now))
                    if fresh != payload['changes'][uid]:
                        raise PlanConflict('Calendar изменился после review. Нужен новый /review_calendar.')
                elif previous and previous['kind'] == 'deleted':
                    if not self.deleted(remote, row['event_id']) or not self.deleted(by_id, row['event_id']):
                        raise PlanConflict('Calendar изменился после review. Нужен новый /review_calendar.')
                elif (not self.matches(remote, row['data']) or not self.matches(by_id, row['data'])
                      or remote.get('etag') != row.get('etag') or by_id.get('etag') != row.get('etag')):
                    raise PlanConflict('Calendar изменился после review. Нужен новый /review_calendar.')
            self._safe_resolutions(base, payload['changes'], now, busy)
            for uid, decision in payload['changes'].items():
                if decision['kind'] == 'moved':
                    base['rows'][uid] = deepcopy(decision['row'])
            base.setdefault('manual_resolutions', {}).update(deepcopy(payload['changes']))
            operation.content_hash = ''  # Separate approval for the remaining replan.
            self.app.operations.save(operation)
            return self.app.reply('Ручные изменения приняты и сохранены. Calendar не записывался. '
                                  'Открой /weekly_preview для отдельного пересчёта остальных событий.')
        except Exception as error:
            detail = str(error) if isinstance(error, PlanConflict) else 'Проверка источника или Calendar не завершена.'
            return self.app.reply(detail + ' Записи в Calendar не было; открой /review_calendar.')

    def validate(self, operation, payload=None):
        base = operation.published_plan
        if not base:
            raise PlanConflict('У этого плана нет снимка опубликованной версии. Нужна проверка оператором; миграции нет.')
        calendar = base['calendar_id']
        # Identity is pinned, never rediscovered by a mutable calendar title.
        if calendar not in {item['id'] for item in self.app.adapter.list_visible_calendars()}:
            raise PlanConflict('Опубликованный календарь недоступен.')
        desired = payload['rows'] if payload else base['rows']
        deleted = {uid for uid, decision in base.get('manual_resolutions', {}).items()
                   if decision['kind'] == 'deleted'}
        if not set(base['rows']).difference(deleted).issubset(desired):
            raise PlanConflict('Предложено удаление. Этот этап требует отдельного разрешения оператором.')
        remote_rows = {}
        for uid, row in {**{uid: base['rows'][uid] for uid in deleted}, **desired}.items():
            old = base['rows'].get(uid)
            if old:
                remote, by_id = self._remote(calendar, uid, old)
            else:
                remote = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                by_id = None
            if uid in deleted:
                if not old.get('etag') or not self.deleted(remote, old['event_id']) or not self.deleted(by_id, old['event_id']):
                    raise PlanConflict('Принятое удаление изменилось в Calendar; нужен новый /review_calendar.')
                remote_rows[uid] = None
                continue
            if (old and not old.get('etag')) or (remote and not remote.get('etag')):
                raise PlanConflict('Calendar не предоставил версию события; запись остановлена.')
            attempted = payload and uid in payload.get('attempted', [])
            if old and remote and remote.get('id') != old['event_id']:
                raise PlanConflict('Идентичность события Calendar изменилась.')
            if old and by_id and remote and (by_id.get('etag') != remote.get('etag')
                                             or by_id.get('iCalUID') != uid):
                raise PlanConflict('UID и ID Calendar расходятся; нужен оператор.')
            if attempted and self.matches(remote, row['data']):
                pass  # A timeout/crash may follow a successful journaled write.
            elif old:
                if (not self.matches(remote, old['data'])
                        or old.get('etag') and remote.get('etag') != old['etag']):
                    raise PlanConflict('Ручное изменение или удаление в Calendar. Сохранено; открой /review_calendar.')
            elif remote is not None:
                raise PlanConflict('Новое событие столкнулось с существующим UID. Нужен разбор конфликта.')
            elif attempted:
                raise PlanConflict('Результат прежней вставки неоднозначен: событие отсутствует. Восстановление остановлено; нужен оператор.')
            remote_rows[uid] = remote
        return remote_rows

    def build(self, operation, settings, now, events, signature, busy):
        remotes = self.validate(operation)
        base = operation.published_plan
        calendar = base['calendar_id']
        decisions = base.get('manual_resolutions', {})
        deleted = {uid for uid, decision in decisions.items() if decision['kind'] == 'deleted'}
        pinned_preps = {uid for uid, row in base['rows'].items() if row['kind'] == 'preparation'
                        and (uid in decisions or row['data']['system_source_event_id'] in deleted)
                        and uid not in deleted}
        effective = []
        for event in events:
            if event.id in deleted:
                continue
            if event.id in decisions and decisions[event.id]['kind'] == 'moved':
                data = decode(base['rows'][event.id]['data'])
                event = replace(event, start_time=data.dtstart, end_time=data.dtend)
            effective.append(event)
        owned = {(calendar, remote['id']) for remote in remotes.values() if remote}
        selected = [event for event in effective if event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
                    and now <= event.start_time < settings.profile.planning_horizon_end(now)]
        external = [event for event in busy if (event.calendar_id, event.event_id) not in owned]
        commitments = [*settings.sleep_commitments(now, settings.profile.planning_horizon_end(now)),
                       *settings.routine_commitments(now, settings.profile.planning_horizon_end(now), effective),
                       *CalendarAvailabilityService(self.app.adapter).hard_commitments(external)]
        for uid in pinned_preps:
            data = decode(base['rows'][uid]['data'])
            commitments.append(FixedCommitment(id='retained:' + uid, title=data.summary,
                                               start=data.dtstart, end=data.dtend))
        suppressed = deleted | {base['rows'][uid]['data']['system_source_event_id']
                                for uid in decisions if base['rows'][uid]['kind'] == 'preparation'}
        candidate = AdaptivePreparationService(settings.profile).build_draft(
            effective, now=now + timedelta(minutes=15), fixed_commitments=commitments,
            completed_source_event_ids=suppressed)
        if candidate.no_slot_reasons or any(block.manual_conflict for block in candidate.blocks):
            raise PlanConflict('Не все подготовки помещаются. Обновление остановлено.')
        old_blocks = {block.source_event_id: block for block in operation.blocks}
        candidate.blocks = [replace(block, status='confirmed', id=old_blocks[block.source_event_id].id
                                    if block.source_event_id in old_blocks else block.id)
                            for block in candidate.blocks]
        for block in operation.blocks:
            if block.id in pinned_preps:
                data = decode(base['rows'][block.id]['data'])
                candidate.blocks.append(replace(block, start=data.dtstart, end=data.dtend,
                                                minutes=int((data.dtend - data.dtstart).total_seconds() / 60)))
        candidate.id, candidate.version = operation.id, operation.version + 1
        candidate.content_hash = signature
        candidate.calendar_id = calendar
        candidate.calendar_ids = dict(operation.calendar_ids)
        candidate.calendar_event_ids = dict(operation.calendar_event_ids)
        rows = self.rows(candidate, selected)
        for uid in pinned_preps | {uid for uid in decisions if decisions[uid]['kind'] == 'moved'}:
            if uid not in deleted:
                rows[uid] = deepcopy(base['rows'][uid])
        if not set(base['rows']).difference(deleted).issubset(rows):
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
            if uid in operation.published_plan.get('manual_resolutions', {}):
                if row != old:
                    raise PlanConflict('Принятое ручное решение изменилось в preview; нужен оператор.')
                continue
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
                if uid in operation.published_plan.get('manual_resolutions', {}):
                    continue
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
                if uid in operation.published_plan.get('manual_resolutions', {}):
                    payload['applied'][uid] = row['event_id']
                    continue
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
            for uid, decision in operation.published_plan.get('manual_resolutions', {}).items():
                if decision['kind'] == 'deleted':
                    final_rows[uid] = deepcopy(operation.published_plan['rows'][uid])
            for uid, row in final_rows.items():
                decision = operation.published_plan.get('manual_resolutions', {}).get(uid)
                if decision:
                    if decision['kind'] == 'deleted':
                        remote, by_id = self._remote(calendar, uid, row)
                        if not self.deleted(remote, row['event_id']) or not self.deleted(by_id, row['event_id']):
                            raise PlanConflict('Принятое удаление больше не подтверждается.')
                    else:
                        remote = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                        if (not self.matches(remote, row['data']) or remote.get('etag') != row['etag']
                                or remote.get('id') != row['event_id']):
                            raise PlanConflict('Принятый перенос изменился в Calendar.')
                    continue
                remote = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                if (not self.matches(remote, row['data']) or not remote.get('etag')
                        or remote['id'] != payload['applied'][uid]):
                    raise PlanConflict('Итоговый план не подтверждён; операция сохранена для проверки.')
                row.update(event_id=remote['id'], etag=remote.get('etag'))
            candidate.status, candidate.projection_pending = 'confirmed', False
            candidate.published_plan = {'version': candidate.version, 'calendar_id': calendar, 'rows': final_rows,
                                        'source_resolutions': operation.published_plan.get('source_resolutions', []),
                                        'manual_resolutions': deepcopy(operation.published_plan.get('manual_resolutions', {}))}
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
                      and event.id in base and base[event.id]['kind'] == 'class'
                      and operation.published_plan.get('manual_resolutions', {}).get(event.id, {}).get('kind') != 'deleted']
        for event in candidates:
            current = source.get(event.university_event_uid)
            reason = event.metadata.get('change_reason')
            if not ((reason == 'missing_from_university_schedule' and current is None)
                    or (reason == 'explicit_source_cancellation' and current is not None and current.is_cancelled)):
                raise PlanConflict('Источник не подтверждает исчезновение/отмену. Нужен разбор оператором.')
        classes = {event.id for event in candidates}
        return classes, {uid: deepcopy(row) for uid, row in base.items()
                         if uid in classes or row['kind'] == 'preparation'
                         and row['data']['system_source_event_id'] in classes
                         and operation.published_plan.get('manual_resolutions', {}).get(uid, {}).get('kind') != 'deleted'}

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
            if base.get('manual_resolutions', {}).get(uid, {}).get('kind') == 'deleted':
                _, by_id = self._remote(calendar, uid, old)
                if not self.deleted(remote, old['event_id']) or not self.deleted(by_id, old['event_id']):
                    raise PlanConflict('Принятое удаление изменилось в Calendar; нужен /review_calendar.')
                continue
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
            # An already accepted manual deletion has no remote write in this
            # source review, but its linked tombstone must leave the baseline
            # together with the class. Otherwise dropping only its decision
            # makes the next preview expect the absent preparation again.
            removed = set(payload['rows']) | {
                uid for uid, row in operation.published_plan['rows'].items()
                if row['kind'] == 'preparation'
                and row['data']['system_source_event_id'] in payload['classes']
                and operation.published_plan.get('manual_resolutions', {}).get(uid, {}).get('kind') == 'deleted'
            }
            operation.blocks = [block for block in operation.blocks
                                if block.id not in removed and block.source_event_id not in payload['classes']]
            for uid in removed:
                operation.calendar_event_ids.pop(uid, None)
                operation.pending_calendar_writes.pop(uid, None)
            for uid in payload['classes']:
                self.app.classes.projection_state.put(uid, pending=None, event_id=None, override='cancelled')
            operation.published_plan['rows'] = {
                uid: row for uid, row in operation.published_plan['rows'].items() if uid not in removed}
            operation.published_plan['manual_resolutions'] = {
                uid: decision for uid, decision in operation.published_plan.get('manual_resolutions', {}).items()
                if uid in operation.published_plan['rows'] and
                operation.published_plan['rows'][uid]['data']['system_source_event_id'] not in payload['classes']}
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
