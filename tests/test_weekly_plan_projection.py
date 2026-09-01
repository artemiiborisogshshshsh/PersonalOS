from datetime import datetime, timedelta
from unittest.mock import Mock

from planning_engine import PlanningItem, PlanningItemType, Schedule, TimeSlot
from services.calendar.weekly_plan_projection_service import (
    ProjectionAction,
    WeeklyPlanCalendarProjectionService,
)
from services.weekly_plan_service import PlanCandidate, WeeklyPlan


START = datetime(2026, 9, 7, 9, 0)


def selected_plan():
    project = PlanningItem(
        id='project-1',
        title='Проект',
        description='Глубокая работа',
        item_type=PlanningItemType.PROJECT_TASK,
        duration_minutes=60,
        metadata={'domain': 'projects'},
    )
    recovery = PlanningItem(
        id='recovery-1',
        title='Восстановление',
        description='Отдых',
        item_type=PlanningItemType.CUSTOM,
        duration_minutes=30,
        metadata={'activity_type': 'recovery'},
    )
    schedule = Schedule(
        items={project.id: project, recovery.id: recovery},
        slots=[
            TimeSlot(START, START + timedelta(minutes=30), scheduled_item_id=project.id),
            TimeSlot(START + timedelta(minutes=30), START + timedelta(hours=1), scheduled_item_id=project.id),
            TimeSlot(START + timedelta(hours=1), START + timedelta(hours=1, minutes=30), scheduled_item_id=recovery.id),
        ],
    )
    plan = WeeklyPlan(START, START + timedelta(days=7), [project, recovery])
    plan.selected_candidate = PlanCandidate(schedule, 'test', 1.0, valid=True)
    return plan


def service_with(existing):
    adapter = Mock()
    adapter._get_or_create_calendar.return_value = 'calendar-id'
    adapter._get_events_in_range.return_value = existing
    adapter._insert_event.return_value = 'new-id'
    adapter._update_event.return_value = 'updated-id'
    return WeeklyPlanCalendarProjectionService(adapter), adapter


def test_full_plan_projection_creates_all_categories():
    service, adapter = service_with([])
    result = service.project(selected_plan())

    assert set(result.actions.values()) == {ProjectionAction.CREATE}
    assert adapter._insert_event.call_count == 2
    descriptions = [call.args[0].description for call in adapter._insert_event.call_args_list]
    assert any('Категория: projects' in description for description in descriptions)
    assert any('Категория: recovery' in description for description in descriptions)


def test_repeated_projection_is_noop_and_stale_projection_is_deleted():
    plan = selected_plan()
    empty_service, _ = service_with([])
    desired = empty_service._desired_events(plan)
    existing = [
        {
            'id': f'google-{index}',
            'iCalUID': uid,
            'description': payload['description'],
        }
        for index, (uid, payload) in enumerate(desired.items())
    ]
    stale_payload = next(iter(desired.values())).copy()
    stale_payload['uid'] = 'personal-os-stale'
    stale_payload['description'] = empty_service._description({
        **stale_payload,
        'description': 'stale',
    })
    existing.append({
        'id': 'google-stale',
        'iCalUID': 'personal-os-stale',
        'description': stale_payload['description'],
    })
    service, adapter = service_with(existing)

    result = service.project(plan)

    assert list(result.actions.values()).count(ProjectionAction.NOOP) == 2
    assert result.actions['personal-os-stale'] == ProjectionAction.DELETE
    adapter._insert_event.assert_not_called()
    adapter._update_event.assert_not_called()
    adapter._delete_event.assert_called_once_with('calendar-id', 'google-stale')


def test_external_event_without_marker_is_conflict():
    plan = selected_plan()
    service, _ = service_with([])
    uid = next(iter(service._desired_events(plan)))
    service, adapter = service_with([{
        'id': 'external-id',
        'iCalUID': uid,
        'description': 'user-edited event without ownership marker',
    }])

    result = service.project(plan)

    assert result.actions[uid] == ProjectionAction.CONFLICT
    adapter._update_event.assert_not_called()
    adapter._delete_event.assert_not_called()


def test_projection_covers_every_personal_schedule_category():
    categories = (
        'university', 'tutoring', 'preparation', 'projects', 'reading',
        'sleep', 'travel', 'recovery', 'buffer',
    )
    items = {}
    slots = []
    for index, category in enumerate(categories):
        item = PlanningItem(
            id=f'item-{category}',
            title=category,
            description=f'{category} block',
            item_type=PlanningItemType.CUSTOM,
            duration_minutes=30,
            metadata={'activity_type': category},
        )
        items[item.id] = item
        slot_start = START + timedelta(minutes=30 * index)
        slots.append(TimeSlot(
            slot_start,
            slot_start + timedelta(minutes=30),
            scheduled_item_id=item.id,
        ))
    schedule = Schedule(items=items, slots=slots)
    plan = WeeklyPlan(START, START + timedelta(days=7), list(items.values()))
    plan.selected_candidate = PlanCandidate(schedule, 'test', 1.0, valid=True)
    service, adapter = service_with([])

    result = service.project(plan)

    assert len(result.actions) == len(categories)
    descriptions = {
        call.args[0].description for call in adapter._insert_event.call_args_list
    }
    for category in categories:
        assert any(f'Категория: {category}' in value for value in descriptions)
