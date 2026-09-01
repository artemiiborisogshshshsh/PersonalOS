"""
Simple demo script to show multi-slot scheduling in action.
"""

from datetime import datetime, timedelta
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType

def demo_multislot_scheduling():
    """Demonstrate multi-slot scheduling with items of different durations."""
    print("=== Multi-Slot Scheduling Demo ===\n")

    engine = PlanningEngine()
    start_time = datetime(2026, 9, 1, 9, 0)  # 9:00 AM
    end_time = datetime(2026, 9, 1, 18, 0)   # 6:00 PM

    # Create items with various durations that require multiple slots
    items = [
        PlanningItem(
            "task-1", "Quick Task", "15-minute task", PlanningItemType.PROJECT_TASK,
            duration_minutes=15,
            earliest_start=start_time,
            latest_end=end_time,
            priority=2
        ),
        PlanningItem(
            "task-2", "Medium Task", "1-hour task", PlanningItemType.PROJECT_TASK,
            duration_minutes=60,
            earliest_start=start_time,
            latest_end=end_time,
            priority=3
        ),
        PlanningItem(
            "task-3", "Long Task", "2.5-hour task", PlanningItemType.PROJECT_TASK,
            duration_minutes=150,
            earliest_start=start_time,
            latest_end=end_time,
            priority=1
        ),
        PlanningItem(
            "task-4", "Another Quick Task", "Another 15-minute task", PlanningItemType.PROJECT_TASK,
            duration_minutes=15,
            earliest_start=start_time,
            latest_end=end_time,
            priority=2
        )
    ]

    print(f"Planning horizon: {start_time.strftime('%H:%M')} to {end_time.strftime('%H:%M')} ({((end_time - start_time).total_seconds() / 3600):.1f} hours)")
    print(f"Time slot granularity: 30 minutes")
    print(f"Items to schedule:")
    for item in items:
        slots_needed = (item.duration_minutes + 29) // 30  # Ceiling division
        print(f"  {item.id}: {item.title} ({item.duration_minutes} min = {slots_needed} slots, priority {item.priority})")

    print()

    # Schedule items
    schedule = engine.schedule_items(items, start_time, end_time, granularity_minutes=30)

    print(f"Results:")
    print(f"  Scheduled items: {len(schedule.get_scheduled_items())}")
    print(f"  Unscheduled items: {len(schedule.unscheduled_items)}")

    if schedule.unscheduled_items:
        print(f"  Unscheduled:")
        for item in schedule.unscheduled_items:
            print(f"    {item.id}: {item.title}")

    print(f"  Scheduled:")
    # Get scheduled items with their actual time spans
    scheduled_items = schedule.get_scheduled_items()
    for item in scheduled_items:
        # Find all slots scheduled for this item
        item_slots = [slot for slot in schedule.slots if slot.scheduled_item_id == item.id]
        if item_slots:
            # Calculate overall time span
            min_start = min(slot.start for slot in item_slots)
            max_end = max(slot.end for slot in item_slots)
            duration_minutes = (max_end - min_start).total_seconds() / 60
            print(f"    {item.id}: {item.title} ({min_start.strftime('%H:%M')} - {max_end.strftime('%H:%M')} = {duration_minutes:.0f} min)")

    # Simple overlap check - just make sure we have the right number of items
    if len(schedule.get_scheduled_items()) + len(schedule.unscheduled_items) == len(items):
        print(f"\n  Item count check: PASS (all items accounted for)")
    else:
        print(f"\n  Item count check: FAIL")

    print(f"\n  Schedule efficiency:")
    total_scheduled_minutes = sum(
        (max(slot.end for slot in schedule.slots if slot.scheduled_item_id == item.id) -
         min(slot.start for slot in schedule.slots if slot.scheduled_item_id == item.id)).total_seconds() / 60
        for item in schedule.get_scheduled_items()
    )
    available_minutes = (end_time - start_time).total_seconds() / 60
    if available_minutes > 0:
        efficiency = (total_scheduled_minutes / available_minutes) * 100
        print(f"    Total scheduled time: {total_scheduled_minutes:.0f} min / {available_minutes:.0f} min available = {efficiency:.1f}%")

if __name__ == "__main__":
    demo_multislot_scheduling()