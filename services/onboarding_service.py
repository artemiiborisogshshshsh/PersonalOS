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
    TIMEZONE = 'timezone'
    SOURCE = 'source'
    ATTENDANCE = 'attendance'
    PROFILE = 'profile'
    CALENDAR = 'calendar'
    COMPLETE = 'complete'


@dataclass
class OnboardingState:
    step: OnboardingStep = OnboardingStep.TIMEZONE
    timezone: str = 'Asia/Tomsk'
    source_id: str = ''
    # Keep the two essential planning settings separate.  Merely opening the
    # profile screen must not silently mark either of them as chosen.
    sleep_configured: bool = False
    travel_configured: bool = False


class OnboardingStore:
    def __init__(self, path: Path): self.path = path
    def load(self) -> OnboardingState:
        if not self.path.exists(): return OnboardingState()
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        return OnboardingState(
            step=OnboardingStep(raw['step']),
            timezone=raw.get('timezone', 'Asia/Tomsk'),
            source_id=raw.get('source_id', ''),
            sleep_configured=raw.get('sleep_configured', False),
            travel_configured=raw.get('travel_configured', False),
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
        state = self.store.load(); state.timezone = timezone_name; state.step = OnboardingStep.SOURCE; self.store.save(state)
        return self._render(state)
    def source_connected(self, source_id: str) -> dict:
        if not source_id: raise ValueError('Source ID is required')
        state = self.store.load(); state.source_id = source_id; state.step = OnboardingStep.ATTENDANCE; self.store.save(state)
        return self._render(state)
    def attendance_completed(self) -> dict:
        state = self.store.load(); state.step = OnboardingStep.PROFILE; self.store.save(state); return self._render(state)
    def profile_completed(self) -> dict:
        """Legacy shortcut for callers which already save both settings."""
        state = self.store.load(); state.step = OnboardingStep.CALENDAR; self.store.save(state); return self._render(state)
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
            state.step = OnboardingStep.CALENDAR
        self.store.save(state)
        return self._render(state)
    def calendar_checked(self) -> dict:
        state = self.store.load(); state.step = OnboardingStep.COMPLETE; self.store.save(state); return self._render(state)
    @staticmethod
    def _render(state: OnboardingState) -> dict:
        prompts = {
            OnboardingStep.TIMEZONE: ('Добро пожаловать. Выбери часовой пояс.', [[{'text': 'Томск (Asia/Tomsk)', 'callback_data': 'ob:timezone:Asia/Tomsk'}]]),
            OnboardingStep.SOURCE: ('Пришли TPU group URL: /connect_tpu https://ro-rasp.tpu.ru/gruppa_.../2026/1/view.html', []),
            OnboardingStep.ATTENDANCE: ('Настрой посещаемость и выбранную лабораторную: /attendance', []),
            OnboardingStep.PROFILE: ('Настрой сон и дорогу: /sleep 23:00 06:40, затем /travel 60.', []),
            OnboardingStep.CALENDAR: ('Проверь статус Google Calendar: /calendar_status. Затем открой /weekly_preview.', []),
            OnboardingStep.COMPLETE: ('Onboarding завершён. Основная команда: /update_all.', []),
        }
        text, buttons = prompts[state.step]
        return {'text': text, 'buttons': buttons, 'step': state.step.value}
