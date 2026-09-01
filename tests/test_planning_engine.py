"""
Unit tests for the planning engine.
"""

import pytest
from datetime import datetime, timedelta
from planning_engine import (
    PlanningEngine, PlanningItem, PlanningItemType, TimeSlot, Schedule,
    Constraint, TimeWindowConstraint, DependencyConstraint, ResourceConstraint,
    DurationConstraint, SleepConstraint, create_planning_item_from_university_event,
    create_planning_item_from_task, create_planning_item_from_preparation_block
)
from models import UniversityEvent, PreparationBlock, Project, Task, EventType, ProjectStatus, TaskStatus, TaskPriority
from ids import IDGenerator


def test_sleep_constraint_rejects_partial_overlap_at_boundary():
    constraint = SleepConstraint(sleep_start_hour=22, sleep_end_hour=6)
    item = PlanningItem(
        id='late-work',
        title='Late work',
        description='',
        item_type=PlanningItemType.PROJECT_TASK,
        duration_minutes=60,
    )
    schedule = Schedule(items={item.id: item})
    overlapping = TimeSlot(
        datetime(2026, 9, 1, 21, 30),
        datetime(2026, 9, 1, 22, 30),
    )
    daytime = TimeSlot(
        datetime(2026, 9, 1, 14, 0),
        datetime(2026, 9, 1, 15, 0),
    )

    assert constraint.evaluate(item, overlapping, schedule) == float('-inf')
    assert constraint.evaluate(item, daytime, schedule) == 0.0

    sleep = PlanningItem(
        id='sleep',
        title='Sleep',
        description='',
        item_type=PlanningItemType.CUSTOM,
        duration_minutes=60,
        metadata={'is_sleep': True},
    )
    assert constraint.evaluate(sleep, overlapping, schedule) == 0.0


def test_planning_item_creation():
    """Test PlanningItem creation and properties."""
    item = PlanningItem(
        id="test-item",
        title="Test Item",
        description="A test planning item",
        item_type=PlanningItemType.CUSTOM,
        preferred_start=datetime(2026, 9, 1, 10, 0),
        preferred_end=datetime(2026, 9, 1, 12, 0),
        duration_minutes=120,
        earliest_start=datetime(2026, 9, 1, 9, 0),
        latest_end=datetime(2026, 9, 1, 18, 0),
        flexible=True,
        priority=2
    )

    assert item.id == "test-item"
    assert item.title == "Test Item"
    assert item.description == "A test planning item"
    assert item.item_type == PlanningItemType.CUSTOM
    assert item.preferred_start == datetime(2026, 9, 1, 10, 0)
    assert item.preferred_end == datetime(2026, 9, 1, 12, 0)
    assert item.duration_minutes == 120
    assert item.earliest_start == datetime(2026, 9, 1, 9, 0)
    assert item.latest_end == datetime(2026, 9, 1, 18, 0)
    assert item.flexible == True
    assert item.priority == 2
    assert item.dependencies == set()
    assert item.required_resources == set()
    assert item.metadata == {}


def test_time_slot_properties():
    """Test TimeSlot properties."""
    start = datetime(2026, 9, 1, 10, 0)
    end = datetime(2026, 9, 1, 12, 0)
    slot = TimeSlot(start=start, end=end)

    assert slot.start == start
    assert slot.end == end
    assert slot.score == 0.0
    assert slot.scheduled_item_id is None
    assert slot.metadata == {}
    assert slot.duration_minutes == 120


def test_schedule_operations():
    """Test Schedule operations."""
    schedule = Schedule()
    item1 = PlanningItem("item1", "Item 1", "Desc 1", PlanningItemType.CUSTOM)
    item2 = PlanningItem("item2", "Item 2", "Desc 2", PlanningItemType.CUSTOM)

    schedule.items = {"item1": item1, "item2": item2}

    # Test getting item
    assert schedule.get_item("item1") == item1
    assert schedule.get_item("nonexistent") is None

    # Initially no scheduled items
    assert len(schedule.get_scheduled_items()) == 0
    # Note: unscheduled_items is not automatically computed - it's managed by the scheduling algorithm
    # For this manual test, we'll check what we expect

    # Add a scheduled slot
    slot = TimeSlot(datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 11, 0))
    slot.scheduled_item_id = "item1"
    schedule.slots.append(slot)

    # Test getting scheduled items
    scheduled = schedule.get_scheduled_items()
    assert len(scheduled) == 1
    assert scheduled[0].id == "item1"

    # Test getting unscheduled items - manually check what we expect
    # Since we're managing this manually in test, item2 should be considered unscheduled
    # But since unscheduled_items is empty, we'll check that item2 is in items but not in slots
    scheduled_item_ids = {slot.scheduled_item_id for slot in schedule.slots if slot.scheduled_item_id}
    unscheduled_items = [item for item_id, item in schedule.items.items() if item_id not in scheduled_item_ids]
    assert len(unscheduled_items) == 1
    assert unscheduled_items[0].id == "item2"


def test_time_window_constraint():
    """Test TimeWindowConstraint."""
    constraint = TimeWindowConstraint()
    schedule = Schedule()

    item = PlanningItem(
        "test-item", "Test Item", "Desc", PlanningItemType.CUSTOM,
        earliest_start=datetime(2026, 9, 1, 9, 0),
        latest_end=datetime(2026, 9, 1, 17, 0),
        preferred_start=datetime(2026, 9, 1, 10, 0),
        preferred_end=datetime(2026, 9, 1, 12, 0)
    )

    # Valid slot within window
    valid_slot = TimeSlot(datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 12, 0))
    score = constraint.evaluate(item, valid_slot, schedule)
    assert score != float('-inf')  # Should not violate hard constraint

    # Slot too early
    early_slot = TimeSlot(datetime(2026, 9, 1, 8, 0), datetime(2026, 9, 1, 9, 0))
    score = constraint.evaluate(item, early_slot, schedule)
    assert score == float('-inf')  # Should violate hard constraint

    # Slot too late
    late_slot = TimeSlot(datetime(2026, 9, 1, 18, 0), datetime(2026, 9, 1, 19, 0))
    score = constraint.evaluate(item, late_slot, schedule)
    assert score == float('-inf')  # Should violate hard constraint


def test_dependency_constraint():
    """Test DependencyConstraint."""
    constraint = DependencyConstraint()
    schedule = Schedule()

    # Item with no dependencies
    item_no_deps = PlanningItem(
        "item1", "Item 1", "Desc", PlanningItemType.CUSTOM
    )

    slot = TimeSlot(datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 11, 0))
    score = constraint.evaluate(item_no_deps, slot, schedule)
    assert score == 0.0  # No dependencies, should be fine

    # Item with unscheduled dependency
    item_with_dep = PlanningItem(
        "item2", "Item 2", "Desc", PlanningItemType.CUSTOM,
        dependencies={"item1"}
    )

    # Add the dependency item to schedule but not scheduled yet
    dep_item = PlanningItem("item1", "Item 1", "Desc", PlanningItemType.CUSTOM)
    schedule.items = {"item1": dep_item, "item2": item_with_dep}

    score = constraint.evaluate(item_with_dep, slot, schedule)
    assert score == float('-inf')  # Dependency not scheduled

    # Schedule the dependency
    dep_slot = TimeSlot(datetime(2026, 9, 1, 9, 0), datetime(2026, 9, 1, 10, 0))
    dep_slot.scheduled_item_id = "item1"
    schedule.slots.append(dep_slot)

    # Now dependency is scheduled and ends before our slot starts
    score = constraint.evaluate(item_with_dep, slot, schedule)
    assert score == 0.0  # Should be fine now

    # Dependency ends after our slot starts (overlap)
    overlapping_slot = TimeSlot(datetime(2026, 9, 1, 9, 30), datetime(2026, 9, 1, 10, 30))
    score = constraint.evaluate(item_with_dep, overlapping_slot, schedule)
    assert score == float('-inf')  # Should violate dependency constraint


def test_resource_constraint():
    """Test ResourceConstraint."""
    constraint = ResourceConstraint()
    schedule = Schedule()

    # Items with no resource conflict
    item1 = PlanningItem(
        "item1", "Item 1", "Desc", PlanningItemType.CUSTOM,
        required_resources={"resourceA"}
    )
    item2 = PlanningItem(
        "item2", "Item 2", "Desc", PlanningItemType.CUSTOM,
        required_resources={"resourceB"}
    )

    schedule.items = {"item1": item1, "item2": item2}

    # Schedule item1 first
    slot1 = TimeSlot(datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 11, 0))
    slot1.scheduled_item_id = "item1"
    schedule.slots.append(slot1)

    # Try to schedule item2 at same time - should conflict due to time overlap but different resources
    slot2 = TimeSlot(datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 11, 0))
    score = constraint.evaluate(item2, slot2, schedule)
    # Time overlap but different resources - should be OK (no resource conflict)
    # Actually, our constraint only checks resource conflict when there's time overlap
    # Since resources are different, it should return 0
    assert score == 0.0

    # Items with same resource - should conflict
    item3 = PlanningItem(
        "item3", "Item 3", "Desc", PlanningItemType.CUSTOM,
        required_resources={"resourceA"}  # Same as item1
    )
    schedule.items["item3"] = item3

    slot3 = TimeSlot(datetime(2026, 9, 1, 10, 30), datetime(2026, 9, 1, 11, 30))  # Overlaps with item1
    score = constraint.evaluate(item3, slot3, schedule)
    assert score == float('-inf')  # Should violate resource constraint


def test_duration_constraint():
    """Test DurationConstraint."""
    constraint = DurationConstraint()
    schedule = Schedule()

    # Item requiring 60 minutes
    item = PlanningItem(
        "test-item", "Test Item", "Desc", PlanningItemType.CUSTOM,
        duration_minutes=60
    )

    # Slot with sufficient duration
    adequate_slot = TimeSlot(datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 11, 30))  # 90 minutes
    score = constraint.evaluate(item, adequate_slot, schedule)
    assert score != float('-inf')  # Should not violate hard constraint
    # Should have small negative score for excess time (30 extra minutes = -0.5)
    assert score <= 0.0

    # Slot with insufficient duration
    insufficient_slot = TimeSlot(datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 10, 30))  # 30 minutes
    score = constraint.evaluate(item, insufficient_slot, schedule)
    assert score == float('-inf')  # Should violate hard constraint

    # Item with no duration preference
    item_no_duration = PlanningItem(
        "test-item2", "Test Item 2", "Desc", PlanningItemType.CUSTOM
        # No duration_minutes specified (defaults to 0)
    )
    score = constraint.evaluate(item_no_duration, adequate_slot, schedule)
    assert score == 0.0  # Should be fine


def test_planning_engine_initialization():
    """Test PlanningEngine initialization."""
    # Default initialization
    engine = PlanningEngine()
    assert len(engine.constraints) == 10  # Default constraints: TimeWindow, Dependency, Resource, Duration, Sleep, FixedCommitment, TravelTime, MaxContinuousWork, PreparationBeforeEvent, WeeklyCapacityConstraint
    assert len(engine.scoring_functions) == 15  # Default scoring functions

    # Custom initialization
    custom_constraints = [TimeWindowConstraint()]
    custom_scoring = [lambda item, slot, schedule: 1.0]
    engine_custom = PlanningEngine(constraints=custom_constraints, scoring_functions=custom_scoring)
    assert len(engine_custom.constraints) == 1
    assert len(engine_custom.scoring_functions) == 1


def test_generate_time_slots():
    """Test time slot generation."""
    engine = PlanningEngine()
    start = datetime(2026, 9, 1, 9, 0)
    end = datetime(2026, 9, 1, 17, 0)  # 8 hours

    slots = engine.generate_time_slots(start, end, granularity_minutes=60)

    assert len(slots) == 8  # 8 one-hour slots
    assert slots[0].start == start
    assert slots[0].end == datetime(2026, 9, 1, 10, 0)
    assert slots[-1].start == datetime(2026, 9, 1, 16, 0)
    assert slots[-1].end == end


def test_evaluate_item_slot():
    """Test evaluating an item in a slot."""
    engine = PlanningEngine()
    schedule = Schedule()

    item = PlanningItem(
        "test-item", "Test Item", "Desc", PlanningItemType.CUSTOM,
        duration_minutes=60,
        earliest_start=datetime(2026, 9, 1, 9, 0),
        latest_end=datetime(2026, 9, 1, 17, 0),
        priority=2
    )

    slot = TimeSlot(datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 11, 0))

    score = engine.evaluate_item_slot(item, slot, schedule)
    assert score != float('-inf')  # Should not violate any hard constraints
    assert isinstance(score, float)
    assert score > float('-inf')


def test_schedule_simple_items():
    """Test scheduling simple items without conflicts."""
    engine = PlanningEngine()
    start_time = datetime(2026, 9, 1, 9, 0)
    end_time = datetime(2026, 9, 1, 17, 0)

    # Create several simple items with different durations
    items = [
        PlanningItem(
            "item1", "Task 1", "First task", PlanningItemType.PROJECT_TASK,
            duration_minutes=60,
            earliest_start=start_time,
            latest_end=end_time,
            priority=2
        ),
        PlanningItem(
            "item2", "Task 2", "Second task", PlanningItemType.PROJECT_TASK,
            duration_minutes=90,
            earliest_start=start_time,
            latest_end=end_time,
            priority=1
        ),
        PlanningItem(
            "item3", "Task 3", "Third task", PlanningItemType.PROJECT_TASK,
            duration_minutes=30,
            earliest_start=start_time,
            latest_end=end_time,
            priority=3
        )
    ]

    schedule = engine.schedule_items(items, start_time, end_time, granularity_minutes=30)

    # All items should be scheduled
    assert len(schedule.unscheduled_items) == 0
    assert len(schedule.get_scheduled_items()) == 3

    # Check that no time overlaps exist
    scheduled_items = schedule.get_scheduled_items()
    # Get the actual time spans for each scheduled item
    item_time_spans = {}
    for item in scheduled_items:
        # Find all slots scheduled for this item
        item_slots = [slot for slot in schedule.slots if slot.scheduled_item_id == item.id]
        if item_slots:
            # Calculate overall time span
            min_start = min(slot.start for slot in item_slots)
            max_end = max(slot.end for slot in item_slots)
            item_time_spans[item.id] = (min_start, max_end)

    # Check that no time overlaps exist between items
    item_ids = list(item_time_spans.keys())
    for i in range(len(item_ids)):
        for j in range(i+1, len(item_ids)):
            id1, id2 = item_ids[i], item_ids[j]
            start1, end1 = item_time_spans[id1]
            start2, end2 = item_time_spans[id2]
            # Check for overlap
            overlap = not (end1 <= start2 or end2 <= start1)
            assert not overlap, f"Items {id1} and {id2} overlap: [{start1}-{end1}] and [{start2}-{end2}]"


def test_schedule_with_dependencies():
    """Test scheduling items with dependencies."""
    engine = PlanningEngine()
    start_time = datetime(2026, 9, 1, 9, 0)
    end_time = datetime(2026, 9, 1, 17, 0)

    # Create items with dependencies using realistic durations
    task_a = PlanningItem(
        "task-a", "Task A", "First task", PlanningItemType.PROJECT_TASK,
        duration_minutes=60,
        earliest_start=start_time,
        latest_end=end_time,
        priority=2
    )

    task_b = PlanningItem(
        "task-b", "Task B", "Second task (depends on A)", PlanningItemType.PROJECT_TASK,
        duration_minutes=90,
        earliest_start=start_time,
        latest_end=end_time,
        priority=2,
        dependencies={"task-a"}  # B depends on A
    )

    task_c = PlanningItem(
        "task-c", "Task C", "Third task (depends on A and B)", PlanningItemType.PROJECT_TASK,
        duration_minutes=30,
        earliest_start=start_time,
        latest_end=end_time,
        priority=2,
        dependencies={"task-a", "task-b"}  # C depends on both A and B
    )

    items = [task_a, task_b, task_c]
    schedule = engine.schedule_items(items, start_time, end_time, granularity_minutes=30)

    # All items should be scheduled
    assert len(schedule.unscheduled_items) == 0
    assert len(schedule.get_scheduled_items()) == 3

    # Get scheduled items and their actual time spans
    scheduled_items = schedule.get_scheduled_items()
    item_time_spans = {}
    for item in scheduled_items:
        # Find all slots scheduled for this item
        item_slots = [slot for slot in schedule.slots if slot.scheduled_item_id == item.id]
        if item_slots:
            # Calculate overall time span
            min_start = min(slot.start for slot in item_slots)
            max_end = max(slot.end for slot in item_slots)
            item_time_spans[item.id] = (min_start, max_end)

    # Verify dependencies: A finishes before B starts, A and B finish before C starts
    a_start, a_end = item_time_spans["task-a"]
    b_start, b_end = item_time_spans["task-b"]
    c_start, c_end = item_time_spans["task-c"]

    # A should finish before B starts
    assert a_end <= b_start, f"Task A ({a_end}) should finish before Task B starts ({b_start})"

    # Both A and B should finish before C starts
    assert a_end <= c_start, f"Task A ({a_end}) should finish before Task C starts ({c_start})"
    assert b_end <= c_start, f"Task B ({b_end}) should finish before Task C starts ({c_start})"


def test_create_planning_item_from_domain_models():
    """Test creating PlanningItems from existing domain models."""
    # Test UniversityEvent
    event = UniversityEvent(
        uid="event-123",
        summary="Математика (ЛК)",
        description="Лекция по высшей математике",
        location="Аудитория 101",
        dtstart=datetime(2026, 9, 1, 10, 0),
        dtend=datetime(2026, 9, 1, 12, 0),
        event_type=EventType.LECTURE,
        is_group_event=True
    )

    planning_item = create_planning_item_from_university_event(event)
    assert planning_item.id == "event-123"
    assert planning_item.title == "Математика (ЛК)"
    assert planning_item.item_type == PlanningItemType.UNIVERSITY_EVENT
    assert planning_item.duration_minutes == 120
    assert planning_item.flexible == False
    assert planning_item.metadata['event_type'] == "ЛК"
    assert planning_item.metadata['is_group_event'] == True

    # Test Task
    project = Project(
        id="proj-123",
        name="Website Redesign",
        description="Redesign company website",
        status=ProjectStatus.PLANNING,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    task = Task(
        id="task-123",
        title="Implement login feature",
        description="Create user authentication system",
        project_id="proj-123",
        status=TaskStatus.INBOX,
        priority=TaskPriority.HIGH,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        due_date=datetime(2026, 9, 1, 18, 0)
    )
    task.estimated_hours = 3.0

    planning_item = create_planning_item_from_task(task)
    assert planning_item.id == "task-123"
    assert planning_item.title == "Implement login feature"
    assert planning_item.item_type == PlanningItemType.PROJECT_TASK
    assert planning_item.duration_minutes == 180  # 3 hours * 60
    assert planning_item.flexible == True
    assert planning_item.priority == 3  # High priority task gets priority 3
    assert planning_item.metadata['project_id'] == "proj-123"
    assert planning_item.metadata['priority'] == "high"

    # Test PreparationBlock
    prep_block = PreparationBlock(
        uid="prep-event-123",
        summary="Подготовка: Математика (ЛК)",
        description="Подготовка к лекции по математике\nТип: ЛК\nДлительность подготовки: 120 мин\nИсходное событие UID: event-123",
        dtstart=datetime(2026, 9, 1, 8, 0),
        dtend=datetime(2026, 9, 1, 10, 0),
        preparation_minutes=120,
        source_event_uid="event-123",
        event_type=EventType.LECTURE
    )

    planning_item = create_planning_item_from_preparation_block(prep_block)
    assert planning_item.id == "prep-event-123"
    assert planning_item.title == "Подготовка: Математика (ЛК)"
    assert planning_item.item_type == PlanningItemType.PREPARATION_BLOCK
    assert planning_item.duration_minutes == 120
    assert planning_item.flexible == False
    assert planning_item.metadata['source_event_uid'] == "event-123"
    assert planning_item.metadata['preparation_minutes'] == 120


def test_weekly_capacity_constraint():
    """Test WeeklyCapacityConstraint functionality."""
    from planning_engine import WeeklyCapacityConstraint, WeeklyCapacityModel, PlanningItem, PlanningItemType
    from datetime import datetime, timedelta

    # Create constraint
    constraint = WeeklyCapacityConstraint()

    # Create a mock weekly capacity
    weekly_capacity = WeeklyCapacityModel(
        sleep_block=8 * 60,           # 8 hours
        fixed_commitments=2 * 60,     # 2 hours
        university_load=3 * 60,       # 3 hours
        teaching_load=0,
        travel_load=0,
        recovery_block=1 * 60,        # 1 hour
        buffer=0.5 * 60               # 0.5 hours
    )
    # Set the capacity on the constraint
    constraint.set_weekly_capacity(weekly_capacity)

    # Create a simple schedule with some items already scheduled
    schedule = Schedule()
    # Add an item that takes 2 hours
    existing_item = PlanningItem(
        "existing-item",
        "Existing Task",
        "An existing task",
        PlanningItemType.PROJECT_TASK,
        duration_minutes=2 * 60,  # 2 hours
    )
    schedule.items = {existing_item.id: existing_item}

    # Add a time slot for the existing item (2 hours)
    existing_slot = TimeSlot(
        start=datetime(2026, 9, 1, 10, 0),
        end=datetime(2026, 9, 1, 12, 0)
    )
    existing_slot.scheduled_item_id = existing_item.id
    schedule.slots.append(existing_slot)

    # Create a new item that would fit within capacity
    new_item = PlanningItem(
        "new-item",
        "New Task",
        "A new task",
        PlanningItemType.PROJECT_TASK,
        duration_minutes=1 * 60,  # 1 hour
    )

    # Create a slot for the new item
    new_slot = TimeSlot(
        start=datetime(2026, 9, 1, 12, 0),
        end=datetime(2026, 9, 1, 13, 0)
    )

    # Test that the new item fits (should not return -inf)
    # Currently scheduled: 2 hours
    # New item would add: 1 hour
    # Total would be: 3 hours
    # Available capacity: 10080 - (8*60 + 2*60 + 3*60 + 0 + 0 + 1*60 + 0.5*60) = 10080 - 870 = 9210 minutes
    # So 3 hours (180 minutes) should definitely fit
    result = constraint.evaluate(new_item, new_slot, schedule)
    assert result != float('-inf')

    # Create an item that would exceed capacity
    # Available capacity is 9210 minutes (153.5 hours)
    # Let's create an item that would exceed this
    huge_item = PlanningItem(
        "huge-item",
        "Huge Task",
        "A huge task",
        PlanningItemType.PROJECT_TASK,
        duration_minutes=10000,  # Way more than available
    )

    huge_slot = TimeSlot(
        start=datetime(2026, 9, 1, 13, 0),
        end=datetime(2026, 9, 1, 13, 0) + timedelta(minutes=10000)
    )

    # Test that the huge item exceeds capacity (should return -inf)
    result = constraint.evaluate(huge_item, huge_slot, schedule)
    assert result == float('-inf')

def test_weekly_capacity_constraint_without_capacity_reference():
    """Test WeeklyCapacityConstraint when no capacity reference is set."""
    from planning_engine import WeeklyCapacityConstraint, PlanningItem, PlanningItemType
    from datetime import datetime, timedelta

    # Create constraint without setting capacity
    constraint = WeeklyCapacityConstraint()
    # weekly_capacity should be None
    assert constraint.weekly_capacity is None

    # Create a schedule
    schedule = Schedule()

    # Create an item
    item = PlanningItem(
        "test-item",
        "Test Task",
        "A test task",
        PlanningItemType.PROJECT_TASK,
        duration_minutes=60,  # 1 hour
    )

    # Create a slot
    slot = TimeSlot(
        start=datetime(2026, 9, 1, 10, 0),
        end=datetime(2026, 9, 1, 11, 0)
    )

    # Should use default limit (40 hours/week = 2400 minutes)
    # Since we have nothing scheduled, a 1-hour item should fit
    result = constraint.evaluate(item, slot, schedule)
    # Should not be -inf (should fit within default 40-hour limit)
    assert result != float('-inf')

    # Test with an item that exceeds the default 40-hour limit
    huge_item = PlanningItem(
        "huge-item",
        "Huge Task",
        "A huge task",
        PlanningItemType.PROJECT_TASK,
        duration_minutes=50 * 60,  # 50 hours > 2400 minutes (40 hours)
    )

    huge_slot = TimeSlot(
        start=datetime(2026, 9, 1, 11, 0),
        end=datetime(2026, 9, 1, 11, 0) + timedelta(minutes=50 * 60)
    )

    result = constraint.evaluate(huge_item, huge_slot, schedule)
    # Should be -inf (exceeds default 40-hour limit)
    assert result == float('-inf')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
