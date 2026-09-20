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
from googleapiclient.http import HttpRequest
from services.sync_retry import retry_read

from .base_adapter import CalendarAdapter
from models import UniversityEvent, PreparationBlock, Task
from ids import IDGenerator


class CalendarReadRequest(HttpRequest):
    """Reconnect bounded read failures; never blindly repeat a write."""

    def execute(self, http=None, num_retries=0):
        transport = http or self.http
        execute = super().execute
        if self.method not in {'GET', 'HEAD'}:
            return execute(http=http, num_retries=0)
        return retry_read(lambda: execute(http=http, num_retries=0),
                          reconnect=transport.close)


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
        'TUTORING': '9',  # blue-gray
        # Keep work separate from university and existing personal colours.
        'WORK_LESSON': '6',  # tangerine
        'WORK_PREPARATION': '7',  # peacock: visually distinct from green labs
        # fallback for unknown types
        'default': '11'  # red
    }

    @staticmethod
    def _log_failure(operation: str, error: Exception | None = None) -> None:
        """Log only a stable operation and numeric HTTP status.

        Google exception text can contain request URLs, event descriptions or
        provider response bodies.  None of those belongs in desktop logs.
        """
        status = getattr(getattr(error, 'resp', None), 'status', None)
        suffix = f' (HTTP {status})' if type(status) is int or str(status).isdigit() else ''
        print(f'Google Calendar {operation} failed{suffix}')

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
        # Paths are injected by the product runtime (or environment), rather
        # than being coupled to one developer's home directory.  A desktop,
        # mobile or web host can provide its own secure token store adapter.
        self.credentials_path = os.path.expanduser(
            self.config.get('credentials_path')
            or os.environ.get('GOOGLE_CALENDAR_CREDENTIALS_PATH', '')
        )
        self.token_path = os.path.expanduser(
            self.config.get('token_path')
            or os.environ.get('GOOGLE_CALENDAR_TOKEN_PATH', '')
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
        except Exception as error:
            self._log_failure('initialization', error)
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
        if self.token_path and os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)

        # If no valid credentials, initiate OAuth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path or not os.path.exists(self.credentials_path):
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
            if not self.token_path:
                raise ValueError(
                    'GOOGLE_CALENDAR_TOKEN_PATH or calendar.token_path is required.'
                )
            token_parent = os.path.dirname(self.token_path)
            if token_parent:
                os.makedirs(token_parent, exist_ok=True)
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())

        return build('calendar', 'v3', credentials=creds,
                     requestBuilder=CalendarReadRequest)

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
            self._log_failure('read', error)
            return []

    def list_visible_calendars(self) -> List[Dict[str, Any]]:
        """Return calendars the authenticated user can read.

        This is intentionally read-only.  Planning uses the result to reserve
        genuine external commitments; it does not infer permission to change
        them.
        """
        if self.service is None:
            raise RuntimeError('Adapter not initialized')
        calendars: List[Dict[str, Any]] = []
        page_token = None
        while True:
            response = self.service.calendarList().list(
                pageToken=page_token,
            ).execute()
            calendars.extend(response.get('items', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                return calendars

    def list_events_in_calendar(
        self,
        calendar_id: str,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> List[Dict[str, Any]]:
        """Read one calendar over a horizon, including recurring instances."""
        if self.service is None:
            raise RuntimeError('Adapter not initialized')
        events: List[Dict[str, Any]] = []
        page_token = None
        while True:
            response = self.service.events().list(
                calendarId=calendar_id,
                timeMin=self._format_datetime(start),
                timeMax=self._format_datetime(end),
                singleEvents=True,
                orderBy='startTime',
                pageToken=page_token,
            ).execute()
            events.extend(response.get('items', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                return events

    def move_external_event(
        self,
        calendar_id: str,
        event_id: str,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> Optional[str]:
        """Move an external event only after an application-level approval."""
        if self.service is None:
            raise RuntimeError('Adapter not initialized')
        event = self.service.events().get(
            calendarId=calendar_id, eventId=event_id,
        ).execute()
        event['start'] = {'dateTime': self._format_datetime(start)}
        event['end'] = {'dateTime': self._format_datetime(end)}
        updated = self.service.events().update(
            calendarId=calendar_id, eventId=event_id, body=event,
        ).execute()
        return updated.get('id')

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
                    except Exception as error:
                        self._log_failure('event-time validation', error)
                        result['warnings'].append(
                            'Could not parse an existing event time for dedup.'
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

        except Exception as error:
            self._log_failure('sync', error)
            result['success'] = False
            result['errors'].append('Unexpected Google Calendar sync failure.')

        return result

    def _insert_event(
        self, event_data: Any, calendar_id: Optional[str] = None,
        *, strict: bool = False,
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
            system_block_id = getattr(event_data, 'system_block_id', None)
            if system_block_id:
                event_body['extendedProperties'] = {'private': {
                    'personal_os_block_id': system_block_id,
                    'personal_os_source_event_id': str(
                        getattr(event_data, 'system_source_event_id', ''),
                    ),
                    'personal_os_operation_id': str(
                        getattr(event_data, 'system_operation_id', ''),
                    ),
                }}

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
            # Google may return 409 when a previous process already created
            # this iCal UID but our local draft-operation snapshot was lost.
            # Treat that as the idempotent update it is, never as permission
            # to create a differently identified replacement.
            target_calendar_id = calendar_id or self.calendar_id
            if self._is_duplicate_error(error):
                uid = str(getattr(event_data, 'uid', ''))
                if strict:
                    existing = self.get_event_by_uid(
                        target_calendar_id, uid, strict=True,
                    )
                    expected_block = getattr(event_data, 'system_block_id', None)
                    if expected_block and existing:
                        private = existing.get('extendedProperties', {}).get('private', {})
                        if private.get('personal_os_block_id') != expected_block:
                            raise RuntimeError('Calendar: владелец события не подтверждён.')
                    existing_id = existing.get('id') if existing else None
                else:
                    existing_id = self.event_exists_by_uid(target_calendar_id, uid)
                if existing_id:
                    return self._update_event(
                        target_calendar_id, existing_id, event_data, strict=strict,
                    )
            if strict:
                raise
            self._log_failure('insert', error)
            return None
        except Exception as error:
            if strict:
                raise
            self._log_failure('insert', error)
            return None

    def _update_event(
        self,
        calendar_id: str,
        event_id: str,
        event_data: Any,
        *, strict: bool = False,
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
            system_block_id = getattr(event_data, 'system_block_id', None)
            if system_block_id:
                event_body['extendedProperties'] = {'private': {
                    'personal_os_block_id': system_block_id,
                    'personal_os_source_event_id': str(
                        getattr(event_data, 'system_source_event_id', ''),
                    ),
                    'personal_os_operation_id': str(
                        getattr(event_data, 'system_operation_id', ''),
                    ),
                }}
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
            if strict:
                raise
            self._log_failure('update', error)
            return None
        except Exception as error:
            if strict:
                raise
            self._log_failure('update', error)
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

            except Exception as error:
                self._log_failure('event conversion', error)
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
            if self._was_already_deleted(error):
                # DELETE is deliberately idempotent: a previous rollback or a
                # user deletion has already achieved the desired state.
                return True
            self._log_failure('delete', error)
            return False
        except Exception as error:
            self._log_failure('delete', error)
            return False

    def get_event_by_uid(
        self, calendar_id: str, uid: str, *, strict: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Return an event by UID, with legacy ownership discovery on request.

        ``strict=True`` is intentionally an indexed exact-iCalUID lookup only.
        Personal sync uses it so a missing new event never becomes a 1970--2100
        calendar scan.  The default retains legacy marker discovery for callers
        that still rely on it.
        """
        if not uid:
            return None
        # Prefer Google's indexed exact filter. This is the normal path for
        # personal university projections and avoids a full calendar scan.
        result = self.service.events().list(
            calendarId=calendar_id, iCalUID=uid,
        ).execute()
        for event in result.get('items', []):
            if event.get('iCalUID') == uid:
                return event

        if strict:
            return None

        # Legacy projections may predate the normalized iCal UID. Preserve
        # their private-property and description ownership markers on miss.
        time_min = self._format_datetime(datetime.datetime(1970, 1, 1))
        time_max = self._format_datetime(datetime.datetime(2100, 1, 1))
        page_token = None
        while True:
            request = {
                'calendarId': calendar_id,
                'timeMin': time_min,
                'timeMax': time_max,
                'singleEvents': True,
                'orderBy': 'startTime',
            }
            if page_token:
                request['pageToken'] = page_token
            events_result = self.service.events().list(**request).execute()
            for event in events_result.get('items', []):
                if event.get('iCalUID') == uid:
                    return event
                properties = event.get('extendedProperties', {}).get('private', {})
                if properties.get('personal_os_block_id') == uid:
                    return event
                if f'AI Calendar Block: {uid}' in event.get('description', ''):
                    return event
            page_token = events_result.get('nextPageToken')
            if not page_token:
                return None

    def get_event_by_id(self, calendar_id: str, event_id: str, *, strict: bool = True):
        """Read the exact event before reusing a saved provider ID."""
        try:
            return self.service.events().get(
                calendarId=calendar_id, eventId=event_id,
            ).execute()
        except HttpError as error:
            if self._was_already_deleted(error):
                return None
            if strict:
                raise
            self._log_failure('event lookup', error)
            return None

    def event_exists_by_uid(self, calendar_id: str, uid: str) -> Optional[str]:
        """Compatibility lookup returning an ID and swallowing read errors."""
        try:
            event = self.get_event_by_uid(calendar_id, uid, strict=False)
            return event.get('id') if event else None
        except Exception as error:
            self._log_failure('event lookup', error)
            return None

    @staticmethod
    def _is_duplicate_error(error: HttpError) -> bool:
        """Return whether Google rejected an insert because it already exists."""
        return getattr(getattr(error, 'resp', None), 'status', None) == 409

    def _delete_event(self, calendar_id: str, event_id: str, *, strict: bool = False) -> bool:
        """
        Delete an event from Google Calendar by its ID.
        This is a private method to match the expected interface in PersonalEventSyncService.
        """
        # Draft projection and rollback are synchronous transactions. Calling
        # the public async facade here used to create an un-awaited coroutine,
        # so the UI reported a rollback without actually deleting the event.
        if not self._is_initialized or self.service is None:
            return False
        try:
            self.service.events().delete(
                calendarId=calendar_id,
                eventId=event_id,
            ).execute()
            return True
        except HttpError as error:
            if self._was_already_deleted(error):
                # Google returns 410 for a tombstone and 404 when the tombstone
                # has expired. Neither is an error for a sync/rollback delete.
                return True
            if strict:
                raise
            self._log_failure('delete', error)
            return False
        except Exception as error:
            if strict:
                raise
            self._log_failure('delete', error)
            return False

    @staticmethod
    def _was_already_deleted(error: HttpError) -> bool:
        """Whether Google reports that a DELETE target is already absent."""
        return getattr(getattr(error, 'resp', None), 'status', None) in {404, 410}
