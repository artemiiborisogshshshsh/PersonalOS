"""Pure update dispatcher for a Telegram-first multi-user runtime."""
from __future__ import annotations

from typing import Any

from services.telegram_user_router import TelegramUserRouter


class TelegramMultiUserDispatcher:
    """Dispatch one update without accepting an untrusted user identifier."""
    def __init__(self, router: TelegramUserRouter): self.router = router

    def handle_update(self, update: dict[str, Any]):
        message = update.get('message') or {}
        chat = message.get('chat') or {}
        text = message.get('text')
        if text is not None and 'id' in chat:
            chat_id = str(chat['id'])
            known = self.router.registry.resolve(chat_id) is not None
            command = text.strip().split(maxsplit=1)[0].split('@', maxsplit=1)[0]
            if not known and command != '/start':
                return None
            return chat_id, self.router.handle_text(chat_id, text)
        callback = update.get('callback_query') or {}
        callback_chat = (callback.get('message') or {}).get('chat') or {}
        if callback.get('data') is None or 'id' not in callback_chat:
            return None
        chat_id = str(callback_chat['id'])
        if self.router.registry.resolve(chat_id) is None:
            return None
        return chat_id, self.router.handle_callback(chat_id, callback['data'])
