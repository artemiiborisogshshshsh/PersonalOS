"""Synthetic MVP acceptance path; no credentials or network are used."""

from datetime import datetime, timedelta

from models import EventType, PersonalAttendanceRule, PersonalEventState, UniversityEvent
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType
from services.attendance_service import AttendanceRuleService
from services.calendar.weekly_plan_projection_service import (
    ProjectionAction,
    WeeklyPlanCalendarProjectionService,
)
from services.university_snapshot_store import UniversitySnapshotStore
from services.weekly_plan_service import CommitmentType, FixedCommitment, WeeklyPlan, WeeklyPlanPipeline


ZONELESS_WEEK = datetime(2026, 9, 7, 9)


class RecordingCalendar:
    def __init__(self):
        self.events = {}
        self.inserts = 0
        self.updates = 0

    def _get_or_create_calendar(self, _summary):
        return 'synthetic-calendar'

    def _get_events_in_range(self, _start, _end):
        return list(self.events.values())

    def _insert_event(self, data, **_kwargs):
        identifier = f'event-{len(self.events) + 1}'
        self.events[data.uid] = {
            'id': identifier, 'iCalUID': data.uid,
            'description': data.description,
        }
        self.inserts += 1
        return identifier

    def _update_event(self, _calendar, identifier, data, **_kwargs):
        self.events[data.uid] = {
            'id': identifier, 'iCalUID': data.uid,
            'description': data.description,
        }
        self.updates += 1
        return identifier

    def _delete_event(self, _calendar, identifier):
        for uid, event in list(self.events.items()):
            if event['id'] == identifier:
                del self.events[uid]
        return True


def lab(uid='lab-1', start=ZONELESS_WEEK + timedelta(days=2, hours=5)):
    return UniversityEvent(
        uid=uid, summary='ОС (ЛБ)', description='Группа 8И41', location='201',
        dtstart=start, dtend=start + timedelta(minutes=90),
        event_type=EventType.LAB, is_group_event=True,
    )


def test_synthetic_mvp_path_preserves_identity_and_projection_is_idempotent(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    rule = PersonalAttendanceRule(
        id='student', description='chosen lab', metadata={
            'attendance_preferences': {'ОС': {
                'labs_enabled': True,
                'lab_slots': [f'{lab().dtstart.weekday()}:{lab().dtstart:%H:%M}'],
            }},
        },
    )
    attendance = AttendanceRuleService(rule)

    first = store.reconcile([lab()], attendance).personal_events[0]
    moved = store.reconcile([
        lab(start=lab().dtstart + timedelta(hours=2)),
    ], attendance).personal_events[0]

    assert first.state == PersonalEventState.CONFIRMED
    assert moved.id == first.id
    assert moved.state == PersonalEventState.MOVED

    calendar = RecordingCalendar()
    projector = WeeklyPlanCalendarProjectionService(calendar)
    preparation = PlanningItem(
        id='prep:lab-1', title='Подготовка к ОС (ЛБ)', description='',
        item_type=PlanningItemType.PREPARATION_BLOCK, duration_minutes=60,
        earliest_start=ZONELESS_WEEK,
        latest_end=ZONELESS_WEEK + timedelta(days=2, hours=4),
        flexible=True, priority=4, metadata={'activity_type': 'preparation'},
    )
    lesson = FixedCommitment(
        id=f'university:{moved.id}', title=moved.title,
        start=moved.start_time, end=moved.end_time,
        commitment_type=CommitmentType.UNIVERSITY,
    )
    plan = WeeklyPlan(
        week_start=ZONELESS_WEEK,
        week_end=ZONELESS_WEEK + timedelta(days=4),
        items=[preparation], fixed_commitments=[lesson],
    )
    pipeline = WeeklyPlanPipeline(PlanningEngine(num_candidates=4), projector=projector)
    pipeline.run_until_selection(plan, granularity_minutes=15)
    pipeline.approve(plan, approved_by='synthetic-user')
    pipeline.commit(plan)

    assert calendar.inserts == 2
    second = projector.project(plan)
    assert set(second.actions.values()) == {ProjectionAction.NOOP}
    assert calendar.inserts == 2
    assert calendar.updates == 0
