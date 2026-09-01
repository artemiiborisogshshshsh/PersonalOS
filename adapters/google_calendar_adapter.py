"""
Google Calendar adapter for Personal OS AI Calendar system.
Provides infrastructure for syncing events with Google Calendar.
"""

import os
import json
import datetime
from typing import List, Dict, Any, Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .base_adapter import CalendarAdapter
from models import UniversityEvent, PreparationBlock, Task
from ids import IDGenerator


class GoogleCalendarAdapter(CalendarAdapter):
    """
    Google Calendar adapter implementation.
    Handles authentication, event synchronization, and calendar management.
    """

    # If modifying these scopes, delete the file token.json.
    SCOPES = ['https://www.googleapis.com/auth/calendar']

    # Color mapping for event types (Google Calendar color IDs)
    COLOR_MAP = {
        'ЛК': '1',  # blue
        'ЛБ': '2',  # green
        'ПР': '4',  # yellow
        'PREPARATION': '3',  # purple
        'PROJECT_TASK': '11',  # red
        'READING': '7',  # cyan
        'SOCIAL': '5',  # pink
        'SLEEP': '8',  # gray
        # fallback for unknown types
        'default': '11'  # red
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize Google Calendar adapter.

        Args:
            config: Configuration dictionary with keys:
                - credentials_path: Path to Google OAuth credentials file
                - token_path: Path to store/load token file
                - calendar_name: Name of calendar to use/create
        """
        super().__init__(config)
        self.credentials_path = os.path.expanduser(
            self.config.get('credentials_path', '~/.config/google/credentials.json')
        )
        self.token_path = os.path.expanduser(
            self.config.get('token_path', '~/.config/google/token.json')
        )
        self.calendar_name = self.config.get('calendar_name', 'University Schedule')
        self.service = None
        self.calendar_id = None
        self.IDGenerator = IDGenerator()

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

    async def initialize(self) -> bool:
        """
        Initialize Google Calendar service and get/create calendar.

        Returns:
            bool: True if initialization successful
        """
        try:
            self.service = self._get_calendar_service()
            self.calendar_id = self._get_or_create_calendar(self.calendar_name)
            self._set_initialized(True)
            return True
        except Exception as e:
            print(f"Failed to initialize Google Calendar adapter: {e}")
            self._set_initialized(False)
            return False

    async def health_check(self) -> bool:
        """
        Check if Google Calendar service is accessible.

        Returns:
            bool: True if healthy, False otherwise
        """
        if not self.service or not self.calendar_id:
            return False

        try:
            # Try to get calendar info as a health check
            self.service.calendars().get(calendarId=self.calendar_id).execute()
            return True
        except Exception:
            return False

    async def shutdown(self) -> bool:
        """
        Shutdown Google Calendar adapter.

        Returns:
            bool: True (no special cleanup needed)
        """
        self.service = None
        self.calendar_id = None
        self._set_initialized(False)
        return True

    def _get_calendar_service(self):
        """
        Authenticate and return Google Calendar service object.

        Returns:
            Google Calendar service object
        """
        creds = None
        # Load existing token if available
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)

        # If no valid credentials, initiate OAuth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(
                        f"Credentials file not found at {self.credentials_path}." +
                        " Please get OAuth 2.0 Client IDs from " +
                        "Google Cloud Console."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, self.SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save credentials for next run
            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())

        return build('calendar', 'v3', credentials=creds)

    def _get_or_create_calendar(self, calendar_summary: str) -> str:
        """
        Get existing calendar by summary or create a new one.
        Returns calendar ID.
        """
        # List calendars
        calendar_list = self.service.calendarList().list().execute()
        for calendar in calendar_list.get('items', []):
            if calendar.get('summary') == calendar_summary:
                self.calendar_id = calendar['id']
                return self.calendar_id

        # Create new calendar
        calendar_body = {
            'summary': calendar_summary,
            'timeZone': 'UTC'  # We'll store events in UTC as per ICS
        }
        created = self.service.calendars().insert(body=calendar_body).execute()
        self.calendar_id = created['id']
        return self.calendar_id

    def _get_events_in_range(self, time_min: str, time_max: str) -> List[Dict]:
        """
        Fetch events in the given time range.

        Args:
            time_min: Start time in ISO format with Z
            time_max: End time in ISO format with Z

        Returns:
            List of event objects
        """
        try:
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            return events_result.get('items', [])
        except HttpError as error:
            print(f"Error fetching events: {error}")
            return []

    async def sync_events_to_calendar(self, events: List[Any]) -> Dict[str, Any]:
        """
        Sync events from planning system to Google Calendar.

        Args:
            events: List of events (UniversityEvent, PreparationBlock, Task, etc.)

        Returns:
            Dict with sync results
        """
        if not self._is_initialized:
            raise RuntimeError("Adapter not initialized. Call initialize() first.")

        result = {
            'success': True,
            'events_processed': len(events),
            'events_created': 0,
            'events_updated': 0,
            'events_skipped': 0,
            'errors': [],
            'warnings': []
        }

        try:
            # Parse ICS-style events to sync
            group_events = []
            for event in events:
                if hasattr(event, 'is_group_event') and event.is_group_event:
                    group_events.append(event)
                elif isinstance(event, UniversityEvent) and event.is_group_event:
                    group_events.append(event)

            if not group_events:
                result['warnings'].append("No group events to sync")
                return result

            # Determine time range for fetching existing events
            times = [ev.dtstart for ev in group_events] + [ev.dtend for ev in group_events]
            min_time = min(times)
            max_time = max(times)
            buffer_before = datetime.timedelta(days=1)
            buffer_after = datetime.timedelta(days=1)
            time_min = self._format_datetime(min_time - buffer_before)
            time_max = self._format_datetime(max_time + buffer_after)

            existing_events = self._get_events_in_range(time_min, time_max)

            # Build sets for deduplication
            existing_uids = set()
            existing_summary_time = set()
            for ev in existing_events:
                uid = ev.get('iCalUID')
                if uid:
                    existing_uids.add(uid)

                summary = ev.get('summary', '')
                summary_normalized = ' '.join(summary.split())
                start_str = ev.get('start', {}).get('dateTime')
                end_str = ev.get('end', {}).get('dateTime')
                if start_str and end_str:
                    try:
                        start_dt = self._parse_datetime(start_str)
                        end_dt = self._parse_datetime(end_str)
                        existing_summary_time.add(
                            (summary_normalized, start_dt, end_dt))
                    except Exception as e:
                        result['warnings'].append(
                            f"Could not parse event time for dedup: {e}"
                        )

            # Sync each event
            for ev in group_events:
                uid = ev.uid
                summary_normalized = ev.summary_normalized
                dtstart = ev.dtstart
                dtend = ev.dtend
                is_duplicate = False

                if uid in existing_uids:
                    result['warnings'].append(
                        f"Event already exists (UID: {uid}), skipping."
                    )
                    is_duplicate = True
                    result['events_skipped'] += 1
                elif (summary_normalized, dtstart, dtend) in existing_summary_time:
                    result['warnings'].append(
                        f"Event already exists (summary+time: {ev.summary} at {dtstart}), skipping."
                    )
                    is_duplicate = True
                    result['events_skipped'] += 1

                if not is_duplicate:
                    event_id = self._insert_event(ev)
                    if event_id:
                        result['events_created'] += 1
                    else:
                        result['errors'].append(
                            f"Failed to insert event: {ev.summary}"
                        )
                        result['success'] = False

        except Exception as e:
            result['success'] = False
            result['errors'].append(f"Unexpected error during sync: {e}")

        return result

    def _insert_event(
        self, event_data: Any, calendar_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Insert a new event into Google Calendar.

        Args:
            event_data: Event object (UniversityEvent, PreparationBlock, etc.)

        Returns:
            Event ID if successful, None otherwise
        """
        try:
            start_str = self._format_datetime(event_data.dtstart)
            end_str = self._format_datetime(event_data.dtend)

            event_body = {
                'summary': event_data.summary,
                'location': getattr(event_data, 'location', ''),
                'description': getattr(event_data, 'description', ''),
                'start': {
                    'dateTime': start_str,
                },
                'end': {
                    'dateTime': end_str,
                },
                'iCalUID': event_data.uid,
                'reminders': {
                    'useDefault': True,
                },
            }

            # Set color based on event type if available
            event_type = getattr(event_data, 'event_type', None)
            if event_type:
                event_type_key = getattr(event_type, 'value', event_type)
                event_body['colorId'] = self.COLOR_MAP.get(
                    event_type_key, self.COLOR_MAP['default']
                )

            event = self.service.events().insert(
                calendarId=calendar_id or self.calendar_id, body=event_body
            ).execute()

            return event.get('id')

        except HttpError as error:
            print(f"Error inserting event: {error}")
            return None
        except Exception as error:
            print(f"Unexpected error inserting event: {error}")
            return None

    def _update_event(
        self,
        calendar_id: str,
        event_id: str,
        event_data: Any,
    ) -> Optional[str]:
        """Update a Google event by its Google event ID."""
        try:
            event_body = {
                'summary': event_data.summary,
                'location': getattr(event_data, 'location', ''),
                'description': getattr(event_data, 'description', ''),
                'start': {'dateTime': self._format_datetime(event_data.dtstart)},
                'end': {'dateTime': self._format_datetime(event_data.dtend)},
                'iCalUID': event_data.uid,
                'reminders': {'useDefault': True},
            }
            event_type = getattr(event_data, 'event_type', None)
            if event_type:
                event_type_key = getattr(event_type, 'value', event_type)
                event_body['colorId'] = self.COLOR_MAP.get(
                    event_type_key, self.COLOR_MAP['default']
                )
            updated = self.service.events().update(
                calendarId=calendar_id or self.calendar_id,
                eventId=event_id,
                body=event_body,
            ).execute()
            return updated.get('id')
        except HttpError as error:
            print(f"Error updating event: {error}")
            return None
        except Exception as error:
            print(f"Unexpected error updating event: {error}")
            return None

    async def sync_events_from_calendar(self, start_time: datetime.datetime,
                                      end_time: datetime.datetime) -> List[Any]:
        """
        Sync events from Google Calendar to planning system.

        Args:
            start_time: Start of time range to sync
            end_time: End of time range to sync

        Returns:
            List of events converted to planning system format
        """
        if not self._is_initialized:
            raise RuntimeError("Adapter not initialized. Call initialize() first.")

        time_min = self._format_datetime(start_time)
        time_max = self._format_datetime(end_time)

        google_events = self._get_events_in_range(time_min, time_max)
        events = []

        for gevent in google_events:
            try:
                # Convert Google Calendar event to UniversityEvent
                uid = gevent.get('iCalUID', '')
                if not uid:
                    continue

                start_dt = self._parse_datetime(gevent['start']['dateTime'])
                end_dt = self._parse_datetime(gevent['end']['dateTime'])

                # Determine event type from colorId or description
                event_type = EventType.UNKNOWN
                color_id = gevent.get('colorId')
                if color_id:
                    # Reverse lookup color ID to event type
                    for etype, color in self.COLOR_MAP.items():
                        if color == color_id and etype != 'default':
                            event_type = EventType(etype)
                            break

                event = UniversityEvent(
                    uid=uid,
                    summary=gevent.get('summary', ''),
                    description=gevent.get('description', ''),
                    location=gevent.get('location', ''),
                    dtstart=start_dt,
                    dtend=end_dt,
                    event_type=event_type,
                    is_group_event=True  # Assuming synced events are group events
                )
                events.append(event)

            except Exception as e:
                print(f"Error converting Google Calendar event: {e}")
                continue

        return events

    async def delete_event(self, event_id: str) -> bool:
        """
        Delete an event from Google Calendar.

        Args:
            event_id: ID of event to delete

        Returns:
            bool: True if deletion successful
        """
        if not self._is_initialized:
            raise RuntimeError("Adapter not initialized. Call initialize() first.")

        try:
            self.service.events().delete(
                calendarId=self.calendar_id,
                eventId=event_id
            ).execute()
            return True
        except HttpError as error:
            print(f"Error deleting event: {error}")
            return False
        except Exception as e:
            print(f"Unexpected error deleting event: {e}")
            return False

    def event_exists_by_uid(self, calendar_id: str, uid: str) -> Optional[str]:
        """
        Check if an event with the given iCalUID exists in the calendar.
        """
        try:
            # Fetch a wide range of events to check for UID
            # Use a broad time range to cover all possible events
            time_min = self._format_datetime(datetime.datetime(1970, 1, 1))
            time_max = self._format_datetime(datetime.datetime(2100, 1, 1))
            events = self._get_events_in_range(time_min, time_max)
            for event in events:
                if event.get('iCalUID') == uid:
                    return event.get('id')
            return None
        except Exception as e:
            print(f"Error checking event existence by UID: {e}")
            return None

    def _delete_event(self, calendar_id: str, event_id: str) -> bool:
        """
        Delete an event from Google Calendar by its ID.
        This is a private method to match the expected interface in PersonalEventSyncService.
        """
        return self.delete_event(event_id)
