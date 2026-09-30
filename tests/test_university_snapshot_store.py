from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from models import EventType, MatchType, PersonalAttendanceRule, PersonalEventState, UniversityEvent
from services.attendance_service import AttendanceRuleService
from services.attendance_preferences import lab_slot_key
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


def choices(**preferences):
    return AttendanceRuleService(PersonalAttendanceRule(
        id='saved-choices', description='Synthetic attendance choices',
        metadata={'attendance_preferences': preferences},
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


def test_saved_opt_out_demotes_existing_attendance_and_reselection_keeps_identity(tmp_path):
    path = tmp_path / 'university_reconciliation.json'
    store = UniversitySnapshotStore(path)
    attended = store.reconcile([source()], service()).personal_events[0]
    attended.preparation_block_uid = 'linked-preparation'
    attended.task_uid = 'linked-task'
    attended.metadata['manual_note'] = 'keep this user note'
    store._save([source()], [attended])

    opted_out = store.reconcile(
        [source()], choices(Математика={'lectures': False}),
    ).personal_events[0]

    assert opted_out.id == attended.id
    assert opted_out.state == PersonalEventState.EXPECTED
    assert opted_out.match_type is None
    assert opted_out.match_confidence is None
    assert opted_out.preparation_block_uid == 'linked-preparation'
    assert opted_out.task_uid == 'linked-task'
    assert opted_out.metadata['manual_note'] == 'keep this user note'
    assert opted_out.metadata['attendance_choice'] is False
    assert UniversitySnapshotStore(path).personal_events()[0].state == PersonalEventState.EXPECTED

    reselected = store.reconcile([source()], service()).personal_events[0]
    assert reselected.id == attended.id
    assert reselected.state == PersonalEventState.CONFIRMED
    assert reselected.match_type == MatchType.MANUAL
    assert reselected.metadata['manual_note'] == 'keep this user note'


def test_refresh_saved_choice_demotes_confirmed_event_without_source_reconcile(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    confirmed = store.reconcile([source()], service()).personal_events[0]

    refreshed = store.refresh_attendance_choices(
        choices(Математика={'lectures': False}),
    )[0]

    assert refreshed.id == confirmed.id
    assert refreshed.state == PersonalEventState.EXPECTED
    assert refreshed.metadata['attendance_choice'] is False
    assert store.personal_events()[0].state == PersonalEventState.EXPECTED


def test_opt_out_stays_expected_after_uid_regeneration_and_move(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    original = store.reconcile([source()], service()).personal_events[0]

    moved = store.reconcile([
        source('math-new', start=START + timedelta(hours=2)),
    ], choices(Математика={'lectures': False})).personal_events[0]

    assert moved.id == original.id
    assert moved.university_event_uid == 'math-new'
    assert moved.state == PersonalEventState.EXPECTED
    assert moved.metadata['schedule_change_history'][-1]['type'] == 'moved'


def test_explicit_opt_out_preserves_review_and_protected_history(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    original = store.reconcile([source()], service()).personal_events[0]

    review = store.reconcile([
        source(title='Физика (ЛК)'),
    ], choices(Физика={'lectures': False})).personal_events[0]
    assert review.state == PersonalEventState.NEEDS_REVIEW
    assert review.metadata['change_reason'] == 'replacement_requires_confirmation'

    history_store = UniversitySnapshotStore(tmp_path / 'history.json')
    historical = history_store.reconcile([source()], service()).personal_events[0]
    protected = history_store.reconcile(
        [source()], choices(Математика={'lectures': False}),
        protected_personal_ids=[historical.id],
    ).personal_events[0]
    assert protected.id == historical.id
    assert protected.state == PersonalEventState.CONFIRMED
    assert protected.metadata.get('attendance_choice') is True


def test_refresh_choices_does_not_age_disappearance_or_rewrite_unchanged_snapshot(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    store.reconcile([source()], service())
    missing = store.reconcile([
        source('other', title='Физика (ЛК)', start=START + timedelta(days=1)),
    ], service())
    expected_missing_count = next(
        event.metadata['missing_sync_count'] for event in missing.personal_events
        if event.university_event_uid == 'math-1'
    )

    refreshed = store.refresh_attendance_choices(choices(Физика={'lectures': False}))
    assert next(event for event in refreshed if event.university_event_uid == 'other').metadata[
        'attendance_choice'
    ] is False
    with patch.object(store, '_save') as save:
        again = store.refresh_attendance_choices(choices(Физика={'lectures': False}))
    save.assert_not_called()
    assert next(event for event in again if event.university_event_uid == 'math-1').metadata[
        'missing_sync_count'
    ] == expected_missing_count


def test_raw_lab_slot_key_survives_timezone_normalization_and_snapshot_reload(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    raw = replace(
        source('lab', title='Математика (ЛБ)', start=START.replace(tzinfo=timezone.utc)),
        event_type=EventType.LAB,
    )
    normalized = replace(
        raw, dtstart=raw.dtstart.astimezone(timezone(timedelta(hours=7))),
        dtend=raw.dtend.astimezone(timezone(timedelta(hours=7))),
        attendance_slot_key=lab_slot_key(raw),
    )
    selected = choices(Математика={
        'labs_enabled': True, 'lab_slots': [lab_slot_key(raw)],
    })

    first = store.reconcile([normalized], selected).personal_events[0]
    assert first.state == PersonalEventState.CONFIRMED
    assert lab_slot_key(store._load()['source_events'][0]) == lab_slot_key(raw)
    assert store.refresh_attendance_choices(selected)[0].state == PersonalEventState.CONFIRMED


def test_moved_lab_outside_selected_slot_becomes_expected(tmp_path):
    store = UniversitySnapshotStore(tmp_path / 'university_reconciliation.json')
    initial = replace(source('lab', title='Математика (ЛБ)'), event_type=EventType.LAB)
    selected = choices(Математика={
        'labs_enabled': True, 'lab_slots': [lab_slot_key(initial)],
    })
    first = store.reconcile([initial], selected).personal_events[0]

    moved = store.reconcile([
        replace(initial, dtstart=initial.dtstart + timedelta(hours=2),
                dtend=initial.dtend + timedelta(hours=2)),
    ], selected).personal_events[0]

    assert first.state == PersonalEventState.CONFIRMED
    assert moved.id == first.id
    assert moved.state == PersonalEventState.EXPECTED
    assert moved.metadata['attendance_choice'] is False


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
