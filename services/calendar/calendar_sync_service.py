"""
Calendar Sync Service
Handles synchronization of ICS files to Google Calendar.
Depends on adapter interfaces for external operations.
"""
from typing import List, Dict, Any, Optional
import datetime
import hashlib
from adapters.base_adapter import CalendarAdapter
from services.calendar.projection_state import (
    CalendarProjectionState, CalendarProjectionError, delete_owned_verified,
)
from services.sync_retry import transient_error


class CalendarSyncService:
    """
    Service for syncing ICS files to Google Calendar.
    Depends on injected calendar adapter for external operations.
    """

    def __init__(self, calendar_adapter: CalendarAdapter,
                 projection_state: Optional[CalendarProjectionState] = None):
        """
        Initialize calendar sync service.

        Args:
            calendar_adapter: Adapter for calendar operations
        """
        self.calendar_adapter = calendar_adapter
        self.projection_state = projection_state or CalendarProjectionState()
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

    def _owned(self, event: dict, uid: str) -> bool:
        private = event.get('extendedProperties', {}).get('private', {})
        if private.get('personal_os_block_id') == uid:
            return True
        _, version = self._get_hash_and_version_from_description(
            str(event.get('description', '')))
        return event.get('iCalUID') == uid and version == self.VERSION

    @staticmethod
    def _same_time(event: dict, data: dict) -> bool:
        try:
            start = datetime.datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
            end = datetime.datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
            expected_start, expected_end = data['dtstart'], data['dtend']
            if start.tzinfo is not None and expected_start.tzinfo is not None:
                return start == expected_start and end == expected_end
            return (start.replace(tzinfo=None) == expected_start.replace(tzinfo=None)
                    and end.replace(tzinfo=None) == expected_end.replace(tzinfo=None))
        except (KeyError, TypeError, ValueError):
            return False

    def _write_verified(self, calendar_id: str, data: dict,
                        action: str, event_id: Optional[str]) -> str:
        uid = data['uid']
        reader = getattr(self.calendar_adapter, 'get_event_by_uid', None)
        if callable(reader):
            try:
                current = reader(calendar_id, uid, strict=True)
            except Exception:
                raise CalendarProjectionError('Calendar: чтение не подтверждено.') from None
            if isinstance(current, dict):
                if not self._owned(current, uid):
                    raise CalendarProjectionError('Calendar: владелец события не подтверждён.')
                if action == 'insert':
                    if (current.get('summary') == data['summary']
                            and current.get('description') == data['description']
                            and self._same_time(current, data)):
                        self.projection_state.put(uid, event_id=current['id'],
                                                  start=data['dtstart'].isoformat(),
                                                  end=data['dtend'].isoformat())
                        return current['id']
                    raise CalendarProjectionError('Calendar: существующая запись требует проверки.')
                if current.get('id') != event_id:
                    raise CalendarProjectionError('Calendar: прежняя запись не подтверждена.')
            elif current is None and action == 'update':
                raise CalendarProjectionError('Calendar: прежняя запись не подтверждена.')
        self.projection_state.put(uid, pending=action)
        event = type('EventData', (), {
            **data, 'system_block_id': uid,
            'system_source_event_id': uid,
            'system_operation_id': 'ics-source',
        })()
        for attempt in range(3):
            try:
                if action == 'insert':
                    result = self.calendar_adapter._insert_event(
                        event, calendar_id, strict=True)
                else:
                    result = self.calendar_adapter._update_event(
                        calendar_id, event_id, event, strict=True)
                if result:
                    self.projection_state.put(uid, pending=None, event_id=result,
                                              start=data['dtstart'].isoformat(),
                                              end=data['dtend'].isoformat())
                    return result
                error = None
            except Exception as exc:
                error = exc
            reader = getattr(self.calendar_adapter, 'get_event_by_uid', None)
            try:
                remote = reader(calendar_id, uid, strict=True) if callable(reader) else None
            except Exception:
                raise CalendarProjectionError('Calendar: чтение не подтверждено.') from None
            if (isinstance(remote, dict) and self._owned(remote, uid)
                    and remote.get('summary') == data['summary']
                    and remote.get('description') == data['description']
                    and self._same_time(remote, data)):
                self.projection_state.put(uid, pending=None, event_id=remote['id'],
                                          start=data['dtstart'].isoformat(),
                                          end=data['dtend'].isoformat())
                return remote['id']
            if error is None or not transient_error(error) or attempt == 2:
                raise CalendarProjectionError('Calendar: запись не подтверждена.', error) from None
        raise CalendarProjectionError('Calendar: запись не подтверждена.')

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
        scoped = getattr(self.calendar_adapter, 'list_events_in_calendar', None)
        if callable(scoped):
            events = scoped(calendar_id, self._parse_datetime(time_min),
                            self._parse_datetime(time_max))
            if isinstance(events, list):
                return events
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
        if self._get_hash_and_version_from_description(adapter_event['description'])[1] is None:
            adapter_event['description'] = self._append_hash_and_version_to_description(
                adapter_event['description'], event_data)
        return self._write_verified(calendar_id, adapter_event, 'insert', None)

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
        if self._get_hash_and_version_from_description(adapter_event['description'])[1] is None:
            adapter_event['description'] = self._append_hash_and_version_to_description(
                adapter_event['description'], event_data)
        return self._write_verified(calendar_id, adapter_event, 'update', event_id)

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
                    print('Calendar: skipped event with invalid time.')
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
                reader = getattr(self.calendar_adapter, 'get_event_by_uid', None)
                if callable(reader):
                    found = reader(calendar_id, uid, strict=True)
                    if isinstance(found, dict):
                        existing_event = found
                if not existing_event and not callable(reader):
                    lookup = getattr(self.calendar_adapter, 'event_exists_by_uid', None)
                    if callable(lookup):
                        existing_id = lookup(calendar_id, uid)
                        if existing_id:
                            existing_event = {'id': existing_id, 'iCalUID': uid,
                                              'description': ''}

            if ev.get('status', 'confirmed').lower() == 'cancelled':
                if existing_event and existing_event.get('id') and self._owned(existing_event, uid):
                    checkpoint = self.projection_state.get(uid)
                    manual_move = checkpoint.get('override') == 'moved'
                    if checkpoint.get('start') and checkpoint.get('end'):
                        baseline = {'dtstart': datetime.datetime.fromisoformat(checkpoint['start']),
                                    'dtend': datetime.datetime.fromisoformat(checkpoint['end'])}
                        manual_move = manual_move or not self._same_time(existing_event, baseline)
                    if manual_move:
                        self.projection_state.put(uid, override='moved')
                    else:
                        self.projection_state.put(uid, pending='delete')
                        delete_owned_verified(
                            self.calendar_adapter, calendar_id, existing_event['id'],
                            lambda value: self._owned(value, uid))
                        self.projection_state.put(uid, pending=None, override='cancelled')
                continue

            if existing_event:
                if not self._owned(existing_event, uid):
                    continue
                checkpoint = self.projection_state.get(uid)
                if checkpoint.get('override'):
                    continue
                if checkpoint.get('start') and checkpoint.get('end'):
                    baseline = {'dtstart': datetime.datetime.fromisoformat(checkpoint['start']),
                                'dtend': datetime.datetime.fromisoformat(checkpoint['end'])}
                    if not self._same_time(existing_event, baseline):
                        self.projection_state.put(uid, override='moved')
                        continue
                # Check if the event data has changed using hash
                try:
                    existing_description = existing_event.get('description', '')
                    old_hash, old_version = self._get_hash_and_version_from_description(existing_description)
                    new_hash = self._compute_event_hash(ev)

                    # If the hash matches and the version is the same or we ignore version, we skip update.
                    if old_hash == new_hash and old_version == self.VERSION:
                        self.projection_state.put(uid, event_id=existing_event.get('id'),
                                                  start=dtstart.isoformat(), end=dtend.isoformat())
                        continue
                    # If hash doesn't match, we need to update.
                    else:
                        existing_id = existing_event.get('id')
                        if existing_id:
                            event_to_update = dict(ev)
                            event_to_update['description'] = self._append_hash_and_version_to_description(
                                ev.get('description', ''), ev
                            )
                            self._write_verified(calendar_id, event_to_update,
                                                 'update', existing_id)
                        else:
                            print('Calendar: owned event has no provider ID.')
                        continue
                except CalendarProjectionError:
                    raise
                except Exception:
                    print('Calendar: existing event could not be verified.')
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
                        print('Calendar: matching legacy projection found.')
                        existing_id = existing_event.get('id')
                        if not existing_id:
                            print('Calendar: matching event has no provider ID.')
                            continue
                        event_to_update = dict(ev)
                        event_to_update['description'] = self._append_hash_and_version_to_description(
                            ev.get('description', ''), ev
                        )
                        self._write_verified(calendar_id, event_to_update,
                                             'update', existing_id)
                        continue
                    # If hash doesn't match, it's a different event - we have a conflict
                    else:
                        print('Calendar: conflicting event in destination.')
                        continue
                except CalendarProjectionError:
                    raise
                except Exception:
                    print('Calendar: legacy event could not be verified.')
                    # In case of error, we'll treat as potential conflict and skip to be safe
                    continue

            # No existing event found by UID or summary+time.
            checkpoint = self.projection_state.get(uid)
            if checkpoint.get('override') == 'cancelled':
                self.projection_state.put(uid, override=None, event_id=None)
                checkpoint = self.projection_state.get(uid)
            if checkpoint.get('event_id') and checkpoint.get('pending') == 'update':
                raise CalendarProjectionError('Calendar: прежняя запись не подтверждена.')
            if checkpoint.get('event_id') and not checkpoint.get('pending'):
                self.projection_state.put(uid, override='deleted')
                continue
            event_to_insert = dict(ev)
            event_to_insert['description'] = self._append_hash_and_version_to_description(
                ev.get('description', ''), ev
            )
            self._write_verified(calendar_id, event_to_insert, 'insert', None)


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
