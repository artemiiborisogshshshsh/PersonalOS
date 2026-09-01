"""
Personal Event Calendar Sync Service
Handles synchronization of personal university events to Google Calendar.
"""
import hashlib
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta, timezone
from adapters.base_adapter import CalendarAdapter
from models import PersonalUniversityEvent, PersonalEventState


class PersonalEventSyncService:
    """
    Service for syncing personal university events to Google Calendar.
    """

    def __init__(self, calendar_adapter: CalendarAdapter):
        """
        Initialize personal event sync service.

        Args:
            calendar_adapter: Adapter for calendar operations
        """
        self.calendar_adapter = calendar_adapter
        # Version 2 includes state/match metadata because those values alter
        # the projected summary and description.
        self.VERSION = 2

    def _compute_event_hash(self, personal_event: PersonalUniversityEvent) -> str:
        """
        Compute a hash of the stable attributes of the personal event that define the calendar event.
        Uses: personal_event.id (stable_id), title, description, start_time, end_time, location.
        Returns a hexadecimal hash string.
        """
        # We'll use a string that concatenates the stable attributes.
        # We need to ensure that the string representation is consistent.
        # For datetime, we use ISO format.
        hash_string = (
            f"{personal_event.id}|"
            f"{personal_event.title or ''}|"
            f"{personal_event.description or ''}|"
            f"{personal_event.start_time.isoformat() if personal_event.start_time else ''}|"
            f"{personal_event.end_time.isoformat() if personal_event.end_time else ''}|"
            f"{personal_event.location or ''}|"
            f"{personal_event.state.value}|"
            f"{personal_event.match_confidence.value if personal_event.match_confidence else ''}|"
            f"{personal_event.match_type.value if personal_event.match_type else ''}"
        )
        # Compute SHA256 hash and return as hex string
        return hashlib.sha256(hash_string.encode('utf-8')).hexdigest()

    def _get_hash_and_version_from_description(self, description: str) -> Tuple[Optional[str], Optional[int]]:
        """
        Extract hash and version from the event description.
        Expected format in description: ...\nХеш: <hash>\nВерсия: <version>\n...
        Returns (hash, version) or (None, None) if not found.
        """
        import re
        hash_match = re.search(r'(?:^|\n)Хеш: ([a-f0-9]{64})(?:\n|$)', description)
        version_match = re.search(r'(?:^|\n)Версия: (\d+)(?:\n|$)', description)
        hash_val = hash_match.group(1) if hash_match else None
        version_val = int(version_match.group(1)) if version_match else None
        return hash_val, version_val

    def _append_hash_and_version_to_description(self, description: str, personal_event: PersonalUniversityEvent) -> str:
        """
        Append the hash and version to the description.
        """
        event_hash = self._compute_event_hash(personal_event)
        return f"{description}\nХеш: {event_hash}\nВерсия: {self.VERSION}\n"

    def get_or_create_personal_calendar(self, calendar_summary: str = 'Personal OS Events') -> str:
        """
        Get existing personal events calendar or create a new one.
        Returns calendar ID.
        """
        return self.calendar_adapter._get_or_create_calendar(calendar_summary)

    def _format_datetime(self, dt: datetime) -> str:
        """Return datetime as ISO 8601 string with Zulu time suffix."""
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    def _find_event_by_summary_and_start(self, calendar_id: str, summary: str, start_time: datetime) -> Optional[Dict[str, Any]]:
        """
        Find an existing event by summary and start time within a small time window.
        Returns the event object if found, otherwise None.
        """
        # Define a time window around the start time (e.g., +/- 5 minutes)
        window = timedelta(minutes=5)
        time_min = self._format_datetime(start_time - window)
        time_max = self._format_datetime(start_time + window)
        try:
            events = self.calendar_adapter._get_events_in_range(time_min, time_max)
            for event in events:
                event_summary = event.get('summary', '')
                event_start_str = event.get('start', {}).get('dateTime')
                if event_summary and event_start_str:
                    # Normalize summary for comparison (extra spaces)
                    normalized_event_summary = ' '.join(event_summary.split())
                    normalized_summary = ' '.join(summary.split())
                    if normalized_event_summary == normalized_summary:
                        # Parse the event start time
                        try:
                            # Handle Zulu time or timezone offset
                            if event_start_str.endswith('Z'):
                                event_start_str = event_start_str[:-1] + '+00:00'
                            event_start = datetime.fromisoformat(event_start_str)
                            # Ensure both datetimes are timezone-aware or timezone-naive for subtraction
                            if event_start.tzinfo is not None and start_time.tzinfo is None:
                                # Convert start_time to UTC to match event_start
                                start_time = start_time.replace(tzinfo=timezone.utc)
                            elif event_start.tzinfo is None and start_time.tzinfo is not None:
                                # Convert event_start to match start_time's timezone
                                event_start = event_start.astimezone(start_time.tzinfo)
                            # Compare if start times are within 1 minute
                            if abs((event_start - start_time).total_seconds()) < 60:
                                return event
                        except ValueError:
                            # If we can't parse, skip this event
                            continue
            return None
        except Exception as e:
            print(f"Error searching for event by summary and start time: {e}")
            return None

    def sync_personal_event_to_calendar(self, personal_event: PersonalUniversityEvent,
                                      calendar_id: str) -> Optional[str]:
        """
        Sync a single personal event to Google Calendar.
        Only syncs events with state CONFIRMED or MOVED. The reconciled state
        is authoritative; MOVED is normally produced by a MEDIUM-confidence
        partial-time match and must still be projected.
        For state CANCELLED, deletes the corresponding calendar event if exists.
        For low/medium confidence events, skips automatic sync and logs a warning.
        Uses hash of stable attributes to avoid unnecessary updates.

        Args:
            personal_event: The personal university event to sync
            calendar_id: The calendar ID to sync to

        Returns:
            Optional[str]: The event ID in Google Calendar if synced, None if skipped or failed
        """
        # Handle cancelled events: delete the calendar event if exists
        if personal_event.state == PersonalEventState.CANCELLED:
            existing_event_id = self.calendar_adapter.event_exists_by_uid(calendar_id, personal_event.id)
            if existing_event_id:
                self.calendar_adapter._delete_event(calendar_id, existing_event_id)
                print(f"Deleted calendar event for cancelled personal event: {personal_event.title}")
            return None

        # Only reconciled attendance states are projected. Confidence is
        # diagnostic metadata, not a second authorization gate.
        if personal_event.state not in (PersonalEventState.CONFIRMED, PersonalEventState.MOVED):
            return None

        # Prepare event data for Google Calendar
        summary = personal_event.title
        description = personal_event.description or ''
        location = personal_event.location or ''

        # Add move notation if event was moved
        if personal_event.state == PersonalEventState.MOVED:
            summary += " (перенесено)"
            description += "\nПримечание: событие было перенесено."

        # Add attendance rule info and stable ID to description
        description += f"\nСтабильный ID личного события: {personal_event.id}"
        if personal_event.match_confidence:
            description += f"\nУверенность совпадения: {personal_event.match_confidence.value}"
        if personal_event.match_type:
            description += f"\nТип совпадения: {personal_event.match_type.value}"
        # We could add more metadata from personal_event.metadata if needed

        # Append hash and version to description
        description = self._append_hash_and_version_to_description(description, personal_event)

        event_data = {
            'uid': personal_event.id,  # Use personal event ID as iCalUID
            'summary': summary,
            'description': description,
            'location': location,
            'dtstart': personal_event.start_time,
            'dtend': personal_event.end_time,
        }

        # Check if event already exists by iCalUID
        existing_event_id = self.calendar_adapter.event_exists_by_uid(calendar_id, personal_event.id)
        if existing_event_id:
            # Fetch the existing event to extract its hash and version
            try:
                # We need to get the existing event object to read its description.
                # Since our adapter doesn't have a method to get a single event by ID, we can fetch events in a small time range around the event's start time.
                # But note: we don't have the event's time in the existing event? We do have the personal_event's time, but the existing event might have been moved?
                # However, we are using the personal_event's iCalUID to find it, so we assume the existing event has the same UID and we can get it by UID?
                # Our adapter has event_exists_by_uid but not get_event_by_uid.
                # We'll fetch events in a wide time range and look for the one with the matching UID.
                # This is inefficient but acceptable for now.
                # We'll use a time range that covers the personal_event's time (with a buffer) to find the event.
                time_min = self._format_datetime(personal_event.start_time - timedelta(days=1))
                time_max = self._format_datetime(personal_event.end_time + timedelta(days=1))
                events = self.calendar_adapter._get_events_in_range(time_min, time_max)
                existing_event = None
                for e in events:
                    if e.get('iCalUID') == personal_event.id:
                        existing_event = e
                        break
                if existing_event:
                    existing_description = existing_event.get('description', '')
                    old_hash, old_version = self._get_hash_and_version_from_description(existing_description)
                    new_hash = self._compute_event_hash(personal_event)
                    # If the hash matches and the version is the same (or we ignore version for now), we skip update.
                    if old_hash == new_hash and old_version == self.VERSION:
                        print(f"Hash matches for event '{personal_event.title}', skipping update.")
                        return existing_event.get('id')  # Return the existing event ID
                    # If hash doesn't match, we proceed to update.
                else:
                    # Could not find the existing event to check hash, so we update.
                    print(f"Could not fetch existing event for UID {personal_event.id}, proceeding with update.")
            except Exception as e:
                print(f"Error checking existing event hash: {e}. Proceeding with update.")

            # Update in place. A delete-then-insert sequence can lose the
            # calendar projection when insertion fails and is not atomic.
            return self.calendar_adapter._update_event(
                calendar_id,
                existing_event_id,
                type('EventData', (), event_data)(),
            )
        else:
            # Check for duplicate by summary and start time to avoid duplicates
            duplicate_event = self._find_event_by_summary_and_start(
                calendar_id,
                summary,  # use the potentially modified summary
                personal_event.start_time
            )
            if duplicate_event:
                old_hash, old_version = self._get_hash_and_version_from_description(
                    duplicate_event.get('description', '')
                )
                new_hash = self._compute_event_hash(personal_event)
                duplicate_event_id = duplicate_event.get('id')
                if (
                    duplicate_event_id
                    and old_hash == new_hash
                    and old_version == self.VERSION
                ):
                    # This is our projection with a stale iCalUID. Repair it
                    # in place so a failed create cannot leave two events.
                    return self.calendar_adapter._update_event(
                        calendar_id,
                        duplicate_event_id,
                        type('EventData', (), event_data)(),
                    )
                print(
                    f"Conflict for personal event '{personal_event.title}': "
                    "same time/title is owned by another calendar event."
                )
                return None
            else:
                # No duplicate found, insert new event
                event_id = self.calendar_adapter._insert_event(
                    type('EventData', (), event_data)()
                )
                return event_id

    def sync_personal_events_to_calendar(self, personal_events: List[PersonalUniversityEvent],
                                       calendar_summary: str = 'Personal OS Events') -> None:
        """
        Sync a list of personal university events to Google Calendar.

        Args:
            personal_events: List of personal university events to sync
            calendar_summary: Summary/name of the target calendar
        """
        # Get or create personal events calendar
        calendar_id = self.get_or_create_personal_calendar(calendar_summary)
        print(f"Using personal events calendar ID: {calendar_id}")

        # Sync each event
        for event in personal_events:
            event_id = self.sync_personal_event_to_calendar(event, calendar_id)
            if event_id:
                print(f"Synced personal event to calendar: {event.title} (ID: {event_id})")
            else:
                print(f"Skipped syncing personal event (not matched): {event.title}")


# Factory function for creating the service with default adapter
def create_personal_event_sync_service(config: Optional[Dict[str, Any]] = None) -> PersonalEventSyncService:
    """
    Factory function to create a PersonalEventSyncService with default adapter.

    Args:
        config: Configuration dictionary containing settings for adapter

    Returns:
        PersonalEventSyncService: Configured service instance
    """
    from adapters.google_calendar_adapter import GoogleCalendarAdapter

    config = config or {}

    # Create adapter
    calendar_adapter = GoogleCalendarAdapter(config.get('calendar', {}))

    # Create and return service
    return PersonalEventSyncService(calendar_adapter=calendar_adapter)
