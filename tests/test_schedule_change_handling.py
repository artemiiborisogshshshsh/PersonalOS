"""Safety tests for university schedule reconciliation."""

from datetime import datetime, timedelta
from unittest.mock import Mock

from models import (
    AttendanceConfidence,
    EventType,
    MatchType,
    PersonalEventState,
    PersonalUniversityEvent,
    UniversityEvent,
    UniversityEventStatus,
)
from services.attendance_service import (
    AttendanceRuleService,
    UniversityEventChangeType,
)
from services.university_service import UniversityService


BASE_START = datetime(2026, 9, 1, 10, 0)


def university_event(
    uid: str = "university-1",
    summary: str = "Математика (ЛК)",
    start: datetime = BASE_START,
    end: datetime | None = None,
    location: str = "Аудитория 101",
    status: UniversityEventStatus = UniversityEventStatus.CONFIRMED,
) -> UniversityEvent:
    return UniversityEvent(
        uid=uid,
        summary=summary,
        description="Преподаватель: Иванов И.И.; группа 8И41",
        location=location,
        dtstart=start,
        dtend=end or start + timedelta(minutes=90),
        event_type=EventType.LECTURE,
        is_group_event=True,
        status=status,
    )


def personal_event(source: UniversityEvent) -> PersonalUniversityEvent:
    return PersonalUniversityEvent(
        id="personal-1",
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


def change_of_type(records, change_type):
    return next(record for record in records if record.change_type == change_type)


def test_cyrillic_courses_have_distinct_stable_ids():
    service = AttendanceRuleService()
    math = university_event(summary="Математика (ЛК)")
    physics = university_event(uid="university-2", summary="Физика (ЛК)")

    assert service.compute_stable_id(math) != service.compute_stable_id(physics)


def test_repeated_sessions_are_not_collapsed_during_detection():
    service = AttendanceRuleService()
    first = university_event(uid="first")
    second = university_event(uid="second", start=BASE_START + timedelta(days=1))
    moved_second = university_event(
        uid="second",
        start=BASE_START + timedelta(days=1, hours=2),
    )

    records = service.detect_schedule_changes([first, second], [first, moved_second])

    assert len(records) == 2
    assert {record.change_type for record in records} == {
        UniversityEventChangeType.UNCHANGED,
        UniversityEventChangeType.MOVED,
    }


def test_move_updates_personal_event_and_requests_preparation_reschedule():
    service = AttendanceRuleService()
    old = university_event()
    new = university_event(
        start=BASE_START + timedelta(hours=2),
        location="Аудитория 202",
    )
    personal = personal_event(old)

    changes = service.detect_changes([old], [new])
    affected = service.process_removed_and_changed_events(changes, [personal])

    assert affected == [personal]
    assert personal.state == PersonalEventState.MOVED
    assert personal.start_time == new.dtstart
    assert personal.location == "Аудитория 202"
    assert personal.metadata['preparation_action'] == 'reschedule'

    planning_item = UniversityService().create_planning_item_from_personal_event(personal)
    assert planning_item.metadata['course'] == 'Математика'
    assert planning_item.metadata['session_type'] == 'lecture'
    assert planning_item.metadata['stable_id']
    assert planning_item.metadata['state'] == 'moved'
    assert planning_item.metadata['is_moved'] is True
    assert planning_item.metadata['move_information']['old_start'] == old.dtstart.isoformat()
    assert planning_item.metadata['move_information']['new_start'] == new.dtstart.isoformat()


def test_location_change_does_not_change_attendance_state():
    service = AttendanceRuleService()
    old = university_event()
    new = university_event(location="Аудитория 303")
    personal = personal_event(old)

    service.process_removed_and_changed_events(
        service.detect_changes([old], [new]), [personal]
    )

    assert personal.location == "Аудитория 303"
    assert personal.state == PersonalEventState.CONFIRMED


def test_explicit_source_cancellation_is_immediate():
    service = AttendanceRuleService()
    old = university_event()
    cancelled = university_event(status=UniversityEventStatus.CANCELLED)
    personal = personal_event(old)

    records = service.detect_schedule_changes([old], [cancelled])
    assert change_of_type(records, UniversityEventChangeType.CANCELLED)

    service.apply_schedule_changes(records, [personal])

    assert personal.state == PersonalEventState.CANCELLED
    assert personal.metadata['preparation_action'] == 'cancel'


def test_disappearance_uses_grace_period_then_cancels():
    service = AttendanceRuleService()
    old = university_event()
    personal = personal_event(old)
    changes = service.detect_changes([old], [])

    service.process_removed_and_changed_events(changes, [personal])
    assert personal.state == PersonalEventState.POSSIBLY_CANCELLED
    assert personal.metadata['missing_sync_count'] == 1

    service.process_removed_and_changed_events(changes, [personal])
    assert personal.state == PersonalEventState.CANCELLED
    assert personal.metadata['missing_sync_count'] == 2


def test_event_reappearance_during_grace_restores_previous_state():
    service = AttendanceRuleService()
    source = university_event()
    personal = personal_event(source)

    service.process_removed_and_changed_events(
        service.detect_changes([source], []), [personal]
    )
    service.process_removed_and_changed_events(
        service.detect_changes([source], [source]), [personal]
    )

    assert personal.state == PersonalEventState.CONFIRMED
    assert 'missing_sync_count' not in personal.metadata


def test_replacement_requires_review_and_preserves_original_link():
    service = AttendanceRuleService()
    old = university_event(summary="Математика (ЛК)")
    replacement = university_event(summary="Физика (ЛК)")
    personal = personal_event(old)

    changes = service.detect_changes([old], [replacement])
    service.process_removed_and_changed_events(changes, [personal])

    assert personal.state == PersonalEventState.NEEDS_REVIEW
    assert personal.university_event_uid == old.uid
    assert personal.title == old.summary
    assert personal.metadata['replacement_candidate']['title'] == replacement.summary


def test_regenerated_uid_is_transferred_for_same_logical_event():
    service = AttendanceRuleService()
    old = university_event(uid="old-uid")
    regenerated = university_event(uid="new-uid")
    personal = personal_event(old)

    changes = service.detect_changes([old], [regenerated])
    service.process_removed_and_changed_events(changes, [personal])

    assert personal.university_event_uid == "new-uid"
    assert personal.state == PersonalEventState.CONFIRMED
    assert personal.metadata['schedule_change_history'][-1]['type'] == 'updated'


def test_reconciliation_pipeline_creates_new_event_and_reschedules_moved_preparation():
    preparation = Mock()
    service = AttendanceRuleService(preparation_integration_service=preparation)
    old = university_event(uid="existing")
    moved = university_event(uid="existing", start=BASE_START + timedelta(hours=1))
    added = university_event(
        uid="added",
        summary="Физика (ЛК)",
        start=BASE_START + timedelta(days=1),
        location="Аудитория 404",
    )
    personal = personal_event(old)
    personal.preparation_block_uid = "prep-existing"
    personal.task_uid = "task-existing"

    result = service.reconcile_schedule_snapshots(
        [old], [moved, added], [personal]
    )

    assert personal.state == PersonalEventState.MOVED
    assert len(result['created']) == 1
    assert result['created'][0].university_event_uid == "added"
    assert result['created'][0].location == "Аудитория 404"
    preparation.integrate_preparation.assert_any_call(personal)
