from datetime import datetime, timedelta

import pytest

from models import EventType, PersonalAttendanceRule, PersonalEventState, UniversityEvent
from services.attendance_service import AttendanceRuleService
from services.university_snapshot_store import UniversitySnapshotStore


START = datetime(2026, 9, 7, 10)


def source(uid='math-1', *, title='Математика (ЛК)', start=START, location='101'):
    return UniversityEvent(
        uid=uid, summary=title, description='Группа 8И41. Преподаватель: Иванов И.И.',
        location=location, dtstart=start, dtend=start + timedelta(minutes=90),
        event_type=EventType.LECTURE, is_group_event=True,
    )


def service():
    return AttendanceRuleService(PersonalAttendanceRule(
        id='attendance', description='confirmed mathematics',
        metadata={'attendance_preferences': {'Математика': {'lectures': True}}},
    ))


def test_initial_snapshot_persists_confirmed_personal_event_and_is_idempotent(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')

    first = store.reconcile([source()], service())
    second = store.reconcile([source()], service())

    assert first.personal_events[0].state == PersonalEventState.CONFIRMED
    assert first.personal_events[0].id == 'personal-university:math-1'
    assert second.changed is False
    assert second.created_count == 0
    assert len(second.personal_events) == 1


def test_saved_attendance_choice_promotes_existing_expected_event_without_new_identity(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    undecided = AttendanceRuleService()
    first = store.reconcile([source()], undecided)

    result = store.reconcile([source()], service())

    assert result.personal_events[0].id == first.personal_events[0].id
    assert result.personal_events[0].state == PersonalEventState.CONFIRMED


def test_uid_regeneration_and_move_keep_personal_identity(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    original = store.reconcile([source()], service()).personal_events[0]

    result = store.reconcile([
        source('math-regenerated', start=START + timedelta(hours=2)),
    ], service())

    assert len(result.personal_events) == 1
    event = result.personal_events[0]
    assert event.id == original.id
    assert event.university_event_uid == 'math-regenerated'
    assert event.state == PersonalEventState.MOVED


def test_location_change_updates_existing_personal_event(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    initial = store.reconcile([source()], service()).personal_events[0]

    result = store.reconcile([source(location='202')], service())

    assert result.personal_events[0].id == initial.id
    assert result.personal_events[0].location == '202'
    assert result.personal_events[0].state == PersonalEventState.CONFIRMED


def test_disappearance_then_reappearance_never_creates_a_duplicate(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    store.reconcile([source()], service())

    missing = store.reconcile([source('other', title='Физика (ЛК)', start=START + timedelta(days=1))], service())
    restored = store.reconcile([source()], service())

    original = next(event for event in missing.personal_events if event.id == 'personal-university:math-1')
    assert original.state == PersonalEventState.POSSIBLY_CANCELLED
    restored_math = [event for event in restored.personal_events if event.id == original.id]
    assert len(restored_math) == 1
    assert restored_math[0].state == PersonalEventState.CONFIRMED


def test_ambiguous_replacement_requires_review_and_does_not_create_candidates(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    store.reconcile([source()], service())

    result = store.reconcile([
        source('physics', title='Физика (ЛК)'),
        source('chemistry', title='Химия (ЛК)'),
    ], service())

    assert len(result.personal_events) == 1
    event = result.personal_events[0]
    assert event.id == 'personal-university:math-1'
    assert event.state == PersonalEventState.NEEDS_REVIEW
    assert {item['uid'] for item in event.metadata['replacement_candidates']} == {
        'physics', 'chemistry',
    }


def test_user_confirmed_replacement_preserves_personal_identity_and_is_durable(tmp_path):
    path = tmp_path / 'university_reconciliation.json'
    store = UniversitySnapshotStore(path)
    original = store.reconcile([source()], service()).personal_events[0]
    store.reconcile([
        source('physics', title='Физика (ЛК)'),
        source('chemistry', title='Химия (ЛК)'),
    ], service())

    accepted = store.accept_replacement(original.id, 'physics')

    assert accepted.id == original.id
    assert accepted.university_event_uid == 'physics'
    assert accepted.title == 'Физика (ЛК)'
    assert accepted.state == PersonalEventState.MOVED
    assert accepted.metadata['replacement_confirmed_from_uid'] == 'math-1'
    assert 'replacement_candidates' not in accepted.metadata
    reloaded = UniversitySnapshotStore(path).personal_events()
    assert [(event.id, event.university_event_uid, event.state) for event in reloaded] == [
        (original.id, 'physics', PersonalEventState.MOVED),
    ]


def test_replacement_acceptance_rejects_unknown_or_stale_candidate(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    original = store.reconcile([source()], service()).personal_events[0]
    store.reconcile([
        source('physics', title='Физика (ЛК)'),
        source('chemistry', title='Химия (ЛК)'),
    ], service())

    with pytest.raises(ValueError, match='candidate'):
        store.accept_replacement(original.id, 'unknown')
    with pytest.raises(KeyError, match='Unknown'):
        store.accept_replacement('unknown-event', 'physics')


def test_rejecting_invalid_snapshot_keeps_the_last_verified_state(tmp_path):
    path = tmp_path / 'university_reconciliation.json'
    store = UniversitySnapshotStore(path)
    store.reconcile([source()], service())
    before = path.read_text(encoding='utf-8')

    with pytest.raises(ValueError, match='must contain events'):
        store.reconcile([], service())

    assert path.read_text(encoding='utf-8') == before


def test_snapshot_outside_requested_horizon_keeps_last_verified_state(tmp_path):
    path = tmp_path / 'university_reconciliation.json'
    store = UniversitySnapshotStore(path)
    store.reconcile([source()], service())
    before = path.read_text(encoding='utf-8')

    with pytest.raises(ValueError, match='does not cover'):
        store.reconcile(
            [source(start=START + timedelta(days=30))], service(),
            required_horizon=(START, START + timedelta(days=14)),
        )

    assert path.read_text(encoding='utf-8') == before
