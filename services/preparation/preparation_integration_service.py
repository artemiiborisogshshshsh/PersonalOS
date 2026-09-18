"""
Preparation Integration Service
Links personal university events with preparation blocks and tasks.
"""
from typing import List, Optional
from datetime import datetime, timedelta
from models import PersonalUniversityEvent, PreparationRequirement, Task, PreparationPolicy, PreparationStatus, TaskStatus, TaskPriority, EstimateSource, PersonalEventState
from services.preparation.preparation_block_service import PreparationBlockService
from ids import IDGenerator


PREPARATION_WINDOW_HOURS = 36


def preparation_day_anchor(event_start: datetime) -> datetime:
    """Use 06:00 of the lesson's date as the common preparation deadline."""
    return event_start.replace(hour=6, minute=0, second=0, microsecond=0)


class PreparationIntegrationService:
    """
    Service for integrating personal university events with preparation requirements,
    preparation blocks (calendar events), and tasks.
    """

    def __init__(self, preparation_block_service: PreparationBlockService):
        """
        Initialize preparation integration service.

        Args:
            preparation_block_service: Service for creating preparation block calendar events
        """
        self.preparation_block_service = preparation_block_service

    def integrate_preparation(self, personal_event: PersonalUniversityEvent) -> None:
        """
        Integrate preparation for a single personal university event.
        Creates a preparation requirement, task, and schedules a preparation block
        if the event is confirmed or moved and does not already have preparation links.

        Args:
            personal_event: The personal university event to integrate preparation for
        """
        if personal_event.state == PersonalEventState.CANCELLED:
            self.cancel_preparation(personal_event)
            return

        # Only integrate preparation for confirmed or moved events that have a linked university event
        if personal_event.state not in (PersonalEventState.CONFIRMED, PersonalEventState.MOVED) or personal_event.university_event_uid is None:
            return

        # If preparation block or task already linked and event is not moved, skip to avoid duplication
        if personal_event.state != PersonalEventState.MOVED and personal_event.preparation_block_uid is not None and personal_event.task_uid is not None:
            return

        # Generate preparation requirement
        prep_req = self._create_preparation_requirement(personal_event)

        # Preserve the existing task when an event moves; its deadline/context
        # changes, but creating a second task would be a duplicate.
        task = None
        if not (personal_event.state == PersonalEventState.MOVED and personal_event.task_uid):
            task = self._create_task_from_preparation_requirement(prep_req, personal_event)

        # Schedule preparation block calendar event
        prep_block_uid = self._schedule_preparation_block(personal_event, prep_req)

        # Link the preparation block and task to the personal event
        if prep_block_uid is not None:
            personal_event.preparation_block_uid = prep_block_uid
        if task is not None:
            personal_event.task_uid = task.id

        # Optionally, we could also link the preparation requirement ID to the personal event
        # but there's no field for that currently. We could store it in metadata.
        personal_event.metadata['preparation_requirement_id'] = prep_req.id

    def cancel_preparation(self, personal_event: PersonalUniversityEvent) -> bool:
        """Remove only this system-owned preparation projection and audit links."""
        if not personal_event.university_event_uid:
            return False
        calendar_id = self.preparation_block_service.get_or_create_calendar(
            'University Schedule'
        )
        prep_uid = IDGenerator.generate_preparation_uid(
            personal_event.university_event_uid
        )
        google_event_id = self.preparation_block_service.event_exists_by_uid(
            calendar_id, prep_uid
        )
        if not google_event_id and personal_event.preparation_block_uid:
            # The lookup may have failed transiently. Keep active links so a
            # later sync can retry instead of pretending cancellation finished.
            personal_event.metadata['preparation_cancellation_error'] = (
                'calendar_projection_not_found'
            )
            return False
        deleted = True
        if google_event_id:
            deleted = self.preparation_block_service.delete_preparation_block_event(
                calendar_id, google_event_id
            )
        if not deleted:
            personal_event.metadata['preparation_cancellation_error'] = (
                'calendar_delete_failed'
            )
            return False

        if personal_event.preparation_block_uid:
            personal_event.metadata['cancelled_preparation_block_uid'] = (
                personal_event.preparation_block_uid
            )
        if personal_event.task_uid:
            personal_event.metadata['cancelled_preparation_task_uid'] = (
                personal_event.task_uid
            )
        personal_event.preparation_block_uid = None
        personal_event.task_uid = None
        personal_event.metadata['preparation_action'] = 'cancelled'
        personal_event.metadata.pop('preparation_cancellation_error', None)
        return True

    def build_preparation_requirement(
        self,
        personal_event: PersonalUniversityEvent,
    ) -> Optional[PreparationRequirement]:
        """Build a requirement only for an event the user will attend."""
        if personal_event.state not in (
            PersonalEventState.CONFIRMED,
            PersonalEventState.MOVED,
        ) or personal_event.university_event_uid is None:
            return None
        return self._create_preparation_requirement(personal_event)

    def _create_preparation_requirement(self, personal_event: PersonalUniversityEvent) -> PreparationRequirement:
        """
        Create a preparation requirement from a personal university event.

        Args:
            personal_event: The matched personal university event

        Returns:
            PreparationRequirement: The created preparation requirement
        """
        # Determine preparation type based on event type or other factors
        preparation_type = self._determine_preparation_type(personal_event)

        # Get preparation time estimate using PreparationPolicy
        # We need to extract course ID and event ID from the personal event
        course_id = self._extract_course_id(personal_event)
        event_id = personal_event.university_event_uid
        difficulty = float(personal_event.metadata.get('difficulty', 1.0))
        priority = int(personal_event.metadata.get('priority', 2))
        course_multiplier = float(
            personal_event.metadata.get('course_preparation_multiplier', 1.0)
        )
        preparation_deadline = preparation_day_anchor(personal_event.start_time)
        window_start = preparation_deadline - timedelta(hours=PREPARATION_WINDOW_HOURS)
        hours_until_deadline = max(
            0.0,
            (preparation_deadline - datetime.now()).total_seconds() / 3600,
        )

        estimate = PreparationPolicy.create_preparation_estimate(
            session_type=preparation_type,
            source=EstimateSource.DEFAULT_RULE,
            confidence=0.8,
            course_id=course_id,
            event_id=event_id,
            difficulty=difficulty,
            priority=priority,
            hours_until_deadline=hours_until_deadline,
            course_multiplier=course_multiplier,
        )

        # Generate UID for the preparation requirement
        prep_req_id = IDGenerator.generate_uid()

        # Create preparation requirement
        prep_req = PreparationRequirement(
            id=prep_req_id,
            title=f"Подготовка к: {personal_event.title}",
            description=f"Подготовка к занятию: {personal_event.description}",
            source_session_id=personal_event.id,  # Link to the personal event
            source_task_id=None,  # Not derived from a task
            preparation_type=preparation_type,
            _time_estimate=estimate,
            required_materials=list(personal_event.metadata.get('required_materials', [])),
            prerequisites=list(personal_event.metadata.get('prerequisites', [])),
            due_time=preparation_deadline,
            status=PreparationStatus.INBOX,
            metadata={
                "personal_event_id": personal_event.id,
                "university_event_uid": personal_event.university_event_uid,
                "course_id": course_id,
                "session_type": preparation_type,
                "difficulty": difficulty,
                "priority": priority,
                "deadline": preparation_deadline.isoformat(),
                "preparation_window_start": window_start.isoformat(),
                "preparation_window_end": preparation_deadline.isoformat(),
                "target_event_start": personal_event.start_time.isoformat(),
                "preparation_lead_hours": PREPARATION_WINDOW_HOURS,
                "deep_work": True,
                "travel_buffer_minutes": int(
                    personal_event.metadata.get('travel_buffer_minutes', 0)
                ),
            }
        )
        return prep_req

    def _determine_preparation_type(self, personal_event: PersonalUniversityEvent) -> str:
        """
        Determine the preparation type based on the personal event.
        Defaults to 'study' but can be refined based on event type, subject, etc.

        Args:
            personal_event: The personal university event

        Returns:
            str: The preparation type (e.g., 'study', 'review', 'practice')
        """
        session_type = personal_event.metadata.get('session_type')
        if session_type:
            return str(session_type).lower()
        title = personal_event.title.casefold()
        if '(лб)' in title or 'лаборатор' in title or ' lab' in title:
            return 'lab'
        if '(пр)' in title or 'практи' in title:
            return 'practical'
        if '(лк)' in title or 'лекц' in title:
            return 'lecture'
        return "study"

    def _extract_course_id(self, personal_event: PersonalUniversityEvent) -> Optional[str]:
        """
        Extract course ID from the personal event.
        In a full implementation, this would parse the title or description.
        For now, we return None to use defaults.

        Args:
            personal_event: The personal university event

        Returns:
            Optional[str]: Course ID if extractable, otherwise None
        """
        metadata_course = personal_event.metadata.get('course')
        if metadata_course:
            return str(metadata_course)
        import re
        course = re.sub(r'\([^)]*\)', '', personal_event.title).strip()
        return course or None

    def _create_task_from_preparation_requirement(
        self,
        prep_req: PreparationRequirement,
        personal_event: PersonalUniversityEvent
    ) -> Task:
        """
        Create a task from a preparation requirement.

        Args:
            prep_req: The preparation requirement
            personal_event: The related personal university event

        Returns:
            Task: The created task
        """
        # Generate UID for the task
        task_id = IDGenerator.generate_uid()

        # Extract course ID and event type from personal event
        course_id = self._extract_course_id(personal_event)
        event_type_code = self._extract_event_type_code(personal_event)

        # Create task
        task = Task(
            id=task_id,
            title=prep_req.title,
            description=prep_req.description,
            project_id=None,  # Standalone task for preparation
            status=TaskStatus.INBOX,
            priority=TaskPriority.MEDIUM,  # Could be based on event importance
            created_at=datetime.now(),
            updated_at=datetime.now(),
            due_date=prep_req.due_time,
            time_estimate=prep_req.time_estimate,
            assignee=None,  # Assuming personal task
            tags=set(),  # Could add tags based on preparation type
            dependencies=[],  # No dependencies by default
            metadata={
                "preparation_requirement_id": prep_req.id,
                "personal_event_id": personal_event.id,
                "university_event_uid": personal_event.university_event_uid,
                "course_id": course_id,
                "event_type_code": event_type_code
            }
        )
        return task

    def _schedule_preparation_block(
        self,
        personal_event: PersonalUniversityEvent,
        prep_req: PreparationRequirement
    ) -> Optional[str]:
        """
        Schedule a preparation block calendar event for the personal event.

        Args:
            personal_event: The personal university event
            prep_req: The preparation requirement with time estimate

        Returns:
            Optional[str]: The UID of the created preparation block calendar event, or None if failed
        """
        # If no time estimate, we cannot schedule
        if prep_req.time_estimate is None:
            return None

        preparation_minutes = int(prep_req.time_estimate.value)

        # Prepare event data for the preparation block service
        event_data = {
            'summary': personal_event.title,
            'description': personal_event.description,
            'event_type': self._extract_event_type_code(personal_event),  # e.g., 'ЛК', 'ЛБ', 'ПР'
            'dtstart': personal_event.start_time,
            'dtend': personal_event.end_time,
            # The preparation block service will compute the preparation block timing
            # based on the event start time and preparation duration
        }

        # We need to use the preparation block service's internal method to find a free slot
        # and insert the event. However, the service is designed to work with a list of events
        # from an ICS file. We can adapt by using its helper methods.

        # Alternatively, we can call the service's sync_preparation_blocks method with a custom ICS
        # but that's overkill.

        # Let's use the preparation block service's adapter directly to insert an event.
        # We need to get the calendar ID first.

        # For simplicity, we'll assume the preparation block service can handle a single event.
        # We'll create a temporary method or use existing ones.

        # Since we don't want to modify the preparation block service, we'll use its
        # get_or_create_calendar and insert_preparation_block methods.

        # However, insert_preparation_block expects event_data that includes the preparation
        # block timing (dtstart, dtend for the preparation block itself), not the source event.

        # We need to compute the preparation block timing: find a free slot of length
        # preparation_minutes before the event start time.

        # We can reuse the logic from preparation_block_service.find_free_slot, but we need
        # busy intervals. We don't have access to the calendar's busy intervals without
        # fetching events.

        # Given the complexity, and since the preparation block service already has
        # the logic to schedule preparation blocks for a list of events, we can
        # create a dummy ICS-like list with just this event and call a modified version
        # of the service's processing.

        # But we are not allowed to modify the preparation block service? We can add a method
        # to it for scheduling a single event.

        # However, to avoid modifying existing services, let's create a simplified version
        # here that uses the same logic as the preparation block service but for a single event.

        # We'll need to fetch existing events from the calendar to get busy intervals.
        # We can use the preparation block service's get_existing_events method.

        # Let's do that.

        # Get or create calendar
        calendar_id = self.preparation_block_service.get_or_create_calendar('University Schedule')
        prep_block_uid = IDGenerator.generate_preparation_uid(
            personal_event.university_event_uid or personal_event.id
        )
        existing_event_id = self.preparation_block_service.event_exists_by_uid(
            calendar_id, prep_block_uid
        )

        # Determine time range for fetching existing events: we need to look at a window
        # that covers the preparation block search area.
        preparation_deadline = preparation_day_anchor(personal_event.start_time)
        search_start = preparation_deadline - timedelta(hours=PREPARATION_WINDOW_HOURS)
        search_end = preparation_deadline
        # The same 36-hour window is used by the draft planner.  This legacy
        # projection remains compatible while never scheduling after 06:00.
        lead_time_hours = float(PREPARATION_WINDOW_HOURS)
        max_prep_duration = timedelta(hours=2)  # safe upper bound
        buffer_before = timedelta(hours=lead_time_hours) + max_prep_duration
        time_min = self._format_datetime(search_start - buffer_before)
        time_max = self._format_datetime(search_end)
        existing_events = self.preparation_block_service.get_existing_events(calendar_id, time_min, time_max)

        # Build a set of busy intervals from existing events
        busy_intervals = []
        for ev in existing_events:
            if ev.get('iCalUID') == prep_block_uid:
                continue
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
                start = datetime.fromisoformat(start_str)
                end = datetime.fromisoformat(end_str)
                busy_intervals.append((start, end))
            except ValueError:
                # Skip events we can't parse
                continue

        # Sort events by start time (not strictly necessary for busy intervals)
        # We'll use the preparation block service's find_free_slot method
        lead_time = timedelta(hours=lead_time_hours)
        slot = self.preparation_block_service.find_free_slot(
            busy_intervals,
            preparation_deadline,
            preparation_minutes,
            lead_time
        )
        if slot is None:
            # Could not find free slot
            return None

        prep_start, prep_end = slot

        # Prepare event data for the preparation block
        prep_event_data = {
            'uid': prep_block_uid,
            'summary': f"Подготовка: {personal_event.title}",
            'description': f"Подготовка к занятию: {personal_event.description}\n"
                         f"Тип: {self._extract_event_type_code(personal_event) or 'неопределен'}\n"
                         f"Длительность подготовки: {preparation_minutes} мин\n"
                         f"Исходное событие UID: {personal_event.university_event_uid or 'неизвестно'}",
            'location': '',
            'dtstart': prep_start,
            'dtend': prep_end,
        }

        if existing_event_id:
            if personal_event.state != PersonalEventState.MOVED:
                return prep_block_uid
            updated_event_id = self.preparation_block_service.update_preparation_block_event(
                calendar_id,
                existing_event_id,
                prep_event_data,
            )
            return prep_block_uid if updated_event_id else None

        # Insert the preparation block event
        event_id = self.preparation_block_service.insert_preparation_block(
            calendar_id,
            prep_event_data,
            is_prep=True
        )
        if event_id:
            return prep_block_uid
        return None

    def _extract_event_type_code(self, personal_event: PersonalUniversityEvent) -> Optional[str]:
        """
        Extract event type code (e.g., 'ЛК', 'ЛБ', 'ПР') from the personal event.
        We can look at the metadata or try to infer from the title.

        Args:
            personal_event: The personal university event

        Returns:
            Optional[str]: Event type code if found, otherwise None
        """
        # Try to get from metadata if available
        if 'event_type_code' in personal_event.metadata:
            return personal_event.metadata['event_type_code']

        # Try to extract from title or description using regex
        import re
        # Look for pattern (XX) where XX are Cyrillic letters
        match = re.search(r'\(([А-Яа-я]+)\)', personal_event.title)
        if match:
            return match.group(1)
        match = re.search(r'\(([А-Яа-я]+)\)', personal_event.description)
        if match:
            return match.group(1)

        # If we have a university event UID, we might need to look up the original event
        # but we don't have access to the university event here.
        return None

    def _format_datetime(self, dt: datetime) -> str:
        """Format datetime as ISO 8601 string with Zulu time suffix."""
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
