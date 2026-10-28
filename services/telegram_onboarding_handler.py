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

from models import EventType, UniversityEvent
from services.attendance_preferences import (
    AttendancePreferenceStore, SubjectAttendancePreference, course_name, lab_slot_key,
)
from services.onboarding_service import OnboardingStep, TelegramOnboardingService
from services.product_analytics import ProductAnalyticsStore
from services.product_state import ScheduleSource, UserProductStateStore
from services.schedule_source_service import ScheduleSourceService
from services.telegram_attendance_onboarding import TelegramAttendanceOnboarding
from services.telegram_task_planning import TelegramTaskPlanningFlow
from services.user_planning_profile_store import (
    COMMUNICATION_STYLES, UserPlanningProfileStore, normalize_communication_style,
)
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
        natural_text_proposals=None,
        task_planning: TelegramTaskPlanningFlow | None = None,
        calendar_list: Callable[[], Iterable[dict]] | None = None,
    ) -> None:
        self.account = account
        self.state_directory = state_directory
        self.onboarding = onboarding
        self.source_service = source_service
        self.events_for_source = events_for_source
        self.profile_store = profile_store
        self.calendar_connected = calendar_connected
        self.calendar_list = calendar_list
        self.weekly_preview = weekly_preview
        self.analytics = analytics
        self.natural_text_proposals = natural_text_proposals
        self.task_planning = task_planning if task_planning is not None else TelegramTaskPlanningFlow(
            account.telegram_chat_id, state_directory,
        )

    def handle_text(self, chat_id: str, text: str) -> Optional[dict]:
        if not self._owns(chat_id):
            return None
        command, _, value = text.strip().partition(' ')
        command = command.split('@', 1)[0]
        if command == '/start':
            return self.onboarding.start()
        if command == '/style':
            return self._set_style(value.strip())
        if not text.strip().startswith('/') and self._looks_like_tpu_link(text.strip()):
            return self._connect_tpu(text.strip())
        if not text.strip().startswith('/') and self._style_from_message(text) is not None:
            return self._set_style(self._style_from_message(text))
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
        if command in {'/tasks', '/task_estimate', '/planned_tasks'}:
            return self.task_planning.handle_text(chat_id, text)
        if not text.strip().startswith('/') and self.natural_text_proposals is not None:
            return self.natural_text_proposals.propose(chat_id, text)
        return {
            'text': 'Продолжи onboarding через /start.',
            'buttons': [],
        }

    def try_handle_preferences(self, text: str) -> Optional[dict]:
        """Handle a schedule link or explicit style change before other intents."""
        if self._looks_like_tpu_link(text.strip()):
            return self._connect_tpu(text.strip())
        style = self._style_from_message(text)
        return self._set_style(style) if style is not None else None

    def initialize_attendance_defaults(self, events: Iterable[UniversityEvent]) -> None:
        """Persist safe defaults for unanswered sessions without changing user choices.

        Lectures and practicals default to attended. A lab defaults to attended
        only when the source offers one unambiguous weekly slot; multiple lab
        choices remain unanswered for the existing /attendance flow.
        """
        store = AttendancePreferenceStore.load(self.state_directory / 'attendance_preferences.json')
        grouped: dict[str, list[UniversityEvent]] = {}
        for event in events:
            if event.is_group_event:
                grouped.setdefault(course_name(event), []).append(event)
        changed = False
        for subject, subject_events in grouped.items():
            preference = store.subjects.get(subject)
            if preference is not None and preference.excluded:
                continue
            if preference is None:
                preference = SubjectAttendancePreference()
                store.subjects[subject] = preference
                changed = True
            lectures = [event for event in subject_events if event.event_type == EventType.LECTURE]
            if preference.lectures is None and lectures:
                preference.lectures = True
                preference.defaulted_lecture_uids.update(event.uid for event in lectures if event.uid)
                changed = True
            practicals = [event for event in subject_events if event.event_type == EventType.PRACTICAL]
            if preference.practicals is None and practicals:
                preference.practicals = True
                preference.defaulted_practical_uids.update(event.uid for event in practicals if event.uid)
                changed = True
            labs = [event for event in subject_events if event.event_type == EventType.LAB]
            lab_slots = {lab_slot_key(event) for event in labs}
            if preference.labs_enabled is None and len(lab_slots) == 1:
                preference.labs_enabled = True
                preference.lab_slots.update(lab_slots)
                preference.defaulted_lab_uids.update(event.uid for event in labs if event.uid)
                changed = True
        if changed:
            store.save()

    def handle_callback(self, chat_id: str, data: str) -> Optional[dict]:
        if not self._owns(chat_id):
            return None
        if data.startswith('ob:timezone:'):
            if self.onboarding.store.load().step not in {OnboardingStep.TIMEZONE, OnboardingStep.SOURCE}:
                return {'text': 'Эта кнопка больше не актуальна. Открой /start.', 'buttons': []}
            return self._set_timezone(data.removeprefix('ob:timezone:'))
        if data.startswith('ob:style:'):
            if self.onboarding.store.load().step != OnboardingStep.STYLE:
                return {'text': 'Эта кнопка больше не актуальна. Открой /start.', 'buttons': []}
            return self._set_style(data.removeprefix('ob:style:'), onboarding_choice=True)
        if data == 'ob:calendar:check':
            return self._calendar_status()
        if data.startswith('att:'):
            return self._attendance_callback(data)
        if data.startswith('nl:') and self.natural_text_proposals is not None:
            return self.natural_text_proposals.handle_callback(chat_id, data)
        if data.startswith('tp:'):
            return self.task_planning.handle_callback(chat_id, data)
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

    def _set_style(self, value: str, onboarding_choice: bool = False) -> dict:
        try:
            style = normalize_communication_style(value)
        except ValueError:
            if value.strip():
                return {'text': 'Выбери стиль: «По делу», «Дружелюбно» или «Неформально».', 'buttons': []}
            return {'text': 'Формат: /style По делу', 'buttons': []}
        settings = self.profile_store.load()
        settings.communication_style = style
        self.profile_store.save(settings)
        if self.onboarding.store.load().step == OnboardingStep.STYLE:
            return self.onboarding.style_completed()
        return {'text': f'Стиль общения изменён: {COMMUNICATION_STYLES[style]}.', 'buttons': []}

    @staticmethod
    def _style_from_message(text: str) -> str | None:
        normalized = ' '.join(text.casefold().replace('ё', 'е').split())
        if any(phrase in normalized for phrase in ('по делу', 'короче и по делу', 'стиль по делу')):
            return 'direct'
        if any(phrase in normalized for phrase in ('дружелюбно', 'дружелюбный стиль')):
            return 'friendly'
        if any(phrase in normalized for phrase in ('неформально', 'неформальный стиль')):
            return 'casual'
        return None

    @staticmethod
    def _looks_like_tpu_link(value: str) -> bool:
        return 'ro-rasp.tpu.ru/gruppa_' in value and value.startswith(('https://', 'http://'))

    def _connect_tpu(self, url: str) -> dict:
        if not url:
            return {
                'text': 'Формат: /connect_tpu https://ro-rasp.tpu.ru/gruppa_.../2026/1/view.html',
                'buttons': [],
            }
        if self.onboarding.store.load().step not in {OnboardingStep.SOURCE, OnboardingStep.TIMEZONE,
                                                      OnboardingStep.ATTENDANCE}:
            return {'text': 'Открой /start и продолжи настройку.', 'buttons': []}
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
            self.initialize_attendance_defaults(self._active_source_events())
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
        try:
            response = self._attendance_flow().start(self._active_source_events())
            if response.get('attendance_complete'):
                self._record_milestone('attendance_completed')
            return response
        except (KeyError, ValueError):
            return {
                'text': 'Источник расписания недоступен для настройки посещаемости. Повтори /connect_tpu.',
                'buttons': [],
            }

    def _attendance_callback(self, data: str) -> dict:
        try:
            flow = self._attendance_flow()
            # Rebuild subject mapping from the durable active source on every
            # callback: a process restart never loses the lab-choice context.
            flow.start(self._active_source_events())
            response = flow.handle_callback(data)
        except (IndexError, KeyError, ValueError):
            return {'text': 'Не удалось применить ответ. Открой /attendance и выбери вариант ещё раз.', 'buttons': []}
        if response.get('attendance_complete'):
            self.onboarding.attendance_completed()
            self._record_milestone('attendance_completed')
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
        if self.onboarding.store.load().step not in {OnboardingStep.CALENDAR, OnboardingStep.COMPLETE}:
            return {'text': 'Сначала настрой расписание, Google Calendar, стиль и ограничения.', 'buttons': []}
        if not self.calendar_connected():
            return {
                'text': 'Google Calendar пока не подключён. Открой /connect_google и повтори /calendar_status.',
                'buttons': [[{'text': 'Повторить проверку', 'callback_data': 'ob:calendar:check'}]],
            }
        response = (self.onboarding.calendar_connected()
                    if self.onboarding.store.load().step == OnboardingStep.CALENDAR
                    else self.onboarding.calendar_checked())
        primary_id = None
        if self.calendar_list is not None:
            try:
                calendars = list(self.calendar_list())
                primary = [row for row in calendars
                           if isinstance(row, dict) and row.get('primary') is True
                           and isinstance(row.get('id'), str) and row['id'].strip()]
                if len(primary) == 1:
                    primary_id = ' '.join(primary[0]['id'].split())[:200]
            except Exception:
                # Calendar connectivity already succeeded; a failed detail read
                # must not expose provider errors or block the existing status flow.
                pass
        details = f'\nОсновной календарь: {primary_id}' if primary_id else ''
        if response.get('step') == OnboardingStep.STYLE.value:
            return {**response, 'text': f'Google Calendar подключён.{details}\n\n{response["text"]}'}
        return {**response, 'text': f'Google Calendar подключён.{details}\n\n{self._weekly_preview()["text"]}'}

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
            timezone=self.profile_store.load().profile.timezone,
        )

    def _active_source_events(self) -> tuple[UniversityEvent, ...]:
        state = self.source_service.state_store.load(self.account.id)
        if not state.active_source_id:
            raise KeyError('No active source')
        source = state.sources[state.active_source_id]
        return tuple(self.events_for_source(source))

    def _owns(self, chat_id: str) -> bool:
        return str(chat_id) == self.account.telegram_chat_id
