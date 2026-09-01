#!/usr/bin/env python3
"""
Demonstration of the explainable scoring feature in the Planning Engine.
Shows how each constraint and scoring function contributes to the final score.
"""

from datetime import datetime, timedelta
from planning_engine import (
    PlanningEngine, PlanningItem, PlanningItemType, TimeSlot, Schedule,
    TimeWindowConstraint, DependencyConstraint
)
from models import UniversityEvent, EventType


def demo_explainable_scoring():
    """Demonstrate explainable scoring with detailed breakdown."""
    print("=" * 80)
    print("EXPLAINABLE SCORING DEMONSTRATION")
    print("=" * 80)

    # Create a planning engine
    engine = PlanningEngine()

    # Create a schedule with some existing items
    schedule = Schedule()

    # Add a university event that's already scheduled
    uni_event = UniversityEvent(
        uid="math-lecture-101",
        summary="Математический анализ (Лекция)",
        description="Лекция по математическому анализу",
        location="Аудитория 205",
        dtstart=datetime(2026, 9, 1, 10, 0),
        dtend=datetime(2026, 9, 1, 12, 0),
        event_type=EventType.LECTURE,
        is_group_event=True
    )

    # Create a planning item for the university event
    uni_item = PlanningItem(
        id="math-lecture-101",
        title="Математический анализ (Лекция)",
        description="Лекция по математическому анализу",
        item_type=PlanningItemType.UNIVERSITY_EVENT,
        preferred_start=datetime(2026, 9, 1, 10, 0),
        preferred_end=datetime(2026, 9, 1, 12, 0),
        duration_minutes=120,
        earliest_start=datetime(2026, 9, 1, 10, 0),
        latest_end=datetime(2026, 9, 1, 12, 0),
        flexible=False,
        priority=2,
        metadata={
            'event_type': 'Лекция',
            'location': 'Аудитория 205',
            'is_group_event': True
        }
    )

    # Schedule the university event
    uni_slot = TimeSlot(datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 12, 0))
    uni_slot.scheduled_item_id = "math-lecture-101"
    schedule.slots.append(uni_slot)
    schedule.items = {"math-lecture-101": uni_item}

    print(f"Scheduled university event: {uni_item.title}")
    print(f"Time: {uni_slot.start.strftime('%H:%M')} - {uni_slot.end.strftime('%H:%M')}")
    print()

    # Create a preparation item for the university event
    prep_item = PlanningItem(
        id="prep-math-101",
        title="Подготовка к математическому анализу",
        description="Подготовка к лекции по математическому анализу",
        item_type=PlanningItemType.PREPARATION_BLOCK,
        preferred_start=datetime(2026, 9, 1, 8, 0),
        preferred_end=datetime(2026, 9, 1, 10, 0),
        duration_minutes=120,
        earliest_start=datetime(2026, 9, 1, 7, 0),
        latest_end=datetime(2026, 9, 1, 11, 0),
        flexible=True,
        priority=2,
        metadata={
            'source_event_uid': 'math-lecture-101',
            'preparation_minutes': 120
        }
    )

    print(f"Preparation item to schedule: {prep_item.title}")
    print(f"Duration: {prep_item.duration_minutes} minutes")
    print(f"Preferred time: {prep_item.preferred_start.strftime('%H:%M')} - {prep_item.preferred_end.strftime('%H:%M')}")
    print(f"Allowed time window: {prep_item.earliest_start.strftime('%H:%M')} - {prep_item.latest_end.strftime('%H:%M')}")
    print()

    # Evaluate different time slots for the preparation item
    test_slots = [
        ("Нарушение окна времени (слишком рано)", TimeSlot(datetime(2026, 9, 1, 6, 0), datetime(2026, 9, 1, 7, 0))),
        ("Нарушение времени сна", TimeSlot(datetime(2026, 9, 1, 1, 0), datetime(2026, 9, 1, 3, 0))),
        ("Идеальное время (прямо перед лекцией)", TimeSlot(datetime(2026, 9, 1, 8, 0), datetime(2026, 9, 1, 10, 0))),
        ("Время после лекции", TimeSlot(datetime(2026, 9, 1, 12, 0), datetime(2026, 9, 1, 14, 0))),
        ("Поздний вечер", TimeSlot(datetime(2026, 9, 1, 20, 0), datetime(2026, 9, 1, 22, 0))),
    ]

    print("SCORING BREAKDOWN FOR DIFFERENT TIME SLOTS:")
    print("-" * 80)

    for slot_name, slot in test_slots:
        print(f"\n{slot_name}: {slot.start.strftime('%H:%M')} - {slot.end.strftime('%H:%M')}")
        print("-" * 50)

        # Evaluate the item in this slot
        score = engine.evaluate_item_slot(prep_item, slot, schedule)

        if score == float('-inf'):
            print("  RESULT: HARD CONSTRAINT VIOLATION (score = -∞)")
            print("  This time slot is NOT allowed due to hard constraints.")
        else:
            print(f"  RESULT: Total score = {score:.2f}")
            print("  Contribution breakdown:")

            # Sort breakdown by absolute value (most impactful first)
            sorted_breakdown = sorted(
                slot.score_breakdown.items(),
                key=lambda x: abs(x[1]) if x[1] != float('-inf') else float('inf'),
                reverse=True
            )

            for component, component_score in sorted_breakdown:
                if component_score == float('-inf'):
                    print(f"    {component}: -∞ (HARD CONSTRAINT VIOLATION)")
                else:
                    print(f"    {component}: {component_score:+.2f}")

    print("\n" + "=" * 80)
    print("DEMONSTRATION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    demo_explainable_scoring()