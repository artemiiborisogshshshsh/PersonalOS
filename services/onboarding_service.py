"""Resumable Telegram-first onboarding state, without provider side effects."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo
import json
import os


class OnboardingStep(str, Enum):
    SOURCE = 'source'
    TIMEZONE = 'timezone'
    CALENDAR = 'calendar'
    STYLE = 'style'
    ATTENDANCE = 'attendance'
    PROFILE = 'profile'
    COMPLETE = 'complete'


@dataclass
class OnboardingState:
    step: OnboardingStep = OnboardingStep.SOURCE
    timezone: str = 'Asia/Tomsk'
    timezone_configured: bool = False
    source_id: str = ''
    # Keep the two essential planning settings separate.  Merely opening the
    # profile screen must not silently mark either of them as chosen.
    sleep_configured: bool = False
    travel_configured: bool = False
    style_configured: bool = False


class OnboardingStore:
    def __init__(self, path: Path): self.path = path
    def load(self) -> OnboardingState:
        if not self.path.exists(): return OnboardingState()
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        step = raw['step']
        # Older onboarding versions asked timezone and attendance before Google.
        # Keep completed setup values and resume at the first newly required step.
        timezone_configured = raw.get('timezone_configured')
        if timezone_configured is None:
            timezone_configured = step != OnboardingStep.TIMEZONE.value or bool(raw.get('source_id'))
        if ('style_configured' not in raw and step in {
                OnboardingStep.ATTENDANCE.value, OnboardingStep.PROFILE.value,
                OnboardingStep.CALENDAR.value}):
            step = OnboardingStep.CALENDAR.value
        return OnboardingState(
            step=OnboardingStep(step),
            timezone=raw.get('timezone', 'Asia/Tomsk'),
            timezone_configured=timezone_configured,
            source_id=raw.get('source_id', ''),
            sleep_configured=raw.get('sleep_configured', False),
            travel_configured=raw.get('travel_configured', False),
            style_configured=raw.get('style_configured', False),
        )
    def save(self, state: OnboardingState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True); temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as output:
                temporary = Path(output.name); json.dump(asdict(state), output); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists(): temporary.unlink()


class TelegramOnboardingService:
    def __init__(self, store: OnboardingStore): self.store = store
    def start(self) -> dict:
        return self._render(self.store.load())
    def set_timezone(self, timezone_name: str) -> dict:
        ZoneInfo(timezone_name)
        state = self.store.load(); state.timezone = timezone_name; state.timezone_configured = True
        if state.step != OnboardingStep.COMPLETE:
            state.step = OnboardingStep.CALENDAR if state.source_id else OnboardingStep.SOURCE
        self.store.save(state)
        return self._render(state)
    def source_connected(self, source_id: str) -> dict:
        if not source_id: raise ValueError('Source ID is required')
        state = self.store.load(); state.source_id = source_id
        if state.step != OnboardingStep.COMPLETE:
            state.step = OnboardingStep.CALENDAR if state.timezone_configured else OnboardingStep.TIMEZONE
        self.store.save(state)
        return self._render(state)
    def attendance_completed(self) -> dict:
        # Attendance remains available as a follow-up setting, not a setup gate.
        return self._render(self.store.load())
    def profile_completed(self) -> dict:
        """Legacy shortcut for callers which already save both settings."""
        state = self.store.load()
        if state.source_id and state.timezone_configured and state.style_configured:
            state.step = OnboardingStep.COMPLETE
        self.store.save(state)
        return self._render(state)

    def calendar_connected(self) -> dict:
        state = self.store.load()
        if state.step in {OnboardingStep.CALENDAR, OnboardingStep.COMPLETE}:
            if state.step != OnboardingStep.COMPLETE:
                state.step = OnboardingStep.STYLE
        self.store.save(state)
        return self._render(state)

    def style_completed(self) -> dict:
        state = self.store.load()
        if state.step != OnboardingStep.STYLE:
            return self._render(state)
        state.style_configured = True
        state.step = (OnboardingStep.COMPLETE if state.sleep_configured and state.travel_configured
                      else OnboardingStep.PROFILE)
        self.store.save(state)
        return self._render(state)
    def profile_setting_saved(self, setting: str) -> dict:
        """Advance only after the user explicitly chose sleep *and* travel."""
        if setting not in {'sleep', 'travel'}:
            raise ValueError('Unknown onboarding profile setting')
        state = self.store.load()
        if setting == 'sleep':
            state.sleep_configured = True
        else:
            state.travel_configured = True
        if state.sleep_configured and state.travel_configured:
            if state.source_id and state.timezone_configured and state.style_configured:
                state.step = OnboardingStep.COMPLETE
        self.store.save(state)
        return self._render(state)
    def calendar_checked(self) -> dict:
        state = self.store.load()
        if (state.source_id and state.timezone_configured and state.style_configured
                and state.sleep_configured and state.travel_configured):
            state.step = OnboardingStep.COMPLETE
        self.store.save(state)
        return self._render(state)
    @staticmethod
    def _render(state: OnboardingState) -> dict:
        missing_profile_settings = []
        if not state.sleep_configured:
            missing_profile_settings.append('сон: /sleep 23:00 06:40')
        if not state.travel_configured:
            missing_profile_settings.append('дорогу: /travel 60')
        profile_prompt = (
            'Укажи ' + ' и '.join(missing_profile_settings)
            + ', и ограничения планирования.'
        ) if missing_profile_settings else 'Укажи ограничения планирования.'
        prompts = {
            OnboardingStep.SOURCE: ('Пришли ссылку на расписание TPU: /connect_tpu https://ro-rasp.tpu.ru/gruppa_.../2026/1/view.html', []),
            OnboardingStep.TIMEZONE: ('Проверь часовой пояс.', [[{'text': 'Томск (Asia/Tomsk)', 'callback_data': 'ob:timezone:Asia/Tomsk'}]]),
            OnboardingStep.CALENDAR: ('Обязательно подключи Google Calendar: /connect_google, затем /calendar_status.', []),
            OnboardingStep.STYLE: ('Выбери стиль общения:', [[
                {'text': 'По делу', 'callback_data': 'ob:style:direct'},
                {'text': 'Дружелюбно', 'callback_data': 'ob:style:friendly'},
                {'text': 'Неформально', 'callback_data': 'ob:style:casual'},
            ]]),
            OnboardingStep.ATTENDANCE: ('Настрой посещаемость и выбранную лабораторную: /attendance', []),
            OnboardingStep.PROFILE: (profile_prompt, []),
            OnboardingStep.COMPLETE: ('Настройка завершена. Первый план: /weekly_preview.', []),
        }
        text, buttons = prompts[state.step]
        return {'text': text, 'buttons': buttons, 'step': state.step.value}
