"""Ownership-safe routing from a Telegram chat to one user application."""
from __future__ import annotations

from typing import Any, Callable

from services.product_analytics import ProductAnalyticsStore
from services.user_registry import UserAccount, UserRegistryStore, UserStatePaths


class TelegramUserRouter:
    """Create handlers from the authenticated chat, never a supplied user ID."""

    def __init__(self, registry: UserRegistryStore, paths: UserStatePaths,
                 factory: Callable[[UserAccount, object], Any],
                 analytics: ProductAnalyticsStore | None = None):
        self.registry, self.paths, self.factory = registry, paths, factory
        self.analytics = analytics
        self._handlers: dict[str, Any] = {}

    def handler_for(self, chat_id: str) -> Any:
        existing = self.registry.resolve(str(chat_id))
        account = self.registry.get_or_create(str(chat_id))
        if existing is None and self.analytics is not None:
            try:
                self.analytics.record_once(account.id, 'registered')
            except (OSError, ValueError):
                # Funnel counters must never block account creation.
                pass
        if account.id not in self._handlers:
            self._handlers[account.id] = self.factory(account, self.paths.directory(account.id))
        return self._handlers[account.id]

    def handle_text(self, chat_id: str, text: str):
        return self.handler_for(chat_id).handle_text(str(chat_id), text)

    def handle_callback(self, chat_id: str, data: str):
        return self.handler_for(chat_id).handle_callback(str(chat_id), data)
