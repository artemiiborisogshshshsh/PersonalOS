"""
Debug script to understand scheduling issues.
"""

from datetime import datetime, timedelta
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType, Schedule

def debug_simple_scheduling():
    """Debug simple scheduling to see what's going wrong."""
    engine = PlanningEngine()
    start_time = datetime(2026, 9, 1, 9, 0)
    end_time = datetime(2026, 9, 1, 17, 0)

    # Create several simple items - all 30 minutes to match slot size
    items = [
        PlanningItem(
            "item1", "Task 1", "First task", PlanningItemType.PROJECT_TASK,
            duration_minutes=30,
            earliest_start=start_time,
            latest_end=end_time,
            priority=2
        ),
        PlanningItem(
            "item2", "Task 2", "Second task", PlanningItemType.PROJECT_TASK,
            duration_minutes=30,
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

    print(f"Planning horizon: {start_time} to {end_time}")
    print(f"Number of items: {len(items)}")
    for item in items:
        print(f"  {item.id}: {item.title} ({item.duration_minutes} min, priority {item.priority})")

    # Generate time slots to see what we're working with
    slots = engine.generate_time_slots(start_time, end_time, granularity_minutes=30)
    print(f"Number of time slots: {len(slots)}")
    slot_strings = [f'{s.start.strftime("%H:%M")}-{s.end.strftime("%H:%M")}' for s in slots[:5]]
    print(f"First few slots: {slot_strings}")

    # Try scheduling with debug info
    print("\nAttempting to schedule items...")
    schedule = Schedule()
    schedule.items = {item.id: item for item in items}

    # Sort items by priority and constraint tightness
    sorted_items = sorted(items, key=lambda x: (
        -x.priority,  # Higher priority first
        -len(x.dependencies),  # More dependencies first (harder to place)
        x.duration_minutes  # Shorter duration first (easier to place)
    ))

    print(f"Order of scheduling: {[item.id for item in sorted_items]}")

    # Try to schedule each item
    for item in sorted_items:
        print(f"\nTrying to schedule {item.id}: {item.title}")
        best_slot = None
        best_score = float('-inf')

        # Find best slot for this item
        for i, slot in enumerate(slots):
            # Skip if slot is already taken
            if slot.scheduled_item_id is not None:
                continue

            # Evaluate this slot for the item
            score = engine.evaluate_item_slot(item, slot, schedule)

            if i < 5:  # Debug first few slots
                print(f"  Slot {i} ({slot.start.strftime('%H:%M')}-{slot.end.strftime('%H:%M')}): score = {score}")

            if score > best_score:
                best_score = score
                best_slot = slot

        print(f"  Best slot score: {best_score}")
        if best_slot and best_score != float('-inf'):
            best_slot.scheduled_item_id = item.id
            best_slot.score = best_score
            schedule.slots.append(best_slot)
            print(f"  -> Scheduled in {best_slot.start.strftime('%H:%M')}-{best_slot.end.strftime('%H:%M')}")
        else:
            print(f"  -> FAILED TO SCHEDULE")
            schedule.unscheduled_items.append(item)

    print(f"\nResults:")
    print(f"Scheduled items: {len(schedule.get_scheduled_items())}")
    print(f"Unscheduled items: {len(schedule.unscheduled_items)}")

    if schedule.unscheduled_items:
        print("Unscheduled items:")
        for item in schedule.unscheduled_items:
            print(f"  {item.id}: {item.title}")

    print("Scheduled items:")
    for item in schedule.get_scheduled_items():
        # Find when it was scheduled
        scheduled_time = None
        for slot in schedule.slots:
            if slot.scheduled_item_id == item.id:
                scheduled_time = f"{slot.start.strftime('%H:%M')}-{slot.end.strftime('%H:%M')}"
                break
        print(f"  {item.id}: {item.title} at {scheduled_time}")

if __name__ == "__main__":
    debug_simple_scheduling()