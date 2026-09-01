from datetime import datetime

from planning_engine import PlanningEngine, PlanningItem, PlanningItemType


def test_scheduler_prefers_complete_candidate_over_partial_one():
    start = datetime(2026, 9, 1, 9, 0)
    end = datetime(2026, 9, 1, 17, 0)
    items = [
        PlanningItem(
            'workshop', 'Workshop', '', PlanningItemType.UNIVERSITY_EVENT,
            duration_minutes=120, earliest_start=start, latest_end=end, priority=2,
        ),
        PlanningItem(
            'deep-work', 'Deep Work', '', PlanningItemType.PROJECT_TASK,
            duration_minutes=180, earliest_start=start, latest_end=end, priority=1,
        ),
        PlanningItem(
            'review', 'Code Review', '', PlanningItemType.PROJECT_TASK,
            duration_minutes=45, earliest_start=start, latest_end=end, priority=3,
        ),
    ]

    schedule = PlanningEngine().schedule_items(items, start, end, 30)

    assert schedule.unscheduled_items == []
    assert {item.id for item in schedule.get_scheduled_items()} == {
        'workshop', 'deep-work', 'review'
    }
