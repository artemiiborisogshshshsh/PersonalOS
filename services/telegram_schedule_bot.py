"""Telegram command endpoint for a manual university-schedule refresh."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional
import os

import requests

from services.university_schedule_source import (
    UniversityScheduleFetchError,
    UniversityScheduleFetchResult,
    fetch_university_schedule,
)


@dataclass
class TelegramScheduleBot:
    """Minimal, allow-listed long-polling bot for schedule refresh requests."""

    token: str
    allowed_chat_id: str
    source_url: str
    output_path: Path
    fetcher: Callable[[str, Path], UniversityScheduleFetchResult] = (
        fetch_university_schedule
    )
    attendance_onboarding: Any = None
    events_loader: Optional[Callable[[], list[Any]]] = None
    calendar_preview: Optional[Callable[[], str]] = None
    calendar_apply: Optional[Callable[[], str]] = None
    request_timeout: int = 35

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError('TELEGRAM_BOT_TOKEN is required')
        if not self.allowed_chat_id:
            raise ValueError('TELEGRAM_CHAT_ID is required')
        self.base_url = f'https://api.telegram.org/bot{self.token}'

    @classmethod
    def from_environment(
        cls,
        source_url: str,
        output_path: Path,
    ) -> 'TelegramScheduleBot':
        return cls(
            token=os.environ.get('TELEGRAM_BOT_TOKEN', ''),
            allowed_chat_id=os.environ.get('TELEGRAM_CHAT_ID', ''),
            source_url=source_url,
            output_path=output_path,
        )

    def handle_text(self, chat_id: Any, text: str) -> Optional[str]:
        """Handle a single incoming message and return an optional response."""
        if str(chat_id) != str(self.allowed_chat_id):
            return None

        command = text.strip().split(maxsplit=1)[0].split('@', maxsplit=1)[0]
        if command in {'/start', '/help'}:
            return 'Команды: /update_schedule, /attendance.'
        if command == '/attendance':
            if self.attendance_onboarding is None or self.events_loader is None:
                return 'Настройка посещаемости пока не подключена.'
            return self.attendance_onboarding.start(self.events_loader())
        if command != '/update_schedule':
            return 'Неизвестная команда. Используйте /update_schedule.'

        try:
            result = self.fetcher(self.source_url, self.output_path)
        except (ValueError, UniversityScheduleFetchError) as error:
            return f'Не удалось обновить расписание: {error}'
        return (
            f'Расписание обновлено: {result.event_count} событий.\n'
            f'Файл: {result.output_path.name}\n'
            f'Хеш: {result.content_hash[:12]}'
        )

    def handle_callback(self, chat_id: Any, data: str) -> Optional[dict]:
        if str(chat_id) != str(self.allowed_chat_id):
            return None
        if data == 'cal:preview' and self.calendar_preview:
            return {'text': self.calendar_preview(), 'buttons': []}
        if data == 'cal:apply' and self.calendar_apply:
            return {'text': self.calendar_apply(), 'buttons': []}
        if data == 'cal:cancel':
            return {'text': 'Синхронизация отменена. Google Calendar не изменён.', 'buttons': []}
        if not data.startswith('att:'):
            return None
        if self.attendance_onboarding is None:
            return None
        return self.attendance_onboarding.handle_callback(data)

    def send_message(self, chat_id: Any, text: str, buttons: Optional[list] = None) -> None:
        payload: Dict[str, Any] = {'chat_id': chat_id, 'text': text}
        if buttons:
            payload['reply_markup'] = {'inline_keyboard': buttons}
        response = requests.post(
            f'{self.base_url}/sendMessage',
            json=payload,
            timeout=10,
        )
        response.raise_for_status()

    def run_forever(self) -> None:
        """Poll Telegram and process only the small allow-listed command set."""
        offset: Optional[int] = None
        while True:
            params: Dict[str, Any] = {
                'timeout': 25,
                'allowed_updates': ['message', 'callback_query'],
            }
            if offset is not None:
                params['offset'] = offset
            try:
                response = requests.get(
                    f'{self.base_url}/getUpdates',
                    params=params,
                    timeout=self.request_timeout,
                )
                response.raise_for_status()
                updates = response.json().get('result', [])
            except requests.RequestException:
                continue

            for update in updates:
                offset = max(offset or 0, update.get('update_id', 0) + 1)
                message = update.get('message') or {}
                chat = message.get('chat') or {}
                text = message.get('text')
                if text is None or 'id' not in chat:
                    callback = update.get('callback_query') or {}
                    callback_message = callback.get('message') or {}
                    callback_chat = callback_message.get('chat') or {}
                    if callback.get('data') is None or 'id' not in callback_chat:
                        continue
                    reply = self.handle_callback(callback_chat['id'], callback['data'])
                    chat = callback_chat
                else:
                    reply = self.handle_text(chat['id'], text)
                if reply is None:
                    continue
                try:
                    if isinstance(reply, dict):
                        self.send_message(
                            chat['id'], reply['text'], reply.get('buttons'),
                        )
                    else:
                        self.send_message(chat['id'], reply)
                except requests.RequestException:
                    continue
