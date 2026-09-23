"""Restricted, operator-provisioned single-student pilot; no legacy write routes."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zoneinfo import ZoneInfo

from models import EventType, PersonalAttendanceRule, PersonalEventState, UniversityEvent, UniversityEventStatus
from scripts.parse_ics import parse_ics
from services.adaptive_preparation_service import AdaptivePreparationService, DraftCalendarProjector
from services.attendance_preferences import AttendancePreferenceStore
from services.attendance_service import AttendanceRuleService
from services.calendar.personal_event_sync_service import PersonalEventSyncService
from services.calendar.projection_state import CalendarProjectionState
from services.calendar_availability_service import CalendarAvailabilityService
from services.draft_operation_store import DraftOperationStore
from services.onboarding_service import OnboardingStep, OnboardingStore, TelegramOnboardingService
from services.product_state import UserProductStateStore
from services.schedule_source_service import ScheduleSourceService
from services.telegram_onboarding_handler import TelegramOnboardingHandler
from services.telegram_schedule_bot import TelegramScheduleBot
from services.tpu_schedule_source import fetch_tpu_group_schedule
from services.university_snapshot_store import UniversitySnapshotStore
from services.user_planning_profile_store import UserPlanningProfileStore
from services.user_registry import UserRegistryStore, UserStatePaths


class PilotSource(ScheduleSourceService):
    """Only public TPU pages; ephemeral exports, current academic week."""
    def __init__(self, state_store, fetcher=fetch_tpu_group_schedule):
        super().__init__(state_store)
        self.fetcher = fetcher

    def _read(self, source):
        if source.kind != 'tpu_group_page':
            raise ValueError('Only TPU is supported in this pilot')
        with TemporaryDirectory(prefix='personalos-tpu-') as directory:
            path = Path(directory) / 'schedule.ics'
            self.fetcher(source.location, path, auto_period=True, export_variant_id=2)
            content = path.read_bytes()
            self._preview(source, content)
            return content

    def events(self, source):
        content = self._read(source)
        with TemporaryDirectory(prefix='personalos-parse-') as directory:
            path = Path(directory) / 'schedule.ics'
            path.write_bytes(content)
            return [UniversityEvent(
                uid=row['uid'], summary=row['summary'], description=row['description'],
                location=row['location'], dtstart=row['dtstart'], dtend=row['dtend'],
                event_type=EventType.from_string(row['event_type'] or ''), is_group_event=True,
                status=UniversityEventStatus.from_string(row['status']), sequence=row['sequence'],
            ) for row in parse_ics(path)]


class ClosedBetaApplication:
    """Explicit preview/apply with a frozen published plan and durable retry.

    Every entry is serialized by PilotBot's operation lock. Approval tokens live
    only in memory, expire in ten minutes and are invalidated by any new command.
    Durable operations survive restart; resuming them requires a fresh preview.
    """
    HELP = ('Закрытый пилот: /start → /connect_tpu → /attendance → /sleep и /travel → '
            '/calendar_status → /weekly_preview. /update_all обновляет только preview. '
            'Запись пар и подготовок — только кнопкой подтверждения. '
            'Изменение опубликованного плана требует помощи оператора.')

    def __init__(self, root, chat_id, adapter, *, source_factory=PilotSource, now=None):
        self.root = Path(root)
        self.chat_id = str(chat_id)
        registry = UserRegistryStore(self.root / 'users_registry.json')
        accounts = registry.accounts()
        if any(account.telegram_chat_id != self.chat_id for account in accounts):
            raise ValueError('Pilot directory belongs to another account')
        self.account = registry.get_or_create(self.chat_id)
        self.directory = UserStatePaths(self.root).directory(self.account.id)
        self.profile = UserPlanningProfileStore(self.directory / 'planning_profile.json')
        self.source = source_factory(UserProductStateStore(self.directory / 'product_state.json'))
        self.snapshot = UniversitySnapshotStore(self.directory / 'university_reconciliation.json')
        self.operations = DraftOperationStore(self.directory / 'draft_operations.json')
        self.adapter = adapter
        self.projector = DraftCalendarProjector(adapter)
        self.classes = PersonalEventSyncService(adapter, CalendarProjectionState(
            self.directory / 'university_calendar_projection.json'))
        self.now = now or (lambda: datetime.now(ZoneInfo(self.profile.load().profile.timezone)))
        self.pending = None
        self.onboarding = TelegramOnboardingService(OnboardingStore(self.directory / 'onboarding.json'))
        self.handler = TelegramOnboardingHandler(
            self.account, self.directory, self.onboarding, self.source, self.source.events,
            self.profile, self._connected, lambda: 'Открой /weekly_preview для расчёта и подтверждения.',
        )

    @staticmethod
    def reply(text, buttons=None):
        return {'text': text, 'buttons': buttons or []}

    def _connected(self):
        if not self.adapter.is_initialized and not asyncio.run(self.adapter.initialize()):
            raise RuntimeError('Calendar unavailable')
        # The pilot adapter authenticates without creating a calendar.
        self.adapter.list_visible_calendars()
        return True

    def handle_text(self, chat_id, text):
        if str(chat_id) != self.chat_id:
            return None
        self.pending = None
        if not isinstance(text, str) or not text.strip():
            return self.reply(self.HELP)
        command = text.split()[0].split('@')[0]
        try:
            if command in {'/weekly_preview', '/preparations', '/update_all', '/update_schedule'}:
                return self.preview()
            if command in {'/start', '/connect_tpu', '/attendance', '/sleep', '/travel', '/calendar_status'}:
                return self.handler.handle_text(chat_id, text)
            return self.reply(self.HELP)
        except Exception:
            return self.reply('Проверка не завершена. Calendar не изменялся. Повтори команду; '
                              'при повторной ошибке обратись к оператору.')

    def handle_callback(self, chat_id, data):
        if str(chat_id) != self.chat_id:
            return None
        if not isinstance(data, str):
            return self.reply(self.HELP)
        if data.startswith('pilot:apply:'):
            return self.apply(data.removeprefix('pilot:apply:'))
        self.pending = None
        if data == 'pilot:cancel':
            return self.reply('Предложение отменено. Запись в Calendar не выполнялась.')
        try:
            if data.startswith(('ob:timezone:', 'att:')) or data == 'ob:calendar:check':
                return self.handler.handle_callback(chat_id, data)
        except Exception:
            return self.reply('Не удалось проверить настройки. Повтори /start; Calendar не изменялся.')
        return self.reply('Эта кнопка недоступна в пилоте. Открой /weekly_preview.')

    def _inputs(self):
        settings = self.profile.load()
        zone = ZoneInfo(settings.profile.timezone)
        now = self.now().astimezone(zone)
        state = self.source.state_store.load(self.account.id)
        source = state.sources[state.active_source_id]
        raw = self.source.events(source)
        def local(value):
            return value.astimezone(zone) if value.tzinfo else value.replace(tzinfo=zone)
        raw = [replace(event, dtstart=local(event.dtstart), dtend=local(event.dtend)) for event in raw]
        prefs = AttendancePreferenceStore.load(self.directory / 'attendance_preferences.json')
        result = self.snapshot.reconcile(raw, AttendanceRuleService(PersonalAttendanceRule(
            id='pilot-attendance', description='Pilot attendance',
            metadata={'attendance_preferences': prefs.as_rule_metadata()},
        )), required_horizon=(now, settings.profile.planning_horizon_end(now)))
        if result.review_events or any(event.state in {
            PersonalEventState.POSSIBLY_CANCELLED, PersonalEventState.CANCELLED,
        } for event in result.personal_events):
            raise ValueError('Source change needs operator review')
        events = result.personal_events
        signature = sha256(json.dumps({
            'source': source.location, 'profile': asdict(settings),
            'events': [(e.id, e.title, e.start_time.isoformat(), e.end_time.isoformat(),
                        e.state.value, e.description, e.location) for e in sorted(events, key=lambda e: e.id)],
        }, sort_keys=True, default=str).encode()).hexdigest()
        self._connected()
        availability = CalendarAvailabilityService(self.adapter, excluded_calendar_names=())
        busy = availability.load(now, settings.profile.planning_horizon_end(now))
        return settings, now, events, signature, busy

    def preview(self):
        if self.onboarding.store.load().step not in {OnboardingStep.CALENDAR, OnboardingStep.COMPLETE}:
            return self.reply('Сначала заверши /start: посещаемость, сон и дорогу.')
        settings, now, events, signature, busy = self._inputs()
        existing = list(self.operations.load_all().values())
        if len(existing) > 1:
            return self.reply('Нужна проверка сохранённых операций оператором. Записи остановлены.')
        operation = existing[0] if existing else None
        if operation and operation.content_hash != signature:
            return self.reply('Расписание или настройки изменились. Опубликованный план сохранён. '
                              'В пилоте замену проверяет оператор; автоматическая запись остановлена.')
        if operation is None:
            commitments = [*settings.sleep_commitments(now, settings.profile.planning_horizon_end(now)),
                           *settings.routine_commitments(now, settings.profile.planning_horizon_end(now), events),
                           *CalendarAvailabilityService(self.adapter).hard_commitments(busy)]
            operation = AdaptivePreparationService(settings.profile).build_draft(
                events, now=now + timedelta(minutes=15), fixed_commitments=commitments)
            operation.content_hash = signature
            # Write confirmed-looking blocks only after explicit approval; no
            # separate remote draft/confirm cycle or automatic replan is exposed.
            operation.blocks = [replace(block, status='confirmed') for block in operation.blocks]
        selected = [e for e in events if e.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
                    and now <= e.start_time < settings.profile.planning_horizon_end(now)]
        lines = ['План закрытого пилота (Calendar пока не изменён):']
        lines += [f'• {e.start_time:%d.%m %H:%M} — {e.title}' for e in selected]
        lines += [f'• {b.start:%d.%m %H:%M}–{b.end:%H:%M}: {b.title}' for b in operation.blocks]
        if operation.no_slot_reasons or any(b.manual_conflict for b in operation.blocks):
            return self.reply('\n'.join(lines) + '\nНе все подготовки помещаются. Запись заблокирована; обратись к оператору.')
        if operation.status == 'confirmed':
            if not self._published_matches(operation, selected):
                return self.reply('Calendar расходится с сохранённым планом. Ручные изменения сохранены; '
                                  'записей не было. Нужна проверка оператором.')
            return self.reply('\n'.join(lines) + '\nЭтот план уже опубликован. Повторных записей нет. '
                              'Ручные изменения Calendar не перезаписываются; расхождения проверяет оператор.')
        if not selected and not operation.blocks:
            return self.reply('Нет выбранных занятий для публикации. Проверь посещаемость с оператором.')
        token = uuid4().hex
        self.pending = (token, now + timedelta(minutes=10), operation, signature,
                        frozenset(event.id for event in selected))
        return self.reply('\n'.join(lines) + '\nЗаписать эти пары и подготовки в отдельный календарь Google?', [[
            {'text': 'Подтвердить запись', 'callback_data': f'pilot:apply:{token}'},
            {'text': 'Отмена', 'callback_data': 'pilot:cancel'},
        ]])

    def _verified_preparation_ids(self, operation):
        owned = set()
        for block in operation.blocks:
            calendar_id = operation.calendar_ids.get(block.calendar.value)
            if not calendar_id:
                continue
            remote = self.adapter.get_event_by_uid(calendar_id, block.id, strict=True)
            if remote is None:
                if block.id in operation.calendar_event_ids:
                    raise ValueError('Published preparation was removed')
                continue
            if not self.projector._matches_projection(remote, self.projector._event_data(block, operation)):
                raise ValueError('Published preparation was edited')
            owned.add((calendar_id, remote['id']))
        return owned

    def _published_matches(self, operation, selected):
        try:
            if len(self._verified_preparation_ids(operation)) != len(operation.blocks):
                return False
        except ValueError:
            return False
        calendars = [c['id'] for c in self.adapter.list_visible_calendars()
                     if c.get('summary') == 'Personal University Schedule']
        if len(calendars) != 1:
            return False
        for event in selected:
            remote = self.adapter.get_event_by_uid(calendars[0], event.id, strict=True)
            if (not remote or not self.classes._owned(remote, event.id)
                    or not self.classes._same_time(remote, event.start_time, event.end_time)
                    or self.classes._get_hash_and_version_from_description(remote.get('description', ''))
                    != (self.classes._compute_event_hash(event), self.classes.VERSION)):
                return False
        return True

    def apply(self, token):
        pending = self.pending
        self.pending = None  # one attempt; failures require a fresh read/preview
        if not pending or token != pending[0] or self.now() >= pending[1]:
            return self.reply('Подтверждение устарело. Открой /weekly_preview.')
        _, _, operation, signature, selected_ids = pending
        writing = False
        try:
            settings, now, events, current_signature, busy = self._inputs()
            if signature != current_signature:
                return self.reply('Данные изменились после preview. Открой /weekly_preview; записи не было.')
            if (any(block.start <= now for block in operation.blocks)
                    or any(event.id in selected_ids and event.start_time <= now for event in events)):
                return self.reply('Время плана устарело. Нужен новый preview или проверка оператором.')
            owned_ids = self._verified_preparation_ids(operation)
            for block in operation.blocks:
                if any((event.calendar_id, event.event_id) not in owned_ids and event.start < block.end and event.end > block.start
                       for event in busy):
                    return self.reply('В Calendar появился конфликт. Запись остановлена; открой /weekly_preview.')
            # Journal precedes ALL writes, including classes, for restart safety.
            operation.projection_pending = True
            self.operations.save(operation)
            writing = True
            calendar_id = self.adapter._get_or_create_calendar('Personal University Schedule')
            for event in events:
                if event.id in selected_ids:
                    if not self.classes.sync_personal_event_to_calendar(event, calendar_id):
                        raise RuntimeError('Class projection not verified')
            self.projector.stage(operation, checkpoint=self.operations.save)
            if operation.manually_deleted_block_ids or operation.manual_calendar_overrides:
                raise RuntimeError('Manual override needs operator review')
            operation.status = 'confirmed'
            operation.projection_pending = False
            self.operations.save(operation)
            return self.reply('План опубликован. /update_all проверит источник и покажет preview без новых записей.')
        except Exception:
            return self.reply(('Запись не завершена; часть событий могла сохраниться. ' if writing else
                               'Проверка не завершена; запись не начиналась. ') +
                              'Повтори /weekly_preview для сверки и нового подтверждения. '
                              'Не очищай состояние; при повторной ошибке обратись к оператору.')


class PilotBot(TelegramScheduleBot):
    """Reuse polling/locking, while keeping every legacy command unreachable."""
    def __init__(self, token, chat_id, application):
        super().__init__(token, chat_id, '', application.directory / 'unused')
        self.application = application
        self.private_owner_only = True

    def handle_text(self, chat_id, text):
        if str(chat_id) != str(self.allowed_chat_id):
            return None
        if not self.operation_lock.acquire(blocking=False):
            return self.application.reply('Проверка уже выполняется. Дождись результата.')
        try:
            return self._handle_text(chat_id, text)
        finally:
            self.operation_lock.release()

    def _handle_text(self, chat_id, text):
        return self.application.handle_text(chat_id, text)

    def _handle_callback(self, chat_id, data):
        return self.application.handle_callback(chat_id, data)
