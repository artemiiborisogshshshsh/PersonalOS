"""
Verification script for multi-slot scheduling.
"""

from datetime import datetime, timedelta
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType

def verify_multislot_scheduling():
    """Verify that multi-slot scheduling works correctly."""
    print("=== Multi-Slot Scheduling Verification ===\n")

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

    # Schedule items
    schedule = engine.schedule_items(items, start_time, end_time, granularity_minutes=30)

    print(f"Planning horizon: {start_time.strftime('%H:%M')} to {end_time.strftime('%H:%M')}")
    print(f"Time slot granularity: 30 minutes")
    print(f"Number of time slots: {len([s for s in engine.generate_time_slots(start_time, end_time, 30)])}")
    print()

    # Verify all items are scheduled
    assert len(schedule.unscheduled_items) == 0, f"Some items were not scheduled: {[i.id for i in schedule.unscheduled_items]}"
    print(f"✓ All {len(items)} items were scheduled successfully")

    # Get scheduled items and verify their actual time allocation
    scheduled_items = schedule.get_scheduled_items()
    print(f"\nDetailed allocation check:")

    total_allocated_minutes = 0
    for item in scheduled_items:
        # Find all slots allocated to this item
        item_slots = [slot for slot in schedule.slots if slot.scheduled_item_id == item.id]

        if item_slots:
            # Calculate actual allocated time from the slots
            allocated_start = min(slot.start for slot in item_slots)
            allocated_end = max(slot.end for slot in item_slots)
            allocated_duration = (allocated_end - allocated_start).total_seconds() / 60

            # Count how many slots were allocated
            slots_allocated = len(item_slots)
            expected_slots = (item.duration_minutes + 29) // 30  # Ceiling division

            print(f"  {item.id}: {item.title}")
            print(f"    Requested duration: {item.duration_minutes} min")
            print(f"    Allocated duration: {allocated_duration:.0f} min ({slots_allocated} slots)")
            print(f"    Expected slots: {expected_slots}")
            print(f"    Actual time: {allocated_start.strftime('%H:%M')} - {allocated_end.strftime('%H:%M')}")

            # Verify that we allocated at least the requested time (might be more due to slot granularity)
            assert allocated_duration >= item.duration_minutes, f"Item {item.id} got less time than requested"

            total_allocated_minutes += allocated_duration
        else:
            print(f"  {item.id}: {item.title} - ERROR: No slots found!")
            assert False, f"No slots found for item {item.id}"

    # Check for overlaps
    print(f"\nOverlap check:")
    overlaps = False
    for i, item1 in enumerate(scheduled_items):
        for j, item2 in enumerate(scheduled_items[i+1:], i+1):
            # Get time spans for both items
            slots1 = [slot for slot in schedule.slots if slot.scheduled_item_id == item1.id]
            slots2 = [slot for slot in schedule.slots if slot.scheduled_item_id == item2.id]

            if slots1 and slots2:
                start1 = min(slot.start for slot in slots1)
                end1 = max(slot.end for slot in slots1)
                start2 = min(slot.start for slot in slots2)
                end2 = max(slot.end for slot in slots2)

                # Check for overlap: not (end1 <= start2 or end2 <= start1)
                overlap = not (end1 <= start2 or end2 <= start1)
                if overlap:
                    print(f"  OVERLAP: {item1.id} [{start1.strftime('%H:%M')}-{end1.strftime('%H:%M')}] and {item2.id} [{start2.strftime('%H:%M')}-{end2.strftime('%H:%M')}]")
                    overlaps = True

    if not overlaps:
        print(f"  ✓ No overlaps detected")
    else:
        print(f"  ✗ Overlaps detected!")
        assert False, "Overlaps found in schedule"

    # Summary
    print(f"\nSummary:")
    print(f"  Total items: {len(items)}")
    print(f"  Successfully scheduled: {len(scheduled_items)}")
    print(f"  Failed to schedule: {len(schedule.unscheduled_items)}")
    print(f"  Total time allocated: {total_allocated_minutes:.0f} minutes")
    print(f"  Available time: {(end_time - start_time).total_seconds() / 60:.0f} minutes")
    if (end_time - start_time).total_seconds() > 0:
        utilization = (total_allocated_minutes / ((end_time - start_time).total_seconds() / 60)) * 100
        print(f"  Time utilization: {utilization:.1f}%")

    print(f"\n✓ All verification checks passed!")

if __name__ == "__main__":
    verify_multislot_scheduling()