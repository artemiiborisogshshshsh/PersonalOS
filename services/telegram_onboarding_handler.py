"""Per-user Telegram onboarding application handler.

The handler deliberately coordinates existing domain services only.  It never
calls a Calendar provider, mutates a university source, or accepts a user ID
from a Telegram command.  The surrounding router binds it to the authenticated
chat before any request reaches this class.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from models import UniversityEvent
from services.attendance_preferences import AttendancePreferenceStore
from services.onboarding_service import OnboardingStep, TelegramOnboardingService
from services.product_analytics import ProductAnalyticsStore
from services.product_state import ScheduleSource, UserProductStateStore
from services.schedule_source_service import ScheduleSourceService
from services.telegram_attendance_onboarding import TelegramAttendanceOnboarding
from services.user_planning_profile_store import UserPlanningProfileStore
from services.user_registry import UserAccount


CalendarConnectionStatus = Callable[[], bool]
WeeklyPreview = Callable[[], str]
SourceEvents = Callable[[ScheduleSource], Iterable[UniversityEvent]]


class TelegramOnboardingHandler:
    """A resumable, ownership-checked onboarding flow for one B2C user."""

    def __init__(
        self,
        account: UserAccount,
        state_directory: Path,
        onboarding: TelegramOnboardingService,
        source_service: ScheduleSourceService,
        events_for_source: SourceEvents,
        profile_store: UserPlanningProfileStore,
        calendar_connected: CalendarConnectionStatus,
        weekly_preview: WeeklyPreview,
        analytics: ProductAnalyticsStore | None = None,
    ) -> None:
        self.account = account
        self.state_directory = state_directory
        self.onboarding = onboarding
        self.source_service = source_service
        self.events_for_source = events_for_source
        self.profile_store = profile_store
        self.calendar_connected = calendar_connected
        self.weekly_preview = weekly_preview
        self.analytics = analytics

    def handle_text(self, chat_id: str, text: str) -> Optional[dict]:
        if not self._owns(chat_id):
            return None
        command, _, value = text.strip().partition(' ')
        command = command.split('@', 1)[0]
        if command == '/start':
            return self.onboarding.start()
        if command == '/connect_tpu':
            return self._connect_tpu(value.strip())
        if command == '/attendance':
            return self._attendance_start()
        if command == '/sleep':
            return self._set_sleep(value)
        if command == '/travel':
            return self._set_travel(value)
        if command == '/calendar_status':
            return self._calendar_status()
        if command == '/weekly_preview':
            return self._weekly_preview()
        return {
            'text': 'Продолжи onboarding через /start.',
            'buttons': [],
        }

    def handle_callback(self, chat_id: str, data: str) -> Optional[dict]:
        if not self._owns(chat_id):
            return None
        if data.startswith('ob:timezone:'):
            return self._set_timezone(data.removeprefix('ob:timezone:'))
        if data == 'ob:calendar:check':
            return self._calendar_status()
        if data.startswith('att:'):
            return self._attendance_callback(data)
        return {'text': 'Эта кнопка больше не актуальна. Открой /start.', 'buttons': []}

    def _set_timezone(self, timezone_name: str) -> dict:
        # ``set_timezone`` validates with ZoneInfo before any user state is
        # modified.  Keep product and planning state aligned with it.
        response = self.onboarding.set_timezone(timezone_name)
        settings = self.profile_store.load()
        settings.profile = replace(settings.profile, timezone=timezone_name)
        self.profile_store.save(settings)
        state = self.source_service.state_store.load(self.account.id)
        state.timezone = timezone_name
        self.source_service.state_store.save(state)
        return response

    def _connect_tpu(self, url: str) -> dict:
        if not url:
            return {
                'text': 'Формат: /connect_tpu https://ro-rasp.tpu.ru/gruppa_.../2026/1/view.html',
                'buttons': [],
            }
        if self.onboarding.store.load().step not in {OnboardingStep.SOURCE, OnboardingStep.ATTENDANCE}:
            return {'text': 'Сначала выбери часовой пояс через /start.', 'buttons': []}
        try:
            source = ScheduleSource(
                id='tpu-primary', kind='tpu_group_page', location=url,
                display_name='TPU',
            )
            preview = self.source_service.connect_and_preview(self.account.id, source)
            # A preview must be meaningful before we activate it.  The source
            # service already verifies ICS structure and intervals.
            if preview.event_count <= 0:
                raise ValueError('Источник не содержит событий')
            self.source_service.activate(self.account.id, source.id)
            response = self.onboarding.source_connected(source.id)
        except (OSError, ValueError, KeyError):
            # Provider details may include URLs/tokens; keep the user-facing
            # reply useful without exposing either.
            return {
                'text': 'Не удалось проверить TPU-расписание. Проверь публичную ссылку группы и повтори попытку.',
                'buttons': [],
            }
        self._record_milestone('source_connected')
        samples = ', '.join(preview.sample_titles[:3]) or 'без названий'
        return {
            **response,
            'text': (
                f'Расписание TPU проверено: {preview.event_count} занятий. '
                f'Примеры: {samples}.\n\n{response["text"]}'
            ),
        }

    def _attendance_start(self) -> dict:
        if self.onboarding.store.load().step != OnboardingStep.ATTENDANCE:
            return {'text': 'Сначала подключи и проверь TPU-расписание.', 'buttons': []}
        try:
            return self._attendance_flow().start(self._active_source_events())
        except (KeyError, ValueError):
            return {
                'text': 'Источник расписания недоступен для настройки посещаемости. Повтори /connect_tpu.',
                'buttons': [],
            }

    def _attendance_callback(self, data: str) -> dict:
        if self.onboarding.store.load().step != OnboardingStep.ATTENDANCE:
            return {'text': 'Настройка посещаемости уже завершена. Открой /start.', 'buttons': []}
        try:
            flow = self._attendance_flow()
            # Rebuild subject mapping from the durable active source on every
            # callback: a process restart never loses the lab-choice context.
            flow.start(self._active_source_events())
            response = flow.handle_callback(data)
        except (IndexError, KeyError, ValueError):
            return {'text': 'Не удалось применить ответ. Открой /attendance и выбери вариант ещё раз.', 'buttons': []}
        if response.get('attendance_complete'):
            next_step = self.onboarding.attendance_completed()
            self._record_milestone('attendance_completed')
            response = {
                **response,
                'text': f'{response["text"]}\n\n{next_step["text"]}',
            }
        return response

    def _set_sleep(self, raw: str) -> dict:
        values = raw.split()
        if len(values) != 2:
            return {'text': 'Формат: /sleep 23:00 06:40', 'buttons': []}
        try:
            time.fromisoformat(values[0])
            time.fromisoformat(values[1])
        except ValueError:
            return {'text': 'Время сна укажи в формате ЧЧ:ММ.', 'buttons': []}
        if self.onboarding.store.load().step != OnboardingStep.PROFILE:
            return {'text': 'Сначала заверши настройку посещаемости.', 'buttons': []}
        settings = self.profile_store.load()
        settings.sleep_start, settings.sleep_end = values
        self.profile_store.save(settings)
        return self.onboarding.profile_setting_saved('sleep')

    def _set_travel(self, raw: str) -> dict:
        try:
            minutes = int(raw.strip())
        except ValueError:
            minutes = -1
        if minutes < 0 or minutes > 360:
            return {'text': 'Укажи дорогу целым числом от 0 до 360 минут: /travel 60', 'buttons': []}
        if self.onboarding.store.load().step != OnboardingStep.PROFILE:
            return {'text': 'Сначала заверши настройку посещаемости.', 'buttons': []}
        settings = self.profile_store.load()
        settings.profile = replace(settings.profile, travel_minutes_each_way=minutes)
        self.profile_store.save(settings)
        return self.onboarding.profile_setting_saved('travel')

    def _calendar_status(self) -> dict:
        if self.onboarding.store.load().step != OnboardingStep.CALENDAR:
            return {'text': 'Сначала настрой посещаемость, сон и дорогу.', 'buttons': []}
        if not self.calendar_connected():
            return {
                'text': 'Google Calendar пока не подключён. Подключи его в настройках и повтори /calendar_status.',
                'buttons': [[{'text': 'Повторить проверку', 'callback_data': 'ob:calendar:check'}]],
            }
        response = self.onboarding.calendar_checked()
        return {
            **response,
            'text': f'Google Calendar подключён.\n\n{self._weekly_preview()["text"]}',
        }

    def _weekly_preview(self) -> dict:
        step = self.onboarding.store.load().step
        if step not in {OnboardingStep.CALENDAR, OnboardingStep.COMPLETE}:
            return {'text': 'Сначала заверши предыдущие шаги onboarding.', 'buttons': []}
        text = self.weekly_preview()
        self._record_milestone('first_plan')
        return {'text': text, 'buttons': []}

    def _record_milestone(self, name: str) -> None:
        if self.analytics is None:
            return
        try:
            self.analytics.record_once(self.account.id, name)
        except (OSError, ValueError):
            # Analytics is deliberately best-effort and never blocks onboarding.
            pass

    def _attendance_flow(self) -> TelegramAttendanceOnboarding:
        return TelegramAttendanceOnboarding(
            AttendancePreferenceStore.load(self.state_directory / 'attendance_preferences.json'),
        )

    def _active_source_events(self) -> tuple[UniversityEvent, ...]:
        state = self.source_service.state_store.load(self.account.id)
        if not state.active_source_id:
            raise KeyError('No active source')
        source = state.sources[state.active_source_id]
        return tuple(self.events_for_source(source))

    def _owns(self, chat_id: str) -> bool:
        return str(chat_id) == self.account.telegram_chat_id
