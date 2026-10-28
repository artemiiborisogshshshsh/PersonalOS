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
from zoneinfo import ZoneInfo

from models import PersonalEventState
from services.adaptive_preparation_service import AdaptivePreparationService
from services.calendar_availability_service import CalendarAvailabilityService
from services.draft_operation_store import DraftOperationStore
from services.weekly_plan_service import FixedCommitment
from services.user_facing_errors import provider_hint


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def encode(data):
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in data.items()}


def decode(data):
    return SimpleNamespace(**{key: datetime.fromisoformat(value) if key in {'dtstart', 'dtend'} else value
                              for key, value in data.items()})


def format_interval(start, end, timezone):
    """Render stored instants in the user's profile timezone without changing them."""
    zone = ZoneInfo(timezone)
    local_start = (start.replace(tzinfo=zone) if start.tzinfo is None else start).astimezone(zone)
    local_end = (end.replace(tzinfo=zone) if end.tzinfo is None else end).astimezone(zone)
    end_format = '%d.%m %H:%M' if local_end.date() != local_start.date() else '%H:%M'
    return f'{local_start:%d.%m %H:%M}–{local_end:{end_format}}'


class PlanConflict(ValueError):
    pass


class PublishedPlanUpdates:
    def __init__(self, app):
        self.app = app

    def _require_access(self, pending=None, *, background=False):
        access = getattr(self.app, 'access', None)
        if access is None:
            return
        if not access.allowed():
            raise PlanConflict(access.DENIED)
        if access._state().get('resume_required') and (
                background and not getattr(self.app, '_access_manual_learning_resume', False)
                or pending is not None and pending[0] != access.fresh_token):
            raise PlanConflict('После перерыва нужен новый /weekly_preview и подтверждение.')

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
        pairs = (self.app.adapter.get_event_pairs(calendar_id, dict.fromkeys(rows))
                 if callable(getattr(self.app.adapter, 'get_event_pairs', None)) else None)
        for uid, row in rows.items():
            remote = pairs[uid][0] if pairs is not None else self.app.adapter.get_event_by_uid(calendar_id, uid, strict=True)
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

    def _remote(self, calendar, uid, row, *, pair=None):
        """A UID lookup alone cannot prove that the saved Google event survived."""
        if pair is not None:
            by_uid, by_id = pair
        else:
            by_uid = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
            if hasattr(self.app.adapter, 'get_event_by_id'):
                by_id = self.app.adapter.get_event_by_id(calendar, row['event_id'], strict=True)
            else:
                # Synthetic adapters without ID reads still inspect the saved ID.
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
            # Google tombstones may retain only ID and cancelled status.
            # Exact saved ID plus the deletion journal is the evidence; live
            # events must still prove every ownership marker below.
            if candidate.get('status') == 'cancelled':
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
                before = format_interval(old.dtstart, old.dtend, settings.profile.timezone)
                if decision['kind'] == 'moved':
                    new = decode(decision['row']['data'])
                    lines.append(f'↔ Перенесена {kind}: {old.summary}, {before} → '
                                 f'{format_interval(new.dtstart, new.dtend, settings.profile.timezone)}')
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
        self._require_access(pending)
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
            source_events = {event.id: event for event in self.app.snapshot.personal_events()}
            for uid, decision in payload['changes'].items():
                if base['rows'][uid]['kind'] == 'class':
                    source = source_events[uid]
                    decision['source_identity'] = {
                        'uid': source.university_event_uid,
                        'start': source.start_time.isoformat(), 'end': source.end_time.isoformat(),
                    }
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
        rows = {**{uid: base['rows'][uid] for uid in deleted}, **desired}
        pairs = None
        if callable(getattr(self.app.adapter, 'get_event_pairs', None)):
            identities = {}
            for uid in rows:
                decision = base.get('manual_resolutions', {}).get(uid, {})
                old = decision.get('row') if decision.get('kind') == 'moved' else None
                old = old or base['rows'].get(uid)
                identities[uid] = old['event_id'] if old else None
            pairs = self.app.adapter.get_event_pairs(calendar, identities)
        for uid, row in rows.items():
            old = base['rows'].get(uid)
            decision = base.get('manual_resolutions', {}).get(uid)
            if decision and decision.get('kind') == 'moved' and decision.get('row'):
                old = decision['row']
            if old:
                remote, by_id = self._remote(calendar, uid, old, pair=pairs[uid] if pairs is not None else None)
            else:
                remote = pairs[uid][0] if pairs is not None else self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
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

    @staticmethod
    def phase(row, now):
        data = decode(row['data'])
        return 'past' if data.dtend <= now else ('ongoing' if data.dtstart <= now else 'future')

    @classmethod
    def frozen_rows(cls, base, now):
        started = {uid for uid, row in base.get('rows', {}).items() if cls.phase(row, now) != 'future'}
        classes = {uid for uid in started if base['rows'][uid]['kind'] == 'class'}
        return started | {uid for uid, row in base.get('rows', {}).items()
                          if row['kind'] == 'preparation' and row['data']['system_source_event_id'] in classes
                          and not uid.startswith('learning-prep:')}

    @staticmethod
    def effective_events(base, events, *, published_times=False):
        """Apply protected Calendar decisions before deriving any busy time."""
        decisions = base.get('manual_resolutions', {})
        effective = []
        for event in events:
            decision = decisions.get(event.id, {})
            if decision.get('kind') == 'deleted':
                continue
            row = decision.get('row') if decision.get('kind') == 'moved' else None
            if row is None and published_times:
                row = base.get('rows', {}).get(event.id)
            if row and row['kind'] == 'class':
                data = decode(row['data'])
                event = replace(event, start_time=data.dtstart, end_time=data.dtend)
            effective.append(event)
        return effective

    def learning_lessons(self, base, events):
        """Use effective lesson times for default deadlines, including manual moves."""
        if not hasattr(self.app, 'learning'):
            return []
        lessons = deepcopy(self.app.learning.store.lessons())
        effective = {event.id: event for event in events}
        decisions = base.get('manual_resolutions', {})
        for lesson in lessons:
            key = lesson.get('source_personal_id') or lesson['id']
            if decisions.get(key, {}).get('kind') == 'deleted':
                lesson['active'] = False
            if key in effective:
                event = effective[key]
                lesson.update(start=event.start_time.isoformat(), end=event.end_time.isoformat())
        for lesson in lessons:
            if lesson['due_explicit']:
                continue
            following = [row for row in lessons if row['active']
                         and row['course'].casefold() == lesson['course'].casefold()
                         and row['event_type'] == lesson['event_type']
                         and datetime.fromisoformat(row['start']) > datetime.fromisoformat(lesson['start'])]
            lesson['due_at'] = min(following, key=lambda row: datetime.fromisoformat(row['start']))['start'] if following else None
        return lessons

    @staticmethod
    def learning_sources(lessons):
        managed = {row.get('source_personal_id') or row['id'] for row in lessons
                   if row['homework_state'] in {'assigned', 'none'} or row['completed']}
        for lesson in lessons:
            if lesson['homework_state'] == 'assigned' and lesson.get('due_at'):
                managed.update(row.get('source_personal_id') or row['id'] for row in lessons
                               if row['course'].casefold() == lesson['course'].casefold()
                               and row['event_type'] == lesson['event_type']
                               and datetime.fromisoformat(row['start']) == datetime.fromisoformat(lesson['due_at']))
        return managed

    @staticmethod
    def check_preparation_conflicts(rows, now, deleted=()):
        active = {uid: (row, decode(row['data'])) for uid, row in rows.items() if uid not in deleted}
        for uid, (row, data) in active.items():
            if row['kind'] != 'preparation' or data.dtend <= now:
                continue
            for other, (_, interval) in active.items():
                if other != uid and data.dtstart < interval.dtend and data.dtend > interval.dtstart:
                    raise PlanConflict('Подготовка пересекается с занятием или другим блоком. '
                                       'Запись остановлена; нужен пересчёт свободного времени.')

    def build(self, operation, settings, now, events, signature, busy):
        from services.learning_lifecycle import plan_learning_preparations
        remotes = self.validate(operation)
        base = operation.published_plan
        calendar = base['calendar_id']
        frozen = self.frozen_rows(base, now)
        recovered_history = {uid for uid, item in base.get('recovery_history', {}).items()
                             if item.get('row', {}).get('kind') == 'class'}
        historical_classes = ({uid for uid in frozen if base['rows'][uid]['kind'] == 'class'}
                              | recovered_history)
        decisions = base.get('manual_resolutions', {})
        deleted = {uid for uid, decision in decisions.items() if decision['kind'] == 'deleted'}
        effective = [event for event in self.effective_events(base, events)
                     if event.id not in historical_classes]
        lessons = self.learning_lessons(base, effective)
        by_personal = {row.get('source_personal_id') or row['id']: row for row in lessons}
        protected_sources = {base['rows'][uid]['data']['system_source_event_id']
                             for uid in decisions if base['rows'][uid]['kind'] == 'preparation'}
        campus = AdaptivePreparationService(settings.profile)._university_commitments(
            effective, now, settings.profile.planning_horizon_end(now))
        owned = {(calendar, remote['id']) for remote in remotes.values() if remote}
        external = [event for event in busy if (event.calendar_id, event.event_id) not in owned]
        # Future homework belongs to its deadline, even when its originating
        # class has finished. Retain it only while the new source remains safe.
        replan_sources = set()
        for block in operation.blocks:
            if (not block.id.startswith('learning-prep:') or block.id in frozen | set(decisions)
                    or block.source_event_id in protected_sources or block.end <= now):
                continue
            lesson = by_personal.get(block.source_event_id)
            deadline = datetime.fromisoformat(lesson['due_at']) if lesson and lesson.get('due_at') else None
            if (deadline is None or block.end > deadline
                    or any(block.start < item.end and block.end > item.start for item in [*campus, *external])):
                replan_sources.add(block.source_event_id)
        pinned_preps = {uid for uid, row in base['rows'].items() if row['kind'] == 'preparation'
                        and (uid in frozen or uid in decisions
                             or uid.startswith('learning-prep:') and row['data']['system_source_event_id'] not in replan_sources
                             or row['data']['system_source_event_id'] in deleted | protected_sources)
                        and uid not in deleted}
        owned = {(calendar, remote['id']) for remote in remotes.values() if remote}
        selected = [event for event in effective if event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
                    and now <= event.start_time < settings.profile.planning_horizon_end(now)]
        external = [event for event in busy if (event.calendar_id, event.event_id) not in owned]
        commitments = [*settings.sleep_commitments(now, settings.profile.planning_horizon_end(now)),
                       *settings.routine_commitments(now, settings.profile.planning_horizon_end(now), effective),
                       *CalendarAvailabilityService(self.app.adapter).hard_commitments(external)]
        for uid in (pinned_preps | frozen) - deleted:
            data = decode(base['rows'][uid]['data'])
            if data.dtend > now:
                commitments.append(FixedCommitment(id='retained:' + uid, title=data.summary,
                                                   start=data.dtstart, end=data.dtend))
        suppressed = deleted | historical_classes | self.learning_sources(lessons) | {
            item['row']['data']['system_source_event_id']
            for item in base.get('recovery_history', {}).values()
            if item.get('row', {}).get('kind') == 'class'
        } | {base['rows'][uid]['data']['system_source_event_id']
             for uid in set(decisions) | pinned_preps if base['rows'][uid]['kind'] == 'preparation'}
        candidate = AdaptivePreparationService(settings.profile).build_draft(
            [event for event in effective if event.start_time < settings.profile.planning_horizon_end(now)],
            now=now + timedelta(minutes=15), fixed_commitments=commitments,
            completed_source_event_ids=suppressed)
        if candidate.no_slot_reasons or any(block.manual_conflict for block in candidate.blocks):
            raise PlanConflict('Не все подготовки помещаются. Обновление остановлено.')
        old_blocks = {block.source_event_id: block for block in operation.blocks
                      if not block.id.startswith('learning-prep:')}
        candidate.blocks = [replace(block, status='confirmed', id=old_blocks[block.source_event_id].id
                                    if block.source_event_id in old_blocks else block.id)
                            for block in candidate.blocks]
        if replan_sources:
            moving = SimpleNamespace(lessons=lambda: [row for row in lessons
                if (row.get('source_personal_id') or row['id']) in replan_sources])
            fixed = [*commitments, *(FixedCommitment(id=block.id, title=block.title,
                                                   start=block.start, end=block.end) for block in candidate.blocks)]
            try:
                planned = plan_learning_preparations(moving, settings, now + timedelta(minutes=15), effective, fixed)
            except ValueError as error:
                raise PlanConflict(str(error)) from error
            for source in replan_sources:
                previous = sorted([block for block in operation.blocks if block.source_event_id == source
                                   and block.id not in pinned_preps], key=lambda block: block.start)
                targets = sorted([block for block in planned if block.source_event_id == source], key=lambda block: block.start)
                if len(targets) < len(previous):
                    raise PlanConflict('Нельзя потерять сохранённые блоки ДЗ при изменении источника; нужен пересчёт материалов.')
                candidate.blocks.extend(replace(block, id=previous[index].id if index < len(previous) else block.id)
                                        for index, block in enumerate(targets))
        candidate.blocks = self.app.learning.enrich_blocks(candidate.blocks) if hasattr(self.app, 'learning') else candidate.blocks
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
        for uid in frozen | pinned_preps | {uid for uid in decisions if decisions[uid]['kind'] == 'moved'}:
            if uid not in deleted:
                rows[uid] = deepcopy(decisions.get(uid, {}).get('row') or base['rows'][uid])
        if not set(base['rows']).difference(deleted).issubset(rows):
            raise PlanConflict('Из плана исчезли пары или подготовки. Удалений не будет; нужен разбор оператором.')
        self.check_preparation_conflicts(rows, now, deleted)
        return {'base_hash': digest(operation.published_plan), 'signature': signature,
                'operation': DraftOperationStore._serialize(candidate), 'rows': rows,
                'attempted': [], 'applied': {}}

    def guard(self, operation, payload, now, busy):
        if payload['base_hash'] != digest(operation.published_plan):
            raise PlanConflict('Версия опубликованного плана изменилась. Нужен новый preview.')
        remotes = self.validate(operation, payload)
        self.check_preparation_conflicts(payload['rows'], now, {uid for uid, decision in
            operation.published_plan.get('manual_resolutions', {}).items() if decision['kind'] == 'deleted'})
        calendar = operation.published_plan['calendar_id']
        owned = {(calendar, remote['id']) for remote in remotes.values() if remote}
        for uid, row in payload['rows'].items():
            old = operation.published_plan['rows'].get(uid)
            if uid in operation.published_plan.get('manual_resolutions', {}):
                decision = operation.published_plan['manual_resolutions'][uid]
                expected = decision.get('row', old)
                if row != expected:
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
            phases = [self.phase(row, now) for row in operation.published_plan['rows'].values()]
            lines = [f"Обновление плана v{operation.version} → v{payload['operation']['version']} (пока без записи):",
                     f"Сохранены без изменений: завершённые — {phases.count('past')}, текущие — {phases.count('ongoing')}."]
            for uid, row in payload['rows'].items():
                old = operation.published_plan['rows'].get(uid)
                if uid in operation.published_plan.get('manual_resolutions', {}):
                    continue
                if old and row['data'] == old['data']:
                    continue
                target = decode(row['data'])
                after = format_interval(target.dtstart, target.dtend, settings.profile.timezone)
                if not old:
                    lines.append(f'+ Добавить: {target.summary}, {after}')
                else:
                    before = decode(old['data'])
                    marker = '\nСтабильный ID личного события:' if row['kind'] == 'class' else '\n\nAI Calendar Operation:'
                    before_text = before.description.split(marker)[0]
                    after_text = target.description.split(marker)[0]
                    before_fields = {key: value for key, value in old['data'].items()
                                     if key != 'description'}
                    after_fields = {key: value for key, value in row['data'].items()
                                    if key != 'description'}
                    metadata_only = before_fields == after_fields and before_text == after_text
                    if metadata_only:
                        lines.append(f'Обновить служебную информацию: {target.summary}, {after} '
                                     '(время без изменений).')
                    else:
                        lines.append(f'↔ Изменить: {before.summary}, '
                                     f'{format_interval(before.dtstart, before.dtend, settings.profile.timezone)}'
                                     f' → {target.summary}, {after}')
                        if before.location != target.location:
                            lines.append(f'  Место: {before.location or "—"} → {target.location or "—"}')
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
        self._require_access(pending)
        _, _, _, payload, operation_id = pending
        writing = False
        try:
            operation = self.app.operations.load(operation_id)
            settings, now, events, signature, busy = self.app._inputs()
            if signature != payload['signature']:
                raise PlanConflict('Данные изменились после preview. Нужен новый preview; записи не было.')
            if operation.pending_plan_update and operation.pending_plan_update != payload:
                raise PlanConflict('Сохранённая операция изменилась. Нужен новый preview.')
            remotes = self.guard(operation, payload, now, busy)
            operation.pending_plan_update = payload
            operation.projection_pending = True
            self.app.operations.save(operation)  # Journal before any remote mutation.
            writing = True
            candidate = DraftOperationStore._deserialize(payload['operation'])
            calendar = operation.published_plan['calendar_id']
            horizon = settings.profile.planning_horizon_end(now)
            def checkpoint(target):
                payload['operation'] = DraftOperationStore._serialize(target)
                self.app.operations.save(operation)
            for uid, row in payload['rows'].items():
                remote = remotes[uid]
                if uid in operation.published_plan.get('manual_resolutions', {}):
                    payload['applied'][uid] = row['event_id']
                    continue
                desired = decode(row['data'])
                if not self.matches(remote, row['data']):
                    # No-op rows need only the complete preflight/final checks.
                    # Before each actual mutation, however, refresh every row
                    # (including manual tombstones) and use the fresh version
                    # for this row's conditional write.
                    fresh_now = self.app.now()
                    fresh_busy = CalendarAvailabilityService(self.app.adapter, excluded_calendar_names=()).load(
                        fresh_now, settings.profile.planning_horizon_end(fresh_now))
                    remotes = self.guard(operation, payload, fresh_now, fresh_busy)
                    remote = remotes[uid]
                    if not self.matches(remote, row['data']):
                        if remote and not remote.get('etag'):
                            raise PlanConflict('Calendar не предоставил версию события; обновление остановлено.')
                        if uid not in payload['attempted']:
                            payload['attempted'].append(uid)
                        self.app.operations.save(operation)
                        action = 'update' if remote else 'insert'
                        event_id = remote['id'] if remote else None
                        etag = remote.get('etag') if remote else None
                        old = operation.published_plan['rows'].get(uid)
                        starts = [desired.dtstart] + ([decode(old['data']).dtstart] if old else [])
                        before_write = lambda: self.app._check_write_time(starts, settings.profile, horizon)
                        if row['kind'] == 'class':
                            self.app.classes._write_verified(calendar, uid, desired, action, event_id, expected_etag=etag, allow_insert_retry=False, before_write=before_write)
                        else:
                            block = next(block for block in candidate.blocks if block.id == uid)
                            self.app.projector._write_verified(candidate, block, calendar, desired, action,
                                                              event_id, checkpoint, expected_etag=etag, allow_insert_retry=False, before_write=before_write)
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
            final_pairs = None
            if callable(getattr(self.app.adapter, 'get_event_pairs', None)):
                final_pairs = self.app.adapter.get_event_pairs(calendar, {
                    uid: payload['applied'].get(uid) or row.get('event_id')
                    for uid, row in final_rows.items()
                })
            for uid, row in final_rows.items():
                decision = operation.published_plan.get('manual_resolutions', {}).get(uid)
                if decision:
                    if decision['kind'] == 'deleted':
                        remote, by_id = self._remote(calendar, uid, row, pair=final_pairs[uid] if final_pairs is not None else None)
                        if not self.deleted(remote, row['event_id']) or not self.deleted(by_id, row['event_id']):
                            raise PlanConflict('Принятое удаление больше не подтверждается.')
                    else:
                        remote = final_pairs[uid][0] if final_pairs is not None else self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                        if (not self.matches(remote, row['data']) or remote.get('etag') != row['etag']
                                or remote.get('id') != row['event_id']):
                            raise PlanConflict('Принятый перенос изменился в Calendar.')
                    continue
                remote = final_pairs[uid][0] if final_pairs is not None else self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                if (not self.matches(remote, row['data']) or not remote.get('etag')
                        or remote['id'] != payload['applied'][uid]):
                    raise PlanConflict('Итоговый план не подтверждён; операция сохранена для проверки.')
                row.update(event_id=remote['id'], etag=remote.get('etag'))
            candidate.status, candidate.projection_pending = 'confirmed', False
            candidate.published_plan = {'version': candidate.version, 'calendar_id': calendar, 'rows': final_rows,
                                        'source_resolutions': operation.published_plan.get('source_resolutions', []),
                                        'manual_resolutions': deepcopy(operation.published_plan.get('manual_resolutions', {})),
                                        'recovery_history': deepcopy(operation.published_plan.get('recovery_history', {}))}
            candidate.pending_plan_update = {}
            self.app.operations.save(candidate)
            return self.app.reply(f'План v{candidate.version} обновлён и проверен. Повторный preview не выполняет запись.')
        except Exception as error:
            detail = str(error) if isinstance(error, PlanConflict) else (provider_hint(error) or 'Проверка провайдера не завершена.')
            return self.app.reply(('Обновление не завершено; часть изменений могла сохраниться. ' if writing else
                                   'Запись не начиналась. ') + detail + ' Открой /weekly_preview; не очищай журналы.')

    @staticmethod
    def reviewable(event):
        return (event.state in {PersonalEventState.POSSIBLY_CANCELLED, PersonalEventState.CANCELLED}
                and event.metadata.get('change_reason') in {
                    'missing_from_university_schedule', 'explicit_source_cancellation'})

    def deletion_targets(self, operation, events, *, explicit_only=False, now=None):
        """Only published classes with explicit source evidence and their preparations."""
        base = operation.published_plan.get('rows', {})
        source = {event.uid: event for event in self.app.snapshot._load()['source_events']}
        candidates = [event for event in events if self.reviewable(event)
                      and event.id in base and base[event.id]['kind'] == 'class'
                      and operation.published_plan.get('manual_resolutions', {}).get(event.id, {}).get('kind') != 'deleted']
        decisions = operation.published_plan.get('manual_resolutions', {})
        if explicit_only:
            candidates = [event for event in candidates
                          if event.metadata.get('change_reason') == 'explicit_source_cancellation'
                          and event.id not in decisions
                          and self.phase(base[event.id], now) == 'future']
        for event in candidates:
            current = source.get(event.university_event_uid)
            reason = event.metadata.get('change_reason')
            if not ((reason == 'missing_from_university_schedule' and current is None)
                    or (reason == 'explicit_source_cancellation' and current is not None and current.is_cancelled)):
                raise PlanConflict('Источник не подтверждает исчезновение/отмену. Нужен разбор оператором.')
        classes = {event.id for event in candidates}
        return classes, {uid: deepcopy(row) for uid, row in base.items()
                         if (uid in classes or row['kind'] == 'preparation'
                             and row['data']['system_source_event_id'] in classes
                             and decisions.get(uid, {}).get('kind') != 'deleted')
                         and (not explicit_only or uid not in decisions and self.phase(row, now) == 'future')}

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
                current_uids = {event.uid for event in self.app.snapshot._load()['source_events']}
                previously_published = {
                    uid for operation in operations for uid in {
                        *operation.published_plan.get('rows', {}),
                        *operation.published_plan.get('source_resolutions', []),
                        *operation.published_plan.get('manual_resolutions', {}),
                        *operation.published_plan.get('recovery_history', {}),
                    }
                }
                local_missing = [event for event in events
                                 if event.state in {PersonalEventState.POSSIBLY_CANCELLED,
                                                    PersonalEventState.CANCELLED}
                                 and event.metadata.get('change_reason') == 'missing_from_university_schedule'
                                 and event.university_event_uid not in current_uids
                                 and event.id not in previously_published]
                if local_missing:
                    return self.app.reply('Нет опубликованной версии Calendar. Сначала подтверди локальное решение '
                                          'через /review_replacements, затем открой /weekly_preview.')
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
                lines.append(f'− {data.summary}: '
                             f'{format_interval(data.dtstart, data.dtend, settings.profile.timezone)}')
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
        self._require_access(pending)
        from services.calendar.projection_state import delete_owned_verified
        _, _, _, payload, operation_id = pending
        writing = False
        try:
            operation = self.app.operations.load(operation_id)
            settings, now, events, signature, busy = self.app._inputs(allow_source_review=True)
            classes, rows = self.deletion_targets(operation, events,
                explicit_only=payload.get('automatic_explicit', False), now=now)
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
                self.validate_deletion(operation, payload, self.app.now())
                remote = self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                if not self.deleted(remote, row['event_id']):
                    if uid not in payload['attempted']:
                        payload['attempted'].append(uid)
                    self.app.operations.save(operation)
                    delete_owned_verified(self.app.adapter, calendar, row['event_id'],
                        lambda event: self.matches(event, row['data']) and event['id'] == row['event_id'],
                        expected_etag=row['etag'], before_write=lambda: self.app._check_write_time(
                            [decode(row['data']).dtstart], settings.profile, settings.profile.planning_horizon_end(now)))
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
                                if block.id not in removed and (payload.get('automatic_explicit')
                                                              or block.source_event_id not in payload['classes'])]
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
                (payload.get('automatic_explicit') or
                 operation.published_plan['rows'][uid]['data']['system_source_event_id'] not in payload['classes'])}
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

    def sync_source_cancellations(self):
        """Automatically apply explicit source cancellation to future owned rows.

        Ambiguous disappearance, started history and accepted manual decisions
        remain outside this authorization. The existing conditional deletion
        journal supplies preflight, ownership checks and restart recovery.
        """
        self._require_access(background=True)
        operations = list(self.app.operations.load_all().values())
        if len(operations) != 1 or not operations[0].published_plan:
            return ''
        operation = operations[0]
        previous = operation.pending_plan_update
        if previous and not previous.get('automatic_explicit'):
            return ''
        settings, now, events, signature, busy = self.app._inputs(allow_source_review=True)
        classes, rows = self.deletion_targets(operation, events, explicit_only=True, now=now)
        if not rows and not previous:
            return ''
        if not self.app._connected():
            raise PlanConflict('Подключи Google Calendar через /connect_google.')
        payload = deepcopy(previous) or {
            'kind': 'delete', 'automatic_explicit': True,
            'base_hash': digest(operation.published_plan), 'signature': signature,
            'classes': sorted(classes), 'rows': rows, 'attempted': [], 'deleted': [],
        }
        result = self.apply_deletion((None, None, 'plan-delete', payload, operation.id))
        if not result['text'].startswith('Удаление проверено.'):
            raise PlanConflict(result['text'])
        return f"Источник подтвердил отмену: удалены будущие занятия и подготовки — {len(rows)}."

    def sync_learning(self, *, preview_only=False):
        """Project saved learning changes through an immutable, restartable journal.

        Only descriptions of owned classes and future owned preparation blocks
        are changed. Every mutation rechecks the complete baseline and ETags.
        """
        self._require_access(background=not preview_only)
        from services.calendar.projection_state import delete_owned_verified
        from services.learning_lifecycle import plan_learning_preparations
        from services.telegram_learning_flow import learning_description
        operations = list(self.app.operations.load_all().values())
        if len(operations) != 1 or not operations[0].published_plan:
            return 'Материалы сохранены; Google обновится после первой публикации плана.'
        operation = operations[0]
        if operation.pending_plan_update and operation.pending_plan_update.get('kind') != 'learning':
            raise PlanConflict('Сначала нужно завершить предыдущее обновление Calendar.')
        if not self.app._connected():
            raise PlanConflict('Подключи Google Calendar через /connect_google.')
        settings = self.app.profile.load()
        now = self.app.now().astimezone(ZoneInfo(settings.profile.timezone))
        base = operation.published_plan
        events = self.effective_events(base, self.app.snapshot.personal_events(), published_times=True)
        calendar = base['calendar_id']
        payload = deepcopy(operation.pending_plan_update)
        if preview_only and not payload:
            raise PlanConflict('Сохранённого обновления материалов для проверки нет.')
        if not payload:
            remotes = self.validate(operation)
            lessons = self.learning_lessons(base, events)
            by_personal = {row.get('source_personal_id', row['id']): row for row in lessons}
            # A new assignment for the following practical/lecture replaces the
            # generic preparation for that same deadline, avoiding double work.
            managed = self.learning_sources(lessons)
            decisions = base.get('manual_resolutions', {})
            protected_sources = {base['rows'][uid]['data']['system_source_event_id']
                                 for uid in decisions if base['rows'][uid]['kind'] == 'preparation'}
            owned = {(calendar, remote['id']) for remote in remotes.values() if remote}
            busy = CalendarAvailabilityService(self.app.adapter, excluded_calendar_names=()).load(
                now, settings.profile.planning_horizon_end(now))
            external = [item for item in busy if (item.calendar_id, item.event_id) not in owned]
            stability_busy = [*external, *settings.sleep_commitments(now, settings.profile.planning_horizon_end(now)),
                              *settings.routine_commitments(now, settings.profile.planning_horizon_end(now), events),
                              *AdaptivePreparationService(settings.profile)._university_commitments(
                                  events, now, settings.profile.planning_horizon_end(now))]
            preparation_pause = timedelta(minutes=max(0, settings.profile.preparation_break_minutes))
            preparation_spans = {uid: decode(row['data']) for uid, row in base['rows'].items()
                                 if row['kind'] == 'preparation'
                                 and decisions.get(uid, {}).get('kind') != 'deleted'}
            stable_sources = set()
            for key, lesson in by_personal.items():
                future = [block for block in operation.blocks if block.source_event_id == key
                          and block.id.startswith('learning-prep:') and block.end > now]
                if (lesson['homework_state'] == 'assigned' and not lesson['completed'] and future
                        and sum(block.minutes for block in future) == (lesson['remaining_minutes'] or lesson['duration_minutes'])
                        and lesson.get('due_at') and all(block.end <= datetime.fromisoformat(lesson['due_at']) for block in future)
                        and not any(block.start < item.end and block.end > item.start
                                    for block in future for item in stability_busy)
                        and not any(block.start < span.dtend + preparation_pause
                                    and block.end > span.dtstart - preparation_pause
                                    for block in future for uid, span in preparation_spans.items() if uid != block.id)):
                    stable_sources.add(key)
            retained = [block for block in operation.blocks
                        if block.source_event_id not in managed or block.source_event_id in stable_sources
                        or block.start <= now or block.id in decisions or block.source_event_id in protected_sources]
            commitments = [*settings.sleep_commitments(now, settings.profile.planning_horizon_end(now)),
                           *settings.routine_commitments(now, settings.profile.planning_horizon_end(now), events),
                           *CalendarAvailabilityService(self.app.adapter).hard_commitments(external),
                           *(FixedCommitment(id=block.id, title=block.title, start=block.start, end=block.end)
                             for block in retained if block.end > now)]
            remaining_store = SimpleNamespace(lessons=lambda: [row for row in lessons
                if (row.get('source_personal_id') or row['id']) not in stable_sources | protected_sources])
            planning_issue = ''
            try:
                generated = plan_learning_preparations(remaining_store, settings, now + timedelta(minutes=5),
                                                       events, commitments, existing_blocks=retained)
            except ValueError as first_error:
                # Try freeing and rebuilding only unstarted, app-owned generic
                # preparations. Classes and personal appointments stay fixed.
                movable = [block for block in retained if block.start > now
                           and block.id not in decisions and block.source_event_id not in stable_sources | protected_sources
                           and not block.id.startswith('learning-prep:')]
                try:
                    if not movable:
                        raise first_error
                    reduced = [block for block in retained if block not in movable]
                    fixed = [item for item in commitments if item.id not in {block.id for block in movable}]
                    generated = plan_learning_preparations(remaining_store, settings, now + timedelta(minutes=5),
                                                           events, fixed, existing_blocks=reduced)
                    generic = AdaptivePreparationService(settings.profile).build_draft(
                        events, completed_source_event_ids={event.id for event in events
                            if event.id not in {block.source_event_id for block in movable}},
                        now=now + timedelta(minutes=5), fixed_commitments=[*fixed,
                            *(FixedCommitment(id=block.id, title=block.title, start=block.start, end=block.end)
                              for block in generated)])
                    if (generic.no_slot_reasons or any(block.manual_conflict for block in generic.blocks)
                            or {block.source_event_id for block in generic.blocks}
                               != {block.source_event_id for block in movable}):
                        raise first_error
                    old_generic = {block.source_event_id: block for block in movable}
                    generated += [replace(block, id=old_generic[block.source_event_id].id, status='confirmed')
                                  for block in generic.blocks]
                    retained = reduced
                except ValueError:
                    # Material descriptions must still reach Google when there
                    # is no feasible preparation plan.
                    planning_issue = str(first_error)
                    retained, generated = list(operation.blocks), []
            self.app._learning_plan_blocked = bool(planning_issue)
            candidate = deepcopy(operation)
            candidate.blocks = retained + [replace(block, status='confirmed') for block in generated
                                            if block.id not in {item.id for item in retained}]
            rows = deepcopy(base['rows'])
            for uid, row in list(rows.items()):
                if row['kind'] == 'preparation' and uid not in {block.id for block in candidate.blocks}:
                    del rows[uid]
            for uid, lesson in by_personal.items():
                if uid not in rows or decisions.get(uid, {}).get('kind') == 'deleted':
                    continue
                description = rows[uid]['data']['description'].split('\n\nУчебные материалы:\n')[0]
                if lesson['materials']:
                    rows[uid]['data']['description'] = description + '\n\nУчебные материалы:\n' + learning_description(lesson)
            for block in candidate.blocks:
                lesson = by_personal.get(block.source_event_id)
                if block in retained and (lesson is None or not lesson['materials'] or block.start <= now or block.id in decisions):
                    continue
                if lesson:
                    reason = block.reason.split('\n\nУчебные материалы:\n')[0] + '\n\nУчебные материалы:\n' + learning_description(lesson, preparation=True)
                    if block in retained and reason == block.reason:
                        continue  # A publication version alone is not a material change.
                    block = replace(block, reason=reason)
                    candidate.blocks = [block if item.id == block.id else item for item in candidate.blocks]
                data = self.app.projector._event_data(block, candidate)
                rows[block.id] = {'kind': 'preparation', 'data': encode({
                    key: getattr(data, key) for key in ('uid', 'system_block_id', 'system_source_event_id',
                        'system_operation_id', 'summary', 'description', 'location', 'dtstart', 'dtend', 'event_type')})}
            self.check_preparation_conflicts(rows, now, {uid for uid, decision in decisions.items()
                                                         if decision['kind'] == 'deleted'})
            removed = {uid: row for uid, row in base['rows'].items() if uid not in rows}
            if all(uid in base['rows'] and row['data'] == base['rows'][uid]['data'] for uid, row in rows.items()) and not removed:
                if not planning_issue:
                    self.app.learning.store.mark_calendar_synced(self.app.learning.store.calendar_revision())
                return ('Подготовка не перепланирована: ' + planning_issue) if planning_issue else ''
            candidate.version += 1
            payload = {'kind': 'learning', 'base_hash': digest(base), 'rows': rows,
                       'removed': removed, 'attempted': [], 'applied': {},
                       'operation': DraftOperationStore._serialize(candidate), 'planning_issue': planning_issue,
                       'learning_revision': self.app.learning.store.calendar_revision()}
            operation.pending_plan_update = payload
            operation.projection_pending = True
            self.app.operations.save(operation)

        def validate(*, check_availability=False):
            self.check_preparation_conflicts(payload['rows'], self.app.now(), {uid for uid, decision in
                base.get('manual_resolutions', {}).items() if decision['kind'] == 'deleted'})
            if payload['base_hash'] != digest(operation.published_plan):
                raise PlanConflict('Версия плана изменилась. Журнал сохранён для проверки.')
            results = {}
            rows = {**payload['rows'], **payload['removed']}
            pairs = None
            if callable(getattr(self.app.adapter, 'get_event_pairs', None)):
                pairs = self.app.adapter.get_event_pairs(calendar, {
                    uid: base['rows'][uid]['event_id'] if uid in base['rows'] else None
                    for uid in rows})
            for uid, row in rows.items():
                old = base['rows'].get(uid)
                if old:
                    remote, by_id = self._remote(calendar, uid, old,
                        pair=pairs[uid] if pairs is not None else None)
                    decision = base.get('manual_resolutions', {}).get(uid)
                    if decision and decision['kind'] == 'deleted':
                        if not self.deleted(remote, old['event_id']) or not self.deleted(by_id, old['event_id']):
                            raise PlanConflict('Принятое удаление изменилось в Calendar.')
                        results[uid] = None
                        continue
                    if uid in payload['removed'] and uid in payload['attempted'] and self.deleted(remote, old['event_id']) and self.deleted(by_id, old['event_id']):
                        results[uid] = None
                        continue
                    if remote and by_id and (remote.get('etag') != by_id.get('etag')
                                             or by_id.get('iCalUID') != uid):
                        raise PlanConflict('UID и ID Calendar расходятся; нужен оператор.')
                else:
                    remote = pairs[uid][0] if pairs is not None else self.app.adapter.get_event_by_uid(calendar, uid, strict=True)
                if (old and not old.get('etag')) or (remote and not remote.get('etag')):
                    raise PlanConflict('Calendar не предоставил версию события; запись остановлена.')
                desired = payload['rows'].get(uid)
                if uid in payload['attempted'] and desired and self.matches(remote, desired['data']):
                    pass
                elif old:
                    if not self.matches(remote, old['data']) or not old.get('etag') or remote.get('etag') != old['etag']:
                        raise PlanConflict('Ручное изменение Calendar сохранено. Открой /review_calendar.')
                elif remote:
                    raise PlanConflict('Новая подготовка столкнулась с существующим UID.')
                elif uid in payload['attempted']:
                    raise PlanConflict('Результат прежней вставки неоднозначен; нужен оператор.')
                results[uid] = remote
            if not check_availability:
                return results
            fresh_now = self.app.now()
            owned = {(calendar, remote['id']) for remote in results.values() if remote}
            busy = CalendarAvailabilityService(self.app.adapter, excluded_calendar_names=()).load(
                fresh_now, settings.profile.planning_horizon_end(fresh_now))
            for uid, row in payload['rows'].items():
                if row['kind'] != 'preparation':
                    continue
                old = base['rows'].get(uid)
                target = decode(row['data'])
                changed_time = old is None or any(row['data'][field] != old['data'][field]
                                                  for field in ('dtstart', 'dtend'))
                if changed_time and target.dtend > fresh_now and any(
                        (item.calendar_id, item.event_id) not in owned
                        and item.start < target.dtend and item.end > target.dtstart for item in busy):
                    raise PlanConflict('В Calendar появился конфликт с личным событием. '
                                       'Запись остановлена; сохранённый журнал требует нового свободного окна.')
            return results

        candidate = DraftOperationStore._deserialize(payload['operation'])
        # One full preflight also identifies no-op rows. Refresh the complete
        # identity snapshot and availability only before an actual mutation,
        # not once per unchanged class/preparation. Every read remains fresh
        # at each write boundary and final publication verification.
        remotes = validate()
        if preview_only:
            token = uuid4().hex
            self.app.pending = (token, self.app.now() + timedelta(minutes=10), 'learning-resume', digest(payload))
            lines = ['Сохранённое обновление материалов (Calendar пока не изменён):']
            for uid, row in payload['rows'].items():
                old = base['rows'].get(uid)
                if old is None or row['data'] != old['data']:
                    data = decode(row['data'])
                    lines.append(f'• {data.dtstart:%d.%m %H:%M} — {data.summary}')
            lines.append(f'Удаляемых подготовок: {len(payload["removed"])}. Прежний журнал сохранён.')
            lines.append('Продолжить проверку и завершить именно это сохранённое обновление?')
            return self.app.reply('\n'.join(lines), [[
                {'text': 'Завершить обновление', 'callback_data': 'pilot:apply:' + token}]])
        for uid, row in payload['removed'].items():
            remote = remotes[uid]
            if remote:
                remotes = validate(check_availability=True)
                remote = remotes[uid]
            if remote:
                if row['kind'] != 'preparation' or decode(row['data']).dtstart <= self.app.now():
                    raise PlanConflict('Разрешено удалять только будущие подготовки бота.')
                if uid not in payload['attempted']:
                    payload['attempted'].append(uid)
                self.app.operations.save(operation)
                self._require_access(background=True)
                delete_owned_verified(self.app.adapter, calendar, row['event_id'],
                                      lambda event: self.matches(event, row['data']), expected_etag=row['etag'])
                remotes[uid] = None
        for uid, row in payload['rows'].items():
            remote = remotes[uid]
            decision = base.get('manual_resolutions', {}).get(uid)
            if decision and (decision['kind'] == 'deleted' or row['kind'] == 'preparation'):
                continue
            if self.matches(remote, row['data']):
                continue
            remotes = validate(check_availability=True)
            remote = remotes[uid]
            if self.matches(remote, row['data']):
                continue
            data = decode(row['data'])
            old = base['rows'].get(uid)
            if row['kind'] == 'preparation' and data.dtstart <= self.app.now():
                raise PlanConflict('Время подготовки уже наступило; данные сохранены.')
            if row['kind'] == 'class' and (not old or any(data.__dict__[field] != decode(old['data']).__dict__[field]
                    for field in ('dtstart', 'dtend', 'summary', 'location'))):
                raise PlanConflict('Изменение пары требует отдельной проверки расписания.')
            if uid not in payload['attempted']:
                payload['attempted'].append(uid)
            self.app.operations.save(operation)
            action, event_id = ('update', remote['id']) if remote else ('insert', None)
            etag = remote.get('etag') if remote else None
            if row['kind'] == 'class':
                self._require_access(background=True)
                self.app.classes._write_verified(calendar, uid, data, action, event_id,
                                                  expected_etag=etag, allow_insert_retry=False)
            else:
                block = next(item for item in candidate.blocks if item.id == uid)
                self._require_access(background=True)
                self.app.projector._write_verified(candidate, block, calendar, data, action, event_id,
                                                  lambda target: None, expected_etag=etag, allow_insert_retry=False)
        remotes = validate()
        final = deepcopy(payload['rows'])
        for uid, row in final.items():
            remote = remotes[uid]
            if uid not in base.get('manual_resolutions', {}) or remote:
                if not self.matches(remote, row['data']) or not remote.get('etag'):
                    raise PlanConflict('Google не подтвердил сохранённые материалы.')
                row.update(event_id=remote['id'], etag=remote['etag'])
            if row['kind'] == 'preparation' and remote:
                candidate.calendar_event_ids[uid] = remote['id']
        candidate.calendar_event_ids = {key: value for key, value in candidate.calendar_event_ids.items() if key in final}
        candidate.pending_plan_update, candidate.projection_pending = {}, False
        candidate.published_plan = {**deepcopy(base), 'rows': final, 'version': candidate.version}
        for uid, decision in candidate.published_plan.get('manual_resolutions', {}).items():
            if decision['kind'] == 'moved' and final[uid]['kind'] == 'class':
                decision['row'] = deepcopy(final[uid])
        self.app.operations.save(candidate)
        self.app._learning_plan_blocked = bool(payload.get('planning_issue'))
        if not payload.get('planning_issue'):
            self.app.learning.store.mark_calendar_synced(payload.get('learning_revision', 0))
        return 'Google Calendar обновлён.' + (' Подготовка не перепланирована: ' + payload['planning_issue'] if payload.get('planning_issue') else '')
