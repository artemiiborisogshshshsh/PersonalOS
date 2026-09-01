from datetime import datetime, timedelta
from unittest.mock import Mock

from models import (
    AttendanceConfidence,
    EventType,
    MatchType,
    PersonalEventState,
    PersonalUniversityEvent,
    UniversityEvent,
)
from services.attendance_service import AttendanceRuleService
from services.sync_coordinator import (
    RegisteredScheduleSource,
    ScheduleSyncCoordinator,
    SyncCadence,
)


START = datetime(2026, 9, 1, 10, 0)


def source_event(start=START):
    return UniversityEvent(
        uid='source-1',
        summary='Математика (ЛК)',
        description='Преподаватель: Иванов И.И.',
        location='101',
        dtstart=start,
        dtend=start + timedelta(hours=1),
        event_type=EventType.LECTURE,
        is_group_event=True,
    )


def personal_event(source):
    return PersonalUniversityEvent(
        id='personal-1',
        title=source.summary,
        description=source.description,
        location=source.location,
        start_time=source.dtstart,
        end_time=source.dtend,
        university_event_uid=source.uid,
        state=PersonalEventState.CONFIRMED,
        match_confidence=AttendanceConfidence.HIGH,
        match_type=MatchType.EXACT,
    )


def test_daily_and_weekly_due_calculation():
    coordinator = ScheduleSyncCoordinator(AttendanceRuleService())
    now = datetime(2026, 9, 1, 8, 0)
    assert coordinator.is_due('university', SyncCadence.DAILY, now)
    coordinator.last_sync_at['university'] = now
    assert not coordinator.is_due('university', SyncCadence.DAILY, now + timedelta(hours=23))
    assert coordinator.is_due('university', SyncCadence.DAILY, now + timedelta(days=1))


def test_replan_runs_for_move_but_not_identical_snapshot():
    replan = Mock()
    coordinator = ScheduleSyncCoordinator(AttendanceRuleService(), replan)
    old = source_event()
    moved = source_event(START + timedelta(hours=2))
    personal = personal_event(old)

    first = coordinator.sync('university', [old], [moved], [personal])
    second = coordinator.sync('university', [moved], [moved], [personal])

    assert first.changed and first.replanned
    assert not second.changed and not second.replanned
    replan.assert_called_once()


def test_repeated_disappearance_confirms_cancellation_after_grace():
    coordinator = ScheduleSyncCoordinator(AttendanceRuleService())
    old = source_event()
    personal = personal_event(old)

    coordinator.sync('university', [old], [], [personal])
    assert personal.state == PersonalEventState.POSSIBLY_CANCELLED

    second = coordinator.sync('university', [], [], [personal])
    assert second.changed and second.replanned
    assert personal.state == PersonalEventState.CANCELLED


def test_all_registered_sources_sync_on_cadence_with_one_real_change_replan():
    replan = Mock()
    coordinator = ScheduleSyncCoordinator(AttendanceRuleService(), replan)
    university_snapshot = [source_event()]
    tutoring_snapshot = [{'id': 'lesson-1', 'start': START.isoformat()}]
    university_loader = Mock(side_effect=lambda: university_snapshot)
    tutoring_loader = Mock(side_effect=lambda: tutoring_snapshot)
    coordinator.register_source(RegisteredScheduleSource(
        'university', SyncCadence.DAILY, university_loader, university_source=True
    ))
    coordinator.register_source(RegisteredScheduleSource(
        'tutoring', SyncCadence.WEEKLY, tutoring_loader
    ))
    now = datetime(2026, 9, 1, 8, 0)
    personal = []

    first = coordinator.sync_due_sources(personal, now)
    assert set(first) == {'university', 'tutoring'}
    assert first['university'].changed
    assert first['tutoring'].changed
    replan.assert_called_once()

    # Nothing is due yet, so loaders and replanning are untouched.
    assert coordinator.sync_due_sources(personal, now + timedelta(hours=1)) == {}
    assert university_loader.call_count == 1
    assert tutoring_loader.call_count == 1

    # Daily university sync is identical and must not replan.
    daily = coordinator.sync_due_sources(personal, now + timedelta(days=1))
    assert set(daily) == {'university'}
    assert not daily['university'].changed
    replan.assert_called_once()

    # A real tutoring change on its weekly cadence triggers one aggregate replan.
    tutoring_snapshot.append({'id': 'lesson-2', 'start': START.isoformat()})
    weekly = coordinator.sync_due_sources(personal, now + timedelta(days=7))
    assert weekly['tutoring'].changed
    assert not weekly['university'].changed
    assert replan.call_count == 2
    assert replan.call_args.args[0]['sources'] == ['tutoring']
