"""
Preparation Block Service
Contains domain logic for calculating and scheduling preparation blocks.
Depends on adapter interfaces for external operations.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from models import UniversityEvent, PreparationBlock


# Define the adapter interfaces that our service depends on
class CalendarAdapter(ABC):
    """Adapter interface for calendar operations."""

    @abstractmethod
    def _get_or_create_calendar(self, calendar_summary: str) -> str:
        """Get existing calendar by summary or create a new one. Returns calendar ID."""
        pass

    @abstractmethod
    def _get_events_in_range(self, time_min: str, time_max: str) -> List[Dict]:
        """Fetch events in the given time range. Returns list of event objects."""
        pass

    @abstractmethod
    def event_exists_by_uid(self, calendar_id: str, uid: str) -> Optional[str]:
        """Check if an event with given iCalUID exists."""
        pass

    @abstractmethod
    def _insert_event(self, event_data: Any) -> Optional[str]:
        """Insert a new event. Returns event ID if successful."""
        pass

    @abstractmethod
    def _delete_event(self, calendar_id: str, event_id: str) -> bool:
        """Delete an owned event by its Google event ID."""
        pass


class NotificationAdapter(ABC):
    """Adapter interface for sending notifications."""

    @abstractmethod
    def send_notification(self, message: str) -> None:
        """Send a notification."""
        pass


class AutomationAdapter(ABC):
    """Adapter interface for triggering automation workflows."""

    @abstractmethod
    def trigger_webhook(self, payload: Dict[str, Any]) -> None:
        """Trigger a webhook with payload."""
        pass


class PreparationBlockService:
    """
    Service for calculating and scheduling preparation blocks.
    Depends on injected adapters for external operations.
    """

    def __init__(self,
                 calendar_adapter: CalendarAdapter,
                 notification_adapter: Optional[NotificationAdapter] = None,
                 automation_adapter: Optional[AutomationAdapter] = None):
        """
        Initialize preparation block service.

        Args:
            calendar_adapter: Adapter for calendar operations
            notification_adapter: Adapter for sending notifications (optional)
            automation_adapter: Adapter for triggering automation workflows (optional)
        """
        self.calendar_adapter = calendar_adapter
        self.notification_adapter = notification_adapter
        self.automation_adapter = automation_adapter

    def parse_event_type(self, summary: str) -> Optional[str]:
        """
        Extract event type code from summary parentheses.
        Returns one of 'ЛК', 'ЛБ', 'ПР', or None.
        """
        import re
        # Look for pattern (XX) where XX are Cyrillic letters
        match = re.search(r'\(([А-Яа-я]+)\)', summary)
        if match:
            return match.group(1)
        return None

    def prep_duration_for_type(self, event_type: Optional[str], subject: Optional[str] = None) -> int:
        """
        Return preparation duration in minutes based on event type.
        Can be overridden by subject-specific rules.
        """
        # Base durations (minutes)
        base = {
            'ЛК': 25,   # lecture
            'ЛБ': 75,   # lab
            'ПР': 45,   # practical
        }
        duration = base.get(event_type, 30)  # default 30 min if unknown
        # Subject-specific overrides could be added here
        # For example, if subject in high_priority_subjects: duration += 15
        return duration

    def find_free_slot(self, busy_intervals: List[tuple], event_start: datetime,
                      duration: int, lead_time: timedelta) -> Optional[tuple]:
        """
        Find a free slot of length `duration` minutes before `event_start`,
        within the interval [event_start - lead_time, event_start].
        Busy intervals are list of (start, end) datetime objects.
        Returns (slot_start, slot_end) or None if no free slot.
        Prefers the latest possible start (closest to event_start).
        """
        search_start = event_start - lead_time
        search_end = event_start
        # We'll search backwards in steps of e.g., 5 minutes for simplicity
        step = timedelta(minutes=5)
        # Start from the latest possible start: event_start - duration
        candidate_end = event_start
        candidate_start = candidate_end - timedelta(minutes=duration)
        # Ensure candidate_start >= search_start
        if candidate_start < search_start:
            # Not enough lead time for the duration; we could reduce duration? but
            # we just return None.
            return None
        # Iterate backwards
        while candidate_start >= search_start:
            # Check if slot [candidate_start, candidate_end] conflicts with any
            # busy interval
            conflict = False
            for b_start, b_end in busy_intervals:
                # Overlap condition: not (candidate_end <= b_start or
                # candidate_start >= b_end)
                if not (candidate_end <= b_start or candidate_start >= b_end):
                    conflict = True
                    break
            if not conflict:
                return candidate_start, candidate_end
            # Move earlier by step
            candidate_end -= step
            candidate_start = candidate_end - timedelta(minutes=duration)
        return None

    def get_ai_suggestion(self, event_data: Dict[str, Any]) -> Optional[int]:
        """
        Get AI-suggested preparation duration in minutes by calling an n8n workflow webhook.
        Falls back to None if the request fails or no suggestion is returned.
        Expects the webhook to return JSON like {"suggested_prep_min": 30} or {"prep_duration_min": 30}.
        """
        if not self.automation_adapter:
            # No automation adapter configured, skip AI suggestion
            return None

        # This would be implemented by calling the automation adapter to trigger
        # an n8n workflow that returns a suggested duration
        # For now, we'll return None to use rule-based duration
        # In a full implementation, this would:
        # 1. Prepare payload with event data
        # 2. Call automation_adapter.trigger_webhook() or trigger_workflow()
        # 3. Parse the response to extract suggested duration
        return None

    def get_or_create_calendar(self, calendar_summary: str = 'University Schedule') -> str:
        """
        Get existing calendar by summary or create a new one.
        Returns calendar ID.
        """
        return self.calendar_adapter._get_or_create_calendar(calendar_summary)

    def get_existing_events(self, calendar_id: str, time_min: str, time_max: str) -> List[Dict]:
        """
        Fetch events in the given time range (ISO format strings with Z).
        Returns list of event objects.
        """
        return self.calendar_adapter._get_events_in_range(time_min, time_max)

    def event_exists_by_uid(self, calendar_id: str, uid: str) -> Optional[str]:
        """
        Check if an event with given iCalUID exists.
        """
        return self.calendar_adapter.event_exists_by_uid(calendar_id, uid)

    def delete_preparation_block_event(
        self, calendar_id: str, event_id: str
    ) -> bool:
        """Delete a preparation projection by its Google event ID."""
        return bool(self.calendar_adapter._delete_event(calendar_id, event_id))

    def update_preparation_block_event(
        self,
        calendar_id: str,
        event_id: str,
        event_data: Dict[str, Any],
    ) -> Optional[str]:
        """Update an existing preparation projection without creating a duplicate."""
        update_method = getattr(self.calendar_adapter, '_update_event', None)
        if not callable(update_method):
            return None
        return update_method(
            calendar_id,
            event_id,
            type('EventData', (), event_data)(),
        )

    def insert_preparation_block(self, calendar_id: str, event_data: Dict[str, Any],
                                is_prep: bool = True) -> Optional[str]:
        """
        Insert a preparation block event.
        """
        # Use the calendar adapter's insert_event method
        return self.calendar_adapter._insert_event(
            type('EventData', (), event_data)()
        )

    def send_webhook_notification(self, payload: Dict[str, Any]) -> None:
        """
        Send a JSON payload to the configured n8n webhook URL.
        """
        if self.automation_adapter:
            # Use automation adapter to trigger webhook
            self.automation_adapter.trigger_webhook(payload)
        else:
            # Fallback to direct implementation for backward compatibility
            import os
            import requests
            webhook_url = os.environ.get(
                'N8N_WEBHOOK_URL',
                'http://localhost:5678/webhook/test-webhook')
            try:
                response = requests.post(webhook_url, json=payload, timeout=5)
                response.raise_for_status()
                print(f"Webhook notification sent to {webhook_url}")
            except Exception as e:
                print(f"Failed to send webhook notification: {e}")

    def send_telegram_notification(self, message: str) -> None:
        """
        Send a notification via Telegram Bot API.
        """
        if self.notification_adapter:
            # Use notification adapter
            self.notification_adapter.send_notification(message)
        else:
            # Direct implementation (fallback)
            import os
            import requests
            token = os.environ.get('TELEGRAM_BOT_TOKEN')
            chat_id = os.environ.get('TELEGRAM_CHAT_ID')
            if not token or not chat_id:
                # Silently skip if not configured
                return
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            try:
                response = requests.post(url, json=payload, timeout=5)
                response.raise_for_status()
                print(f"Telegram notification sent to chat {chat_id}")
            except Exception as e:
                print(f"Failed to send Telegram notification: {e}")

    def sync_preparation_blocks(self, ics_file_path: str,
                               calendar_summary: str = 'University Schedule',
                               lead_time_hours: float = 2.0) -> None:
        """
        Main function: parse ICS, compute preparation blocks,
        insert into Google Calendar.
        lead_time_hours: maximum time before event to look for a free slot.
        """
        # Parse ICS
        try:
            from scripts.parse_ics import parse_ics
            events = parse_ics(ics_file_path)
        except ImportError:
            try:
                from parse_ics import parse_ics
                events = parse_ics(ics_file_path)
            except ImportError:
                print("Error: parse_ics module not available")
                return

        group_events = [ev for ev in events if ev['is_group_event']]
        print(f"Found {len(group_events)} events for group 8И41.")

        if not group_events:
            print("No events to process.")
            return

        # Get or create calendar
        calendar_id = self.get_or_create_calendar(calendar_summary)
        print(f"Using calendar ID: {calendar_id}")

        # Determine time range for fetching existing events:
        # we need to look at a window
        # that covers all events plus potential preparation blocks before them
        times = [ev['dtstart'] for ev in group_events] + [ev['dtend']
                                                          for ev in group_events]
        min_time = min(times)
        max_time = max(times)
        # We'll look back up to lead_time_hours + max preparation duration before
        # earliest event
        max_prep_duration = timedelta(hours=2)  # safe upper bound
        buffer_before = timedelta(
            hours=lead_time_hours) + max_prep_duration
        time_min = self._format_datetime(min_time - buffer_before)
        time_max = self._format_datetime(max_time)
        print(f"Fetching existing events from {time_min} to {time_max}...")
        existing_events = self.get_existing_events(calendar_id, time_min, time_max)
        print(f"Found {len(existing_events)} existing events in calendar.")

        # Build a set of busy intervals from existing events (to avoid overlapping)
        # We'll treat each existing event as busy.
        # We'll also consider preparation blocks we are about to insert (we'll add
        # them as we go).
        busy_intervals = []  # list of (start, end) as datetime objects
        for ev in existing_events:
            start_str = ev.get(
                'start', {}
            ).get('dateTime') or ev.get(
                'start', {}
            ).get('date')
            end_str = ev.get(
                'end', {}
            ).get('dateTime') or ev.get(
                'end', {}
            ).get('date')
            if start_str.endswith('Z'):
                start_str = start_str[:-1] + '+00:00'
            if end_str.endswith('Z'):
                end_str = end_str[:-1] + '+00:00'
            try:
                start = datetime.datetime.fromisoformat(start_str)
                end = datetime.datetime.fromisoformat(end_str)
                busy_intervals.append((start, end))
            except ValueError as e:
                msg = f"Could not parse event time: {start_str} - {end_str}: {e}"
                print(msg)
                continue

        # Sort events by start time
        group_events.sort(key=lambda x: x['dtstart'])

        # For each event, try to schedule preparation block just before it.
        for ev in group_events:
            uid = ev['uid']
            summary = ev['summary']
            dtstart = ev['dtstart']  # datetime object
            # Determine event type
            event_type = self.parse_event_type(summary)
            # Get AI-suggested preparation duration, fallback to rule-based
            ai_suggested_min = self.get_ai_suggestion({
                'summary': summary,
                'event_type': event_type,
                'dtstart': dtstart
            })
            prep_min = ai_suggested_min if ai_suggested_min is not None else self.prep_duration_for_type(
                event_type, None)
            # Preparation block should end at event start (could also subtract a small
            # travel buffer)
            prep_end = dtstart
            # Search for free slot within lead_time
            lead_time = timedelta(hours=lead_time_hours)
            slot = self.find_free_slot(busy_intervals, dtstart, prep_min, lead_time)
            if slot is None:
                print(
                    f"Could not find free slot for preparation block of {prep_min} min "
                    f"before {summary} within {lead_time_hours} hours. Skipping."
                )
                continue
            prep_start, prep_end = slot
            # Prepare event data
            prep_uid = f"prep-{uid}"
            # Check if preparation block already exists (by iCalUID)
            exists = self.event_exists_by_uid(calendar_id, prep_uid)
            if exists:
                print(
                    f"Preparation block already exists for {summary} "
                    f"(UID: {prep_uid}), skipping."
                )
                continue
            # Add to busy intervals (so subsequent preparation blocks avoid
            # overlap)
            busy_intervals.append((prep_start, prep_end))
            prep_event_data = {
                'uid': prep_uid,
                'summary': f"Подготовка: {summary}",
                'description': f"Подготовка к занятию: {summary}\n"
                f"Тип: {event_type or 'неопределен'}\n"
                f"Длительность подготовки: {prep_min} мин\n"
                f"Исходное событие UID: {uid}",
                'location': '',  # preparation location unspecified
                'dtstart': prep_start,
                'dtend': prep_end,
            }
            event_id = self.insert_preparation_block(
                calendar_id,
                prep_event_data,
                is_prep=True)
            if event_id:
                # Send webhook notification with details
                payload = {
                    "uid": prep_uid,
                    "summary": prep_event_data["summary"],
                    "description": prep_event_data["description"],
                    "start": prep_start.isoformat(),
                    "end": prep_end.isoformat(),
                    "event_type": event_type or "неопределен",
                    "prep_duration_min": prep_min
                }
                self.send_webhook_notification(payload)
                # Send Telegram notification
                tg_message = (
                    f"<b>Создан блок подготовки:</b> {prep_event_data['summary']}\n"
                    f"<b>Время:</b> {prep_start.strftime('%Y-%m-%d %H:%M')} - {prep_end.strftime('%Y-%m-%d %H:%M')}\n"
                    f"<b>Тип:</b> {event_type or 'неопределен'}\n"
                    f"<b>Длительность:</b> {prep_min} мин"
                )
                self.send_telegram_notification(tg_message)

    def _format_datetime(self, dt: datetime) -> str:
        """Return datetime as ISO 8601 string with Zulu time suffix."""
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


# Factory function for creating the service with default adapters
def create_preparation_block_service(config: Optional[Dict[str, Any]] = None) -> PreparationBlockService:
    """
    Factory function to create a PreparationBlockService with default adapters.

    Args:
        config: Configuration dictionary containing settings for adapters

    Returns:
        PreparationBlockService: Configured service instance
    """
    from adapters.google_calendar_adapter import GoogleCalendarAdapter
    from adapters.telegram_adapter import TelegramAdapter
    from adapters.n8n_adapter import N8NAdapter

    config = config or {}

    # Create adapters
    calendar_adapter = GoogleCalendarAdapter(config.get('calendar', {}))
    notification_adapter = TelegramAdapter(config.get('notification', {}))
    automation_adapter = N8NAdapter(config.get('automation', {}))

    # Create and return service
    return PreparationBlockService(
        calendar_adapter=calendar_adapter,
        notification_adapter=notification_adapter,
        automation_adapter=automation_adapter
    )
