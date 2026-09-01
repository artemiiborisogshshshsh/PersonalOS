"""
University Domain Service
Encapsulates business logic for university-related entities:
- UniversityEvent
- PreparationBlock
- Calendar synchronization
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from models import UniversityEvent, PreparationBlock, EventType, CalendarSyncResult, PersonalUniversityEvent, PersonalEventState
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType
from ids import IDGenerator
from persistence.repository import university_event_repo, preparation_block_repo
from services.attendance_service import AttendanceRuleService


class UniversityService:
    """Service for managing university events and preparation blocks."""

    def __init__(self, planning_engine: Optional[PlanningEngine] = None):
        """
        Initialize the university service.

        Args:
            planning_engine: Optional planning engine for scheduling preparation blocks
        """
        self.planning_engine = planning_engine or PlanningEngine()
        self.IDGenerator = IDGenerator()

    # ===== University Event Methods ===

    def create_lecture(self, uid: str, summary: str, description: str,
                      location: str, dtstart: datetime, dtend: datetime,
                      is_group_event: bool = False) -> UniversityEvent:
        """
        Create a lecture event.

        Args:
            uid: Unique identifier
            summary: Event title/description
            description: Detailed description
            location: Location (room, building, etc.)
            dtstart: Start datetime
            dtend: End datetime
            is_group_event: Whether this is a group event

        Returns:
            UniversityEvent: Created lecture event
        """
        event = UniversityEvent(
            uid=uid,
            summary=summary,
            description=description,
            location=location,
            dtstart=dtstart,
            dtend=dtend,
            event_type=EventType.LECTURE,
            is_group_event=is_group_event
        )

        # Persist the event
        return university_event_repo.create(event)

    def create_lab(self, uid: str, summary: str, description: str,
                   location: str, dtstart: datetime, dtend: datetime,
                   is_group_event: bool = False) -> UniversityEvent:
        """
        Create a laboratory work event.

        Args:
            uid: Unique identifier
            summary: Event title/description
            description: Detailed description
            location: Location (room, building, etc.)
            dtstart: Start datetime
            dtend: End datetime
            is_group_event: Whether this is a group event

        Returns:
            UniversityEvent: Created lab event
        """
        event = UniversityEvent(
            uid=uid,
            summary=summary,
            description=description,
            location=location,
            dtstart=dtstart,
            dtend=dtend,
            event_type=EventType.LAB,
            is_group_event=is_group_event
        )

        # Persist the event
        return university_event_repo.create(event)

    def create_practical(self, uid: str, summary: str, description: str,
                        location: str, dtstart: datetime, dtend: datetime,
                        is_group_event: bool = False) -> UniversityEvent:
        """
        Create a practical session event.

        Args:
            uid: Unique identifier
            summary: Event title/description
            description: Detailed description
            location: Location (room, building, etc.)
            dtstart: Start datetime
            dtend: End datetime
            is_group_event: Whether this is a group event

        Returns:
            UniversityEvent: Created practical session event
        """
        event = UniversityEvent(
            uid=uid,
            summary=summary,
            description=description,
            location=location,
            dtstart=dtstart,
            dtend=dtend,
            event_type=EventType.PRACTICAL,
            is_group_event=is_group_event
        )

        # Persist the event
        return university_event_repo.create(event)

    def get_event(self, uid: str) -> Optional[UniversityEvent]:
        """
        Get a university event by UID.

        Args:
            uid: Unique identifier

        Returns:
            UniversityEvent: Found event or None
        """
        return university_event_repo.get_by_id(uid)

    def list_events(self, limit: Optional[int] = None, offset: int = 0) -> List[UniversityEvent]:
        """
        List university events with pagination.

        Args:
            limit: Maximum number of events to return
            offset: Number of events to skip

        Returns:
            List[UniversityEvent]: List of events
        """
        return university_event_repo.get_all(limit=limit, offset=offset)

    def update_event(self, event: UniversityEvent) -> UniversityEvent:
        """
        Update an existing university event.

        Args:
            event: Event to update

        Returns:
            UniversityEvent: Updated event
        """
        # Update the timestamp
        event.updated_at = datetime.now()
        return university_event_repo.update(event)

    def delete_event(self, uid: str) -> bool:
        """
        Delete a university event by UID.

        Args:
            uid: Unique identifier

        Returns:
            bool: True if deleted, False if not found
        """
        return university_event_repo.delete(uid)

    def filter_group_events(self, events: List[UniversityEvent]) -> List[UniversityEvent]:
        """
        Filter events to only include group events.

        Args:
            events: List of university events

        Returns:
            List[UniversityEvent]: Filtered list of group events
        """
        return [event for event in events if event.is_group_event]

    def get_events_by_type(self, events: List[UniversityEvent],
                          event_type: EventType) -> List[UniversityEvent]:
        """
        Get events by specific type.

        Args:
            events: List of university events
            event_type: Type of events to filter by

        Returns:
            List[UniversityEvent]: Filtered list of events
        """
        return [event for event in events if event.event_type == event_type]

    # ===== Preparation Block Methods =====

    def create_preparation_block(self, uid: str, summary: str, description: str,
                               dtstart: datetime, dtend: datetime,
                               preparation_minutes: int,
                               source_event_uid: str,
                               event_type: Optional[EventType] = None,
                               calendar_id: str = "University Schedule") -> PreparationBlock:
        """
        Create a preparation block for a university event.

        Args:
            uid: Unique identifier for the preparation block
            summary: Title/description
            description: Detailed description
            dtstart: Start datetime
            dtend: End datetime
            preparation_minutes: Duration of preparation in minutes
            source_event_uid: UID of the source university event
            event_type: Type of source event (optional)
            calendar_id: Calendar ID (defaults to "University Schedule")

        Returns:
            PreparationBlock: Created preparation block
        """
        block = PreparationBlock(
            uid=uid,
            summary=summary,
            description=description,
            dtstart=dtstart,
            dtend=dtend,
            preparation_minutes=preparation_minutes,
            source_event_uid=source_event_uid,
            event_type=event_type
        )

        # Persist the preparation block
        return preparation_block_repo.create(block)

    def get_preparation_block(self, uid: str) -> Optional[PreparationBlock]:
        """
        Get a preparation block by UID.

        Args:
            uid: Unique identifier

        Returns:
            PreparationBlock: Found preparation block or None
        """
        return preparation_block_repo.get_by_id(uid)

    def list_preparation_blocks(self, limit: Optional[int] = None, offset: int = 0) -> List[PreparationBlock]:
        """
        List preparation blocks with pagination.

        Args:
            limit: Maximum number of blocks to return
            offset: Number of blocks to skip

        Returns:
            List[PreparationBlock]: List of preparation blocks
        """
        return preparation_block_repo.get_all(limit=limit, offset=offset)

    def update_preparation_block(self, block: PreparationBlock) -> PreparationBlock:
        """
        Update an existing preparation block.

        Args:
            block: Preparation block to update

        Returns:
            PreparationBlock: Updated preparation block
        """
        block.updated_at = datetime.now()
        return preparation_block_repo.update(block)

    def delete_preparation_block(self, uid: str) -> bool:
        """
        Delete a preparation block by UID.

        Args:
            uid: Unique identifier

        Returns:
            bool: True if deleted, False if not found
        """
        return preparation_block_repo.delete(uid)

    def create_preparation_from_event(self, personal_event: PersonalUniversityEvent,
                                    preparation_minutes: int,
                                    lead_time_hours: int = 24) -> Optional[PreparationBlock]:
        """
        Create a preparation block based on a personal university event.
        Only creates preparation for confirmed or moved events.

        Args:
            personal_event: Personal university event
            preparation_minutes: Duration of preparation in minutes
            lead_time_hours: Hours before the event to start preparation

        Returns:
            PreparationBlock: Created preparation block, or None if event is not confirmed/moved
        """
        # Only create preparation for confirmed or moved events
        if personal_event.state not in (PersonalEventState.CONFIRMED, PersonalEventState.MOVED):
            return None

        # Get the linked university event
        university_event = university_event_repo.get_by_id(personal_event.university_event_uid)
        if university_event is None:
            # Linked university event not found
            return None

        # Calculate preparation time (end at event start, start lead_time_hours before)
        preparation_end = university_event.dtstart
        preparation_start = preparation_end - timedelta(minutes=preparation_minutes)

        # Generate UID for preparation block
        prep_uid = self.IDGenerator.generate_preparation_uid(university_event.uid)

        # Prepare metadata string to include in description
        metadata_info = f"Стабильный ID личного события: {personal_event.id}"
        if personal_event.state == PersonalEventState.MOVED:
            metadata_info += "; Событие было перенесено"
        # Add any relevant metadata from personal_event.metadata
        if personal_event.metadata:
            metadata_str = ", ".join(f"{k}: {v}" for k, v in personal_event.metadata.items())
            if metadata_str:
                metadata_info += f"; Метаданные: {metadata_str}"

        return self.create_preparation_block(
            uid=prep_uid,
            summary=f"Подготовка: {university_event.summary}",
            description=f"Подготовка к событию: {university_event.summary}\n"
                       f"Тип: {university_event.event_type.value if university_event.event_type else ''}\n"
                       f"Длительность подготовки: {preparation_minutes} мин\n"
                       f"Исходное событие UID: {university_event.uid}\n"
                       f"{metadata_info}",
            dtstart=preparation_start,
            dtend=preparation_end,
            preparation_minutes=preparation_minutes,
            source_event_uid=university_event.uid,
            event_type=university_event.event_type
        )

    # ===== Scheduling Methods =====

    def schedule_preparation_blocks(self, preparation_blocks: List[PreparationBlock],
                                  planning_horizon_start: datetime,
                                  planning_horizon_end: datetime,
                                  granularity_minutes: int = 30) -> Any:
        """
        Schedule preparation blocks using the planning engine.

        Args:
            preparation_blocks: List of preparation blocks to schedule
            planning_horizon_start: Start of planning horizon
            planning_horizon_end: End of planning horizon
            granularity_minutes: Time slot granularity in minutes

        Returns:
            Schedule: Result from planning engine
        """
        # Convert preparation blocks to planning items
        planning_items = []
        for block in preparation_blocks:
            planning_item = PlanningItem(
                id=block.uid,
                title=block.summary,
                description=block.description,
                item_type=PlanningItemType.PREPARATION_BLOCK,
                preferred_start=block.dtstart,
                preferred_end=block.dtend,
                duration_minutes=block.duration_minutes,
                earliest_start=block.dtstart,
                latest_end=block.dtend,
                flexible=False,  # Preparation blocks are tied to specific events
                priority=2,  # Medium-high priority for preparation
                metadata={
                    'source_event_uid': block.source_event_uid,
                    'event_type': block.source_event_type,
                    'preparation_minutes': block.preparation_minutes,
                    'calendar_id': block.calendar_id
                }
            )
            planning_items.append(planning_item)

        # Schedule using planning engine
        return self.planning_engine.schedule_items(
            planning_items,
            planning_horizon_start,
            planning_horizon_end,
            granularity_minutes
        )

    def create_planning_item_from_personal_event(self, personal_event: PersonalUniversityEvent) -> PlanningItem:
        """
        Create a PlanningItem from a personal university event for scheduling.
        Includes course, session_type, stable_id, state, and move information
        in the metadata for transfer to subsequent planning stages.

        Args:
            personal_event: The personal university event

        Returns:
            PlanningItem: Planning item representing the personal event
        """
        # Get the university event to extract course, session_type, and stable_id
        university_event = None
        if personal_event.university_event_uid:
            university_event = university_event_repo.get_by_id(personal_event.university_event_uid)

        # Initialize metadata with basic information
        metadata = {
            'personal_event_id': personal_event.id,
            'university_event_uid': personal_event.university_event_uid,
            'state': personal_event.state.value if personal_event.state else None,
            'match_confidence': personal_event.match_confidence.value if personal_event.match_confidence else None,
            'match_type': personal_event.match_type.value if personal_event.match_type else None,
            'course': personal_event.metadata.get('course'),
            'session_type': personal_event.metadata.get('session_type'),
            'stable_id': personal_event.metadata.get('stable_id'),
            'is_moved': personal_event.state == PersonalEventState.MOVED,
            'move_information': next(
                (
                    entry for entry in reversed(
                        personal_event.metadata.get('schedule_change_history', [])
                    ) if entry.get('type') in ('moved', 'time_changed')
                ),
                None,
            ),
        }

        # Extract additional information from university event if available
        if university_event:
            # Create attendance service instance for extracting information
            attendance_service = AttendanceRuleService()

            # Extract course ID from university event summary
            course_id = attendance_service._extract_course_id(university_event.summary)
            if course_id:
                metadata['course'] = course_id

            # Extract session type from university event
            session_type = attendance_service._extract_session_type(university_event)
            if session_type:
                metadata['session_type'] = session_type

            # Compute stable ID from university event
            stable_id = attendance_service.compute_stable_id(university_event)
            if stable_id:
                metadata['stable_id'] = stable_id

        metadata = {key: value for key, value in metadata.items() if value is not None}

        return PlanningItem(
            id=personal_event.id,
            title=personal_event.title,
            description=personal_event.description,
            item_type=PlanningItemType.UNIVERSITY_EVENT,
            preferred_start=personal_event.start_time,
            preferred_end=personal_event.end_time,
            duration_minutes=personal_event.duration_minutes,
            earliest_start=personal_event.start_time,
            latest_end=personal_event.end_time,
            flexible=False,  # Personal events are considered fixed in time
            priority=1,  # Low priority for personal events (can be adjusted)
            metadata=metadata
        )

    # ===== Calendar Synchronization Methods =====

    def create_calendar_sync_result(self) -> CalendarSyncResult:
        """
        Create a new calendar synchronization result object.

        Returns:
            CalendarSyncResult: Empty result object
        """
        return CalendarSyncResult(success=True)

    def events_needing_preparation(self, events: List[UniversityEvent],
                                 min_preparation_minutes: int = 30) -> List[UniversityEvent]:
        """
        Determine which events need preparation blocks created.

        Args:
            events: List of university events
            min_preparation_minutes: Minimum preparation time to consider

        Returns:
            List[UniversityEvent]: Events that should have preparation blocks
        """
        # In a real implementation, this might check existing preparation blocks,
        # complexity of subject, student level, etc.
        # For now, we'll return all group events that have sufficient duration
        group_events = self.filter_group_events(events)
        return [
            event for event in group_events
            if event.duration_minutes >= min_preparation_minutes
        ]


# ===== Use Case Demonstrations =====

def demonstrate_university_services():
    """Demonstrate use cases for the university service."""
    print("=== University Domain Service Use Cases ===\n")

    # Initialize service
    service = UniversityService()

    # Use Case 1: Creating different types of university events
    print("Use Case 1: Creating University Events")
    lecture = service.create_lecture(
        uid="lecture-001",
        summary="Математический анализ",
        description="Лекция по пределам и непрерывности функций",
        location="Аудитория 205",
        dtstart=datetime(2026, 9, 1, 10, 0),
        dtend=datetime(2026, 9, 1, 11, 30),
        is_group_event=True
    )

    lab = service.create_lab(
        uid="lab-001",
        summary="Программирование лаб работа",
        description="Лабораторная работа: основы алгоритмизации",
        location="Компьютерный класс 101",
        dtstart=datetime(2026, 9, 1, 14, 0),
        dtend=datetime(2026, 9, 1, 16, 0),
        is_group_event=True
    )

    practical = service.create_practical(
        uid="practical-001",
        summary="Физический практикум",
        description="Практические работы по механике",
        location="Физическая лаборатория",
        dtstart=datetime(2026, 9, 2, 9, 0),
        dtend=datetime(2026, 9, 2, 11, 0),
        is_group_event=True
    )

    events = [lecture, lab, practical]
    print(f"  Created {len(events)} university events:")
    for event in events:
        print(f"  - {event.summary} ({event.event_type.value})")
        print(f"    {event.dtstart.strftime('%d.%m %H:%M')} - {event.dtend.strftime('%H:%M')}")
    print()

    # Use Case 2: Persistence demonstration
    print("Use Case 2: Persistence Demonstration")
    # Retrieve the lecture we just created
    retrieved_lecture = service.get_event("lecture-001")
    if retrieved_lecture:
        print(f"  Retrieved lecture: {retrieved_lecture.summary}")
    print()

    # Use Case 3: Listing all events
    print("Use Case 3: Listing Events")
    all_events = service.list_events()
    print(f"  Total events in database: {len(all_events)}")
    for event in all_events:
        print(f"  - {event.summary}")
    print()

    # Use Case 4: Filtering events by type
    print("Use Case 4: Filtering Events by Type")
    lectures = service.get_events_by_type(events, EventType.LECTURE)
    labs = service.get_events_by_type(events, EventType.LAB)
    practicals = service.get_events_by_type(events, EventType.PRACTICAL)

    print(f"  Lectures: {len(lectures)}")
    print(f"  Labs: {len(labs)}")
    print(f"  Practicals: {len(practicals)}")
    print()

    # Use Case 5: Creating preparation blocks
    print("Use Case 5: Creating Preparation Blocks")
    personal_lecture = PersonalUniversityEvent(
        id=lecture.uid,
        title=lecture.summary,
        description=lecture.description,
        start_time=lecture.dtstart,
        end_time=lecture.dtend,
        university_event_uid=lecture.uid,
        state=PersonalEventState.CONFIRMED
    )
    prep_for_lecture = service.create_preparation_from_event(personal_lecture, 90)  # 90 min preparation
    personal_lab = PersonalUniversityEvent(
        id=lab.uid,
        title=lab.summary,
        description=lab.description,
        start_time=lab.dtstart,
        end_time=lab.dtend,
        university_event_uid=lab.uid,
        state=PersonalEventState.CONFIRMED
    )
    prep_for_lab = service.create_preparation_from_event(personal_lab, 60)          # 60 min preparation

    print(f"  Created preparation block for lecture:")
    print(f"    {prep_for_lecture.summary}")
    print(f"    {prep_for_lecture.dtstart.strftime('%d.%m %H:%M')} - {prep_for_lecture.dtend.strftime('%H:%M')}")
    print(f"    Duration: {prep_for_lecture.preparation_minutes} minutes")
    print()

    print(f"  Created preparation block for lab:")
    print(f"    {prep_for_lab.summary}")
    print(f"    {prep_for_lab.dtstart.strftime('%d.%m %H:%M')} - {prep_for_lab.dtend.strftime('%H:%M')}")
    print(f"    Duration: {prep_for_lab.preparation_minutes} minutes")
    print()

    # Use Case 6: Persistence demonstration for preparation blocks
    print("Use Case 6: Preparation Block Persistence")
    retrieved_prep = service.get_preparation_block(prep_for_lecture.uid)
    if retrieved_prep:
        print(f"  Retrieved preparation block: {retrieved_prep.summary}")
        print(f"    Duration: {retrieved_prep.preparation_minutes} minutes")
    print()

    # Use Case 7: Listing all preparation blocks
    print("Use Case 7: Listing Preparation Blocks")
    all_preps = service.list_preparation_blocks()
    print(f"  Total preparation blocks in database: {len(all_preps)}")
    for prep in all_preps:
        print(f"  - {prep.summary}")
    print()

    # Use Case 8: Scheduling preparation blocks
    print("Use Case 8: Scheduling Preparation Blocks")
    preparation_blocks = [prep_for_lecture, prep_for_lab]

    # Schedule for tomorrow 9am-6pm
    start_time = datetime(2026, 9, 1, 9, 0)
    end_time = datetime(2026, 9, 1, 18, 0)

    schedule_result = service.schedule_preparation_blocks(
        preparation_blocks, start_time, end_time, 30
    )

    scheduled_items = schedule_result.get_scheduled_items()
    unscheduled_items = schedule_result.get_unscheduled_items()

    print(f"  Scheduled {len(scheduled_items)} preparation blocks")
    print(f"  Failed to schedule {len(unscheduled_items)} preparation blocks")

    for item in scheduled_items:
        # Find the time slots for this item
        item_slots = [slot for slot in schedule_result.slots if slot.scheduled_item_id == item.id]
        if item_slots:
            start_time_slot = min(slot.start for slot in item_slots)
            end_time_slot = max(slot.end for slot in item_slots)
            print(f"    {item.title}: {start_time_slot.strftime('%H:%M')} - {end_time_slot.strftime('%H:%M')}")
    print()

    # Use Case 9: Determining which events need preparation
    print("Use Case 9: Determining Events Needing Preparation")
    events_needing_prep = service.events_needing_preparation(events, 60)
    print(f"  {len(events_needing_prep)} events need preparation (>= 60 min duration)")
    for event in events_needing_prep:
        print(f"    - {event.summary} ({event.duration_minutes} min)")
    print()

    print("✓ All university domain use cases demonstrated successfully!")


if __name__ == "__main__":
    demonstrate_university_services()
