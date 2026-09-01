"""
Base adapter class for infrastructure adapters in Personal OS AI Calendar system.
Defines the common interface that all adapters should implement.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from datetime import datetime


class BaseAdapter(ABC):
    """Base class for all infrastructure adapters."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the adapter with configuration.

        Args:
            config: Configuration dictionary for the adapter
        """
        self.config = config or {}
        self._is_initialized = False

    @abstractmethod
    async def initialize(self) -> bool:
        """
        Initialize the adapter and establish connections.

        Returns:
            bool: True if initialization successful, False otherwise
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the adapter is healthy and connected.

        Returns:
            bool: True if healthy, False otherwise
        """
        pass

    @abstractmethod
    async def shutdown(self) -> bool:
        """
        Shutdown the adapter and cleanup resources.

        Returns:
            bool: True if shutdown successful, False otherwise
        """
        pass

    @property
    def is_initialized(self) -> bool:
        """Check if adapter is initialized."""
        return self._is_initialized

    def _set_initialized(self, value: bool) -> None:
        """Set initialization status."""
        self._is_initialized = value


class CalendarAdapter(BaseAdapter):
    """
    Adapter for calendar services (Google Calendar, etc.).
    Handles synchronization of events between the planning system and calendar services.
    """

    @abstractmethod
    async def sync_events_to_calendar(self, events: List[Any]) -> Dict[str, Any]:
        """
        Sync events from planning system to calendar service.

        Args:
            events: List of events to sync

        Returns:
            Dict with sync results (success count, errors, etc.)
        """
        pass

    @abstractmethod
    async def sync_events_from_calendar(self, start_time: datetime, end_time: datetime) -> List[Any]:
        """
        Sync events from calendar service to planning system.

        Args:
            start_time: Start of time range to sync
            end_time: End of time range to sync

        Returns:
            List of events from calendar service
        """
        pass

    @abstractmethod
    async def delete_event(self, event_id: str) -> bool:
        """
        Delete an event from the calendar service.

        Args:
            event_id: ID of event to delete

        Returns:
            bool: True if deletion successful
        """
        pass


class NotificationAdapter(BaseAdapter):
    """
    Adapter for notification services (Telegram, email, SMS, etc.).
    Handles sending notifications and alerts.
    """

    @abstractmethod
    async def send_notification(self, message: str,
                              priority: str = "normal",
                              metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Send a notification.

        Args:
            message: Notification message
            priority: Priority level (low, normal, high, urgent)
            metadata: Additional metadata for the notification

        Returns:
            bool: True if notification sent successfully
        """
        pass

    @abstractmethod
    async def send_schedule_update(self, events: List[Any]) -> bool:
        """
        Send a schedule update notification.

        Args:
            events: List of schedule changes or updates

        Returns:
            bool: True if notification sent successfully
        """
        pass


class AutomationAdapter(BaseAdapter):
    """
    Adapter for automation services (n8n, Zapier, etc.).
    Handles triggering workflows and exchanging data with automation platforms.
    """

    @abstractmethod
    async def trigger_workflow(self, workflow_name: str,
                             payload: Optional[Dict[str, Any]] = None) -> bool:
        """
        Trigger an automation workflow.

        Args:
            workflow_name: Name of workflow to trigger
            payload: Data to pass to the workflow

        Returns:
            bool: True if workflow triggered successfully
        """
        pass

    @abstractmethod
    async def register_webhook(self, webhook_url: str,
                             event_types: List[str]) -> bool:
        """
        Register a webhook for receiving events from the automation service.

        Args:
            webhook_url: URL to register as webhook
            event_types: List of event types to listen for

        Returns:
            bool: True if registration successful
        """
        pass