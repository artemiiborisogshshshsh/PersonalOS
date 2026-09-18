"""Ownership-safe routing from a Telegram chat to one user application."""
from __future__ import annotations

from typing import Any, Callable

from services.user_registry import UserAccount, UserRegistryStore, UserStatePaths


class TelegramUserRouter:
    """Create handlers from the authenticated chat, never a supplied user ID."""

    def __init__(self, registry: UserRegistryStore, paths: UserStatePaths,
                 factory: Callable[[UserAccount, object], Any]):
        self.registry, self.paths, self.factory = registry, paths, factory
        self._handlers: dict[str, Any] = {}

    def handler_for(self, chat_id: str) -> Any:
        account = self.registry.get_or_create(str(chat_id))
        if account.id not in self._handlers:
            self._handlers[account.id] = self.factory(account, self.paths.directory(account.id))
        return self._handlers[account.id]

    def handle_text(self, chat_id: str, text: str):
        return self.handler_for(chat_id).handle_text(str(chat_id), text)

    def handle_callback(self, chat_id: str, data: str):
        return self.handler_for(chat_id).handle_callback(str(chat_id), data)
