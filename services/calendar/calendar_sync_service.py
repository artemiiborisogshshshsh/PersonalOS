"""
Calendar Sync Service
Handles synchronization of ICS files to Google Calendar.
Depends on adapter interfaces for external operations.
"""
from typing import List, Dict, Any, Optional
import datetime
import hashlib
from adapters.base_adapter import CalendarAdapter


class CalendarSyncService:
    """
    Service for syncing ICS files to Google Calendar.
    Depends on injected calendar adapter for external operations.
    """

    def __init__(self, calendar_adapter: CalendarAdapter):
        """
        Initialize calendar sync service.

        Args:
            calendar_adapter: Adapter for calendar operations
        """
        self.calendar_adapter = calendar_adapter
        # Version of the hash computation algorithm. Increment if you change the hash formula.
        self.VERSION = 1

        # Color mapping for event types (Google Calendar color IDs)
        self.COLOR_MAP = {
            'ЛК': '1',  # blue
            'ЛБ': '2',  # green
            'ПР': '4',  # yellow
            # fallback for unknown types
            'default': '11'  # red
        }

    def _compute_event_hash(self, event_data: Dict[str, Any]) -> str:
        """
        Compute a hash of the stable attributes of the event data that define the event.
        Uses: summary, description, location, dtstart, dtend, event_type,
        source status and sequence.
        Returns a hexadecimal hash string.
        """
        # We'll use a string that concatenates the stable attributes.
        # We need to ensure that the string representation is consistent.
        # For datetime, we use ISO format.
        hash_string = (
            f"{event_data.get('summary', '')}|"
            f"{event_data.get('description', '')}|"
            f"{event_data.get('location', '')}|"
            f"{event_data.get('dtstart').isoformat() if event_data.get('dtstart') else ''}|"
            f"{event_data.get('dtend').isoformat() if event_data.get('dtend') else ''}|"
            f"{event_data.get('event_type', '') if event_data.get('event_type') else ''}|"
            f"{event_data.get('status', 'confirmed')}|"
            f"{event_data.get('sequence', 0)}"
        )
        # Compute SHA256 hash and return as hex string
        return hashlib.sha256(hash_string.encode('utf-8')).hexdigest()

    def _get_hash_and_version_from_description(self, description: str) -> tuple[Optional[str], Optional[int]]:
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

    def _append_hash_and_version_to_description(self, description: str, event_data: Dict[str, Any]) -> str:
        """
        Append the hash and version to the description.
        """
        event_hash = self._compute_event_hash(event_data)
        return f"{description}\nХеш: {event_hash}\nВерсия: {self.VERSION}\n"

    def _format_datetime(self, dt: datetime.datetime) -> str:
        """Return datetime as ISO 8601 string with Zulu time suffix."""
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    def _parse_datetime(self, dt_str: str) -> datetime.datetime:
        """Parse ISO 8601 string to datetime object.
        Handles strings ending with Z or timezone offset.
        """
        if dt_str.endswith('Z'):
            dt_str = dt_str[:-1] + '+00:00'
        return datetime.datetime.fromisoformat(dt_str)

    def get_or_create_calendar(self, calendar_summary: str = 'University Schedule') -> str:
        """
        Get existing calendar by summary or create a new one.
        Returns calendar ID.
        """
        return self.calendar_adapter._get_or_create_calendar(calendar_summary)

    def get_events_in_range(self, calendar_id: str, time_min: str, time_max: str) -> List[Dict]:
        """
        Fetch events in the given time range (ISO format strings with Z).
        Returns list of event objects.
        """
        return self.calendar_adapter._get_events_in_range(time_min, time_max)

    def insert_event(self, calendar_id: str, event_data: Dict[str, Any],
                    event_type: Optional[str] = None) -> Optional[str]:
        """
        Insert a new event into the calendar.
        event_data dict should contain: uid, summary, description, location,
        dtstart, dtend.
        dtstart and dtend are datetime objects (assumed UTC).
        event_type: optional string like 'ЛК', 'ЛБ', 'ПР' to set colorId.
        """
        adapter_event = {
            'uid': event_data['uid'],
            'summary': event_data['summary'],
            'location': event_data.get('location', ''),
            'description': event_data.get('description', ''),
            'dtstart': event_data['dtstart'],
            'dtend': event_data['dtend'],
            'event_type': event_type,
        }
        return self.calendar_adapter._insert_event(
            type('EventData', (), adapter_event)()
        )

    def update_event(
        self,
        calendar_id: str,
        event_id: str,
        event_data: Dict[str, Any],
        event_type: Optional[str] = None,
    ) -> Optional[str]:
        """Update an owned projection without a destructive delete/create gap."""
        adapter_event = {
            'uid': event_data['uid'],
            'summary': event_data['summary'],
            'location': event_data.get('location', ''),
            'description': event_data.get('description', ''),
            'dtstart': event_data['dtstart'],
            'dtend': event_data['dtend'],
            'event_type': event_type,
        }
        return self.calendar_adapter._update_event(
            calendar_id,
            event_id,
            type('EventData', (), adapter_event)(),
        )

    def sync_ics_to_gcalendar(self, ics_file_path: str,
                             calendar_summary: str = 'University Schedule') -> None:
        """
        Main sync function: parse ICS, authenticate, and sync events
        to Google Calendar.
        Uses versioning, hashing and idempotency to prevent duplicates
        and handle updates properly.
        Skips events not belonging to group 8И41 (based on description).
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

        # Filter to group 8И41 events
        group_events = [ev for ev in events if ev['is_group_event']]
        msg_part1 = f"Found {len(group_events)} events for group 8И41"
        msg = f"{msg_part1} out of {len(events)} total."
        print(msg)

        if not group_events:
            print("No group events to sync.")
            return

        # Get or create target calendar
        calendar_id = self.get_or_create_calendar(calendar_summary)
        print(f"Using calendar ID: {calendar_id}")

        # Determine time range for fetching existing events:
        # we need to look at a window that covers all events plus a buffer.
        times = [ev['dtstart'] for ev in group_events] + [ev['dtend']
                                                          for ev in group_events]
        min_time = min(times)
        max_time = max(times)
        # Add a buffer of 1 day on each side to be safe.
        buffer_before = datetime.timedelta(days=1)
        buffer_after = datetime.timedelta(days=1)
        time_min = self._format_datetime(min_time - buffer_before)
        time_max = self._format_datetime(max_time + buffer_after)
        print(f"Fetching existing events from {time_min} to {time_max}...")
        existing_events = self.get_events_in_range(
            calendar_id, time_min, time_max)
        print(f"Found {len(existing_events)} existing events in calendar.")

        # Build maps for efficient lookup
        # Map by UID for direct lookup
        existing_by_uid = {}
        # Map by (summary_normalized, start, end) for fallback lookup
        existing_by_summary_time = {}

        for ev in existing_events:
            # Extract UID
            uid = ev.get('iCalUID')
            if uid:
                existing_by_uid[uid] = ev

            # Extract summary and time for summary+time deduplication
            summary = ev.get('summary', '')
            summary_normalized = ' '.join(summary.split())
            start_str = ev.get('start', {}).get('dateTime')
            end_str = ev.get('end', {}).get('dateTime')
            if start_str and end_str:
                try:
                    start_dt = self._parse_datetime(start_str)
                    end_dt = self._parse_datetime(end_str)
                    key = (summary_normalized, start_dt, end_dt)
                    existing_by_summary_time[key] = ev
                except Exception as e:
                    print(f"Warning: Could not parse event time for dedup: {e}")
                    continue

        # Sync each event
        for ev in group_events:
            uid = ev['uid']
            summary_normalized = ev['summary_normalized']
            dtstart = ev['dtstart']
            dtend = ev['dtend']

            # First, try to find existing event by UID
            existing_event = existing_by_uid.get(uid)
            if not existing_event:
                # A moved event may be outside the window around the new
                # snapshot. A global UID lookup prevents creating a second
                # projection while the old one remains elsewhere.
                event_exists_by_uid = getattr(
                    self.calendar_adapter, 'event_exists_by_uid', None
                )
                if callable(event_exists_by_uid):
                    existing_id = event_exists_by_uid(calendar_id, uid)
                    if existing_id:
                        existing_event = {
                            'id': existing_id,
                            'iCalUID': uid,
                            'description': '',
                        }

            if ev.get('status', 'confirmed').lower() == 'cancelled':
                if existing_event and existing_event.get('id'):
                    self.calendar_adapter._delete_event(calendar_id, existing_event['id'])
                    print(f"Deleted explicitly cancelled event: {ev['summary']}")
                continue

            if existing_event:
                # Check if the event data has changed using hash
                try:
                    existing_description = existing_event.get('description', '')
                    old_hash, old_version = self._get_hash_and_version_from_description(existing_description)
                    new_hash = self._compute_event_hash(ev)

                    # If the hash matches and the version is the same or we ignore version, we skip update.
                    if old_hash == new_hash and old_version == self.VERSION:
                        print(f"Event unchanged (UID: {uid}), skipping.")
                        continue
                    # If hash doesn't match, we need to update.
                    else:
                        print(f"Event changed (UID: {uid}), updating.")
                        existing_id = existing_event.get('id')
                        if existing_id:
                            event_to_update = dict(ev)
                            event_to_update['description'] = self._append_hash_and_version_to_description(
                                ev.get('description', ''), ev
                            )
                            self.update_event(
                                calendar_id,
                                existing_id,
                                event_to_update,
                                ev.get('event_type'),
                            )
                        else:
                            print(
                                f"Conflict: existing event for UID {uid} has no Google event ID."
                            )
                        continue
                except Exception as e:
                    print(
                        f"Error checking existing event hash: {e}. "
                        "Skipping to avoid a duplicate."
                    )
                    continue

            # If not found by UID, try to find by summary+time to handle potential UID changes
            key = (summary_normalized, dtstart, dtend)
            existing_event = existing_by_summary_time.get(key)

            if existing_event:
                # Found by summary+time, check if it's the same event with potentially changed UID
                try:
                    existing_description = existing_event.get('description', '')
                    old_hash, old_version = self._get_hash_and_version_from_description(existing_description)
                    new_hash = self._compute_event_hash(ev)

                    # If the hash matches, it's the same owned event with a
                    # stale source UID. Repair it in place.
                    if old_hash == new_hash and old_version == self.VERSION:
                        print(f"Event found by summary+time with matching hash, updating UID.")
                        existing_id = existing_event.get('id')
                        if not existing_id:
                            print("Conflict: matching event has no Google event ID.")
                            continue
                        event_to_update = dict(ev)
                        event_to_update['description'] = self._append_hash_and_version_to_description(
                            ev.get('description', ''), ev
                        )
                        self.update_event(
                            calendar_id,
                            existing_id,
                            event_to_update,
                            ev.get('event_type'),
                        )
                        continue
                    # If hash doesn't match, it's a different event - we have a conflict
                    else:
                        print(f"Conflict: different event with same summary+time found. Skipping to avoid overwriting.")
                        continue
                except Exception as e:
                    print(f"Error checking existing event hash for summary+time match: {e}. Proceeding with caution.")
                    # In case of error, we'll treat as potential conflict and skip to be safe
                    continue

            # No existing event found by UID or summary+time.
            print(f"Inserting new event: {ev['summary']}")
            event_to_insert = dict(ev)
            event_to_insert['description'] = self._append_hash_and_version_to_description(
                ev.get('description', ''), ev
            )
            self.insert_event(calendar_id, event_to_insert, ev.get('event_type'))


def create_calendar_sync_service(
    config: Optional[Dict[str, Any]] = None,
) -> CalendarSyncService:
    """Create CalendarSyncService with the configured Google adapter."""
    from adapters.google_calendar_adapter import GoogleCalendarAdapter

    config = config or {}
    return CalendarSyncService(
        calendar_adapter=GoogleCalendarAdapter(config.get('calendar', {}))
    )


# ============================================================================
#  Use Case Demonstrations
# ============================================================================


def demonstrate_calendar_sync_service():
    """Demonstrate use cases for the calendar sync service."""
    print("=== Calendar Sync Service Use Cases ===\n")

    # Note: This is a demonstration of the service structure.
    # Actual usage requires proper adapter configuration and credentials.

    # Initialize service (would need real adapter in practice)
    # service = CalendarSyncService(calendar_adapter=some_adapter)

    # Use Case 1: Basic ICS to Google Calendar synchronization
    print("Use Case 1: ICS to Google Calendar Synchronization")
    print("  - Parses ICS file containing university events")
    print("  - Filters to group 8И41 events")
    print("  - Synchronizes events to Google Calendar")
    print("  - Uses versioning and hashing for idempotent operations")
    print("  - Prevents duplicate creation on repeated syncs")
    print()

    # Use Case 2: Change detection and updates
    print("Use Case 2: Change Detection and Updates")
    print("  - Detects when event data has changed using hash comparison")
    print("  - Updates existing calendar events when source data changes")
    print("  - Preserves existing events when data is unchanged")
    print()

    # Use Case 3: Handling UID changes in source
    print("Use Case 3: Handling Source UID Changes")
    print("  - Detects when event UID changes but event content is same")
    print("  - Updates calendar event with new UID while preserving content")
    print("  - Treats as update rather than delete+create when appropriate")
    print()

    print("✓ Calendar sync service use cases demonstrated successfully!")


if __name__ == "__main__":
    demonstrate_calendar_sync_service()
