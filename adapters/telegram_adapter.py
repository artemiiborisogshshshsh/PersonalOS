"""
Telegram adapter for Personal OS AI Calendar system.
Provides infrastructure for sending notifications via Telegram Bot API.
"""

import os
from typing import Dict, Any, Optional, List
import requests

from .base_adapter import NotificationAdapter


class TelegramAdapter(NotificationAdapter):
    """
    Telegram adapter implementation.
    Handles sending notifications via Telegram Bot API.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize Telegram adapter.

        Args:
            config: Configuration dictionary with keys:
                - bot_token: Telegram bot token
                - chat_id: Target chat ID or username
                - parse_mode: Message parsing mode (default: HTML)
                - disable_notification: Send silent notification (default: False)
                - use_mcp: Whether to use MCP server instead of direct API (default: False)
        """
        super().__init__(config)
        self.bot_token = self.config.get('bot_token') or os.environ.get('TELEGRAM_BOT_TOKEN')
        self.chat_id = self.config.get('chat_id') or os.environ.get('TELEGRAM_CHAT_ID')
        self.parse_mode = self.config.get('parse_mode', 'HTML')
        self.disable_notification = self.config.get('disable_notification', False)
        self.use_mcp = self.config.get('use_mcp', False)
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None

    async def initialize(self) -> bool:
        """
        Initialize Telegram adapter by validating configuration.

        Returns:
            bool: True if initialization successful
        """
        if not self.bot_token:
            print("Telegram bot token not provided")
            self._set_initialized(False)
            return False

        if not self.chat_id:
            print("Telegram chat ID not provided")
            self._set_initialized(False)
            return False

        # Test the connection by getting bot info
        try:
            if not self.use_mcp:
                response = requests.get(f"{self.base_url}/getMe", timeout=10)
                response.raise_for_status()
                bot_info = response.json()
                if not bot_info.get('ok'):
                    print('Telegram adapter initialization failed')
                    self._set_initialized(False)
                    return False
                print('Telegram adapter initialized')
            else:
                # TODO: Implement MCP initialization when needed
                print("Telegram adapter initialized (MCP mode - placeholder)")

            self._set_initialized(True)
            return True
        except Exception:
            print('Telegram adapter initialization failed')
            self._set_initialized(False)
            return False

    async def health_check(self) -> bool:
        """
        Check if Telegram bot is accessible.

        Returns:
            bool: True if healthy, False otherwise
        """
        if not self._is_initialized or not self.bot_token:
            return False

        try:
            if not self.use_mcp:
                response = requests.get(f"{self.base_url}/getMe", timeout=5)
                return response.status_code == 200 and response.json().get('ok', False)
            else:
                # TODO: Implement MCP health check
                return True
        except Exception:
            return False

    async def shutdown(self) -> bool:
        """
        Shutdown Telegram adapter.

        Returns:
            bool: True (no special cleanup needed)
        """
        self._set_initialized(False)
        return True

    async def send_notification(self, message: str,
                              priority: str = "normal",
                              metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Send a notification via Telegram.

        Args:
            message: Notification message
            priority: Priority level (low, normal, high, urgent)
            metadata: Additional metadata for the notification

        Returns:
            bool: True if notification sent successfully
        """
        if not self._is_initialized:
            print("Telegram adapter not initialized")
            return False

        try:
            # Prepare payload
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': self.parse_mode,
                'disable_notification': self.disable_notification
            }

            # Add priority information if needed
            if priority != "normal":
                priority_text = {
                    "low": "🔽 Low Priority",
                    "high": "🔼 High Priority",
                    "urgent": "🚨 URGENT"
                }.get(priority, "")
                if priority_text:
                    payload['text'] = f"{priority_text}\n\n{payload['text']}"

            # Send notification
            if not self.use_mcp:
                response = requests.post(
                    f"{self.base_url}/sendMessage",
                    json=payload,
                    timeout=10
                )
                response.raise_for_status()
                result = response.json()

                if not result.get('ok'):
                    print('Telegram notification was rejected')
                    return False

                print('Telegram notification sent')
                return True
            else:
                # TODO: Implement MCP message sending
                print("Telegram notification sent via MCP (placeholder)")
                return True

        except Exception:
            print('Telegram notification failed')
            return False

    async def send_schedule_update(self, events: List[Any]) -> bool:
        """
        Send a schedule update notification via Telegram.

        Args:
            events: List of schedule changes or updates

        Returns:
            bool: True if notification sent successfully
        """
        if not events:
            message = "📅 No schedule updates"
        elif len(events) == 1:
            event = events[0]
            message = f"📅 Schedule Update:\n{getattr(event, 'title', getattr(event, 'summary', 'Unknown Event'))}"
        else:
            message = f"📅 Schedule Update ({len(events)} events):\n"
            for i, event in enumerate(events[:5]):  # Limit to 5 events to avoid huge messages
                title = getattr(event, 'title', getattr(event, 'summary', f'Event {i+1}'))
                message += f"• {title}\n"
            if len(events) > 5:
                message += f"... and {len(events) - 5} more"

        return await self.send_notification(message, priority="normal")
