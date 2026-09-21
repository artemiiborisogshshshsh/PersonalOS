import json
from datetime import datetime, timezone

import pytest

from services.tutoring_service import TutoringMode, TutoringSession
from services.user_tutoring_store import UserTutoringStore


def make_session(*, identifier="lesson-1", student="Максим", start=None, **changes):
    values = {
        "id": identifier, "student": student,
        "start": start or datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc),
        "duration_minutes": 45, "mode": TutoringMode.ONLINE,
        "location": "", "travel_before_minutes": 0, "travel_after_minutes": 0,
        "material_preparation_minutes": 0, "homework_review_minutes": 0,
        "weekly_variable_preparation_minutes": 0,
    }
    values.update(changes)
    return TutoringSession(**values)


def test_confirmed_45_minute_session_survives_restart(tmp_path):
    session = make_session()
    store = UserTutoringStore(tmp_path / "user-a")

    store.upsert(session, confirmed=True)

    assert UserTutoringStore(tmp_path / "user-a").load() == [session]


def test_unconfirmed_session_never_creates_or_changes_state(tmp_path):
    store = UserTutoringStore(tmp_path / "user-a")

    with pytest.raises(PermissionError, match="must be confirmed"):
        store.upsert(make_session())
    with pytest.raises(PermissionError, match="must be confirmed"):
        store.upsert(make_session(), confirmed=False)

    assert not store.path.exists()
    assert store.load() == []


@pytest.mark.parametrize("confirmed", [1, "true", "false"])
def test_truthy_confirmation_values_cannot_mutate_prior_state(tmp_path, confirmed):
    store = UserTutoringStore(tmp_path)
    store.upsert(make_session(), confirmed=True)
    before = store.path.read_bytes()

    with pytest.raises(PermissionError, match="must be confirmed"):
        store.upsert(make_session(student="Changed"), confirmed=confirmed)

    assert store.path.read_bytes() == before


def test_repeated_id_is_idempotent_and_confirmed_update_replaces_it(tmp_path):
    store = UserTutoringStore(tmp_path)
    original = make_session()
    changed = make_session(student="Настя", duration_minutes=90)

    store.upsert(original, confirmed=True)
    store.upsert(original, confirmed=True)
    store.upsert(changed, confirmed=True)

    assert UserTutoringStore(tmp_path).load() == [changed]


def test_users_have_isolated_state_directories(tmp_path):
    first = UserTutoringStore(tmp_path / "user-a")
    second = UserTutoringStore(tmp_path / "user-b")
    first_session = make_session(identifier="a")
    second_session = make_session(identifier="b")

    first.upsert(first_session, confirmed=True)
    second.upsert(second_session, confirmed=True)

    assert first.load() == [first_session]
    assert second.load() == [second_session]


def test_metadata_is_never_persisted_or_restored(tmp_path):
    store = UserTutoringStore(tmp_path)
    source = make_session(metadata={"private": "discard this"})

    store.upsert(source, confirmed=True)

    assert store.load()[0].metadata == {}
    assert "metadata" not in json.loads(store.path.read_text())["sessions"][0]


@pytest.mark.parametrize("payload", [
    "not-json",
    {"version": True, "sessions": []},
    {"version": 2, "sessions": []},
    {"version": 1, "sessions": {}},
    {"version": 1, "sessions": [{"id": "missing"}]},
    {"version": 1, "sessions": [
        {"id": "same", "student": "A", "start": "2026-09-08T16:00:00+00:00",
         "duration_minutes": 45, "mode": "online", "location": "",
         "travel_before_minutes": 0, "travel_after_minutes": 0,
         "material_preparation_minutes": 0, "homework_review_minutes": 0,
         "weekly_variable_preparation_minutes": 0},
        {"id": "same", "student": "B", "start": "2026-09-09T16:00:00+00:00",
         "duration_minutes": 45, "mode": "online", "location": "",
         "travel_before_minutes": 0, "travel_after_minutes": 0,
         "material_preparation_minutes": 0, "homework_review_minutes": 0,
         "weekly_variable_preparation_minutes": 0},
    ]},
])
def test_malformed_state_fails_closed(tmp_path, payload):
    store = UserTutoringStore(tmp_path)
    store.path.write_text(payload if isinstance(payload, str) else json.dumps(payload))

    with pytest.raises(ValueError, match="Invalid tutoring session state"):
        store.load()


def test_load_missing_file_does_not_create_directory(tmp_path):
    store = UserTutoringStore(tmp_path / "absent")

    assert store.load() == []
    assert not store.path.parent.exists()


def test_symlink_state_file_is_rejected(tmp_path):
    store = UserTutoringStore(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text('{"version": 1, "sessions": []}')
    store.path.symlink_to(target)

    with pytest.raises(ValueError, match="Invalid tutoring session state"):
        store.load()


def test_failed_atomic_replacement_keeps_previous_state(tmp_path, monkeypatch):
    store = UserTutoringStore(tmp_path)
    original = make_session()
    store.upsert(original, confirmed=True)

    monkeypatch.setattr("services.user_tutoring_store.os.replace",
                        lambda *_: (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        store.upsert(make_session(student="Changed"), confirmed=True)

    assert store.load() == [original]


@pytest.mark.parametrize("changes", [
    {"identifier": ""},
    {"identifier": "   "},
    {"student": ""},
    {"student": "   "},
    {"start": datetime(2026, 9, 8, 16, 0)},
])
def test_confirmed_invalid_session_never_creates_state(tmp_path, changes):
    store = UserTutoringStore(tmp_path / "user-a")

    with pytest.raises(ValueError, match="Invalid tutoring session"):
        store.upsert(make_session(**changes), confirmed=True)

    assert not store.path.exists()
