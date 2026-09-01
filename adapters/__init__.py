"""
Infrastructure adapters package for Personal OS AI Calendar system.
Provides adapters for Google Calendar, Telegram, and n8n integration.
"""

from .base_adapter import BaseAdapter, CalendarAdapter, NotificationAdapter, AutomationAdapter
from .google_calendar_adapter import GoogleCalendarAdapter
from .telegram_adapter import TelegramAdapter
from .n8n_adapter import N8NAdapter

__all__ = [
    'BaseAdapter',
    'CalendarAdapter',
    'NotificationAdapter',
    'AutomationAdapter',
    'GoogleCalendarAdapter',
    'TelegramAdapter',
    'N8NAdapter'
]