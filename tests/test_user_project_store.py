"""Regression coverage for confirmed, isolated project/task state."""

from datetime import datetime, timezone
import json
import math

import pytest

from models import Project, ProjectStatus, Task, TaskPriority, TaskStatus
from services.user_project_store import UserProjectStore


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def make_project(*, identifier="project-1", name="Calendar", **changes):
    values = {
        "id": identifier, "name": name, "description": "Project description",
        "status": ProjectStatus.ACTIVE, "created_at": NOW, "updated_at": NOW,
        "start_date": None, "target_date": None,
    }
    values.update(changes)
    return Project(**values)


def make_task(*, identifier="task-1", project_id="project-1", **changes):
    values = {
        "id": identifier, "title": "Implement store", "description": "Task description",
        "project_id": project_id, "status": TaskStatus.TODO,
        "priority": TaskPriority.MEDIUM, "created_at": NOW, "updated_at": NOW,
        "due_date": None, "estimated_hours": 1.5, "dependencies": [],
    }
    values.update(changes)
    return Task(**values)


def test_confirmed_project_and_task_survive_restart(tmp_path):
    store = UserProjectStore(tmp_path / "user-a")
    project = make_project()
    task = make_task()

    store.upsert_project(project, confirmed=True)
    store.upsert_task(task, confirmed=True)

    assert UserProjectStore(tmp_path / "user-a").load() == ([project], [task])


def test_unconfirmed_calls_never_read_or_change_state(tmp_path, monkeypatch):
    store = UserProjectStore(tmp_path / "user-a")
    read_attempted = False

    def fail_if_read():
        nonlocal read_attempted
        read_attempted = True
        raise AssertionError("unconfirmed request read state")

    monkeypatch.setattr(store, "load", fail_if_read)
    with pytest.raises(PermissionError):
        store.upsert_project(make_project())
    with pytest.raises(PermissionError):
        store.upsert_task(make_task())

    assert not read_attempted
    assert not store.path.exists()


@pytest.mark.parametrize("confirmed", [False, 0, 1, "true", object()])
def test_only_literal_true_can_mutate_existing_state(tmp_path, confirmed):
    store = UserProjectStore(tmp_path)
    store.upsert_project(make_project(), confirmed=True)
    before = store.path.read_bytes()

    with pytest.raises(PermissionError):
        store.upsert_project(make_project(name="Changed"), confirmed=confirmed)

    assert store.path.read_bytes() == before


def test_stable_ids_update_without_duplicates_and_records_sort_by_id(tmp_path):
    store = UserProjectStore(tmp_path)
    store.upsert_project(make_project(identifier="b"), confirmed=True)
    store.upsert_project(make_project(identifier="a"), confirmed=True)
    store.upsert_project(make_project(identifier="a", name="Updated"), confirmed=True)
    store.upsert_task(make_task(identifier="z", project_id="a"), confirmed=True)
    store.upsert_task(make_task(identifier="a", project_id="a"), confirmed=True)

    projects, tasks = UserProjectStore(tmp_path).load()
    assert [project.id for project in projects] == ["a", "b"]
    assert projects[0].name == "Updated"
    assert [task.id for task in tasks] == ["a", "z"]


def test_same_ids_are_isolated_between_user_directories(tmp_path):
    first = UserProjectStore(tmp_path / "user-a")
    second = UserProjectStore(tmp_path / "user-b")
    first.upsert_project(make_project(name="First"), confirmed=True)
    second.upsert_project(make_project(name="Second"), confirmed=True)

    assert first.load()[0][0].name == "First"
    assert second.load()[0][0].name == "Second"


@pytest.mark.parametrize("status", [TaskStatus.INBOX, TaskStatus.REVIEW, TaskStatus.DONE,
                                   TaskStatus.COMPLETED, TaskStatus.ARCHIVED])
def test_non_active_task_statuses_can_omit_estimate(tmp_path, status):
    store = UserProjectStore(tmp_path)
    store.upsert_project(make_project(), confirmed=True)

    store.upsert_task(make_task(status=status, estimated_hours=None), confirmed=True)

    assert store.load()[1][0].estimated_hours is None


@pytest.mark.parametrize("status", [TaskStatus.TODO, TaskStatus.IN_PROGRESS])
def test_active_task_statuses_require_an_explicit_estimate(tmp_path, status):
    store = UserProjectStore(tmp_path)
    store.upsert_project(make_project(), confirmed=True)
    before = store.path.read_bytes()

    with pytest.raises(ValueError):
        store.upsert_task(make_task(status=status, estimated_hours=None), confirmed=True)

    assert store.path.read_bytes() == before


@pytest.mark.parametrize("hours", [0, -1, True, math.nan, math.inf, 0.0001, 1.234, 10 ** 400])
def test_estimate_must_be_positive_finite_and_a_whole_number_of_minutes(tmp_path, hours):
    store = UserProjectStore(tmp_path)
    store.upsert_project(make_project(), confirmed=True)
    before = store.path.read_bytes()

    with pytest.raises(ValueError):
        store.upsert_task(make_task(estimated_hours=hours), confirmed=True)

    assert store.path.read_bytes() == before


def test_aware_dates_are_required(tmp_path):
    store = UserProjectStore(tmp_path)

    with pytest.raises(ValueError):
        store.upsert_project(make_project(start_date=datetime(2026, 9, 21)), confirmed=True)
    assert not store.path.exists()

    store.upsert_project(make_project(), confirmed=True)
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.upsert_task(make_task(due_date=datetime(2026, 9, 21)), confirmed=True)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(("field", "value"), [
    ("start_date", 123),
    ("target_date", "not-a-datetime"),
])
def test_invalid_project_optional_date_never_becomes_null(tmp_path, field, value):
    store = UserProjectStore(tmp_path)
    store.upsert_project(make_project(), confirmed=True)
    before = store.path.read_bytes()

    with pytest.raises(ValueError):
        store.upsert_project(make_project(**{field: value}), confirmed=True)

    assert store.path.read_bytes() == before


@pytest.mark.parametrize(("field", "value"), [
    ("due_date", 123),
    ("due_date", "not-a-datetime"),
])
def test_invalid_task_optional_date_never_becomes_null(tmp_path, field, value):
    store = UserProjectStore(tmp_path)
    store.upsert_project(make_project(), confirmed=True)
    before = store.path.read_bytes()

    with pytest.raises(ValueError):
        store.upsert_task(make_task(**{field: value}), confirmed=True)

    assert store.path.read_bytes() == before


def test_cross_user_or_missing_references_and_cycles_are_rejected_without_writes(tmp_path):
    foreign = UserProjectStore(tmp_path / "foreign")
    foreign.upsert_project(make_project(), confirmed=True)
    store = UserProjectStore(tmp_path / "local")

    with pytest.raises(ValueError):
        store.upsert_task(make_task(), confirmed=True)
    assert not store.path.exists()

    store.upsert_project(make_project(), confirmed=True)
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.upsert_task(make_task(dependencies=["not-here"]), confirmed=True)
    assert store.path.read_bytes() == before

    store.upsert_task(make_task(identifier="second", dependencies=[]), confirmed=True)
    store.upsert_task(make_task(identifier="first", dependencies=["second"]), confirmed=True)
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.upsert_task(make_task(identifier="second", dependencies=["first"]), confirmed=True)
    assert store.path.read_bytes() == before


def test_metadata_and_unstored_task_fields_are_not_retained(tmp_path):
    store = UserProjectStore(tmp_path)
    project = make_project(tags={"private"}, metadata={"secret": "discard"})
    task = make_task(tags={"private"}, metadata={"secret": "discard"},
                     actual_hours=4, assignee="Someone")
    store.upsert_project(project, confirmed=True)
    store.upsert_task(task, confirmed=True)

    reloaded_project, reloaded_task = store.load()
    raw = json.loads(store.path.read_text())
    assert reloaded_project[0].tags == set()
    assert reloaded_project[0].metadata == {}
    assert reloaded_task[0].tags == set()
    assert reloaded_task[0].metadata == {}
    assert reloaded_task[0].actual_hours is None
    assert reloaded_task[0].assignee is None
    assert set(raw["projects"][0]) == {
        "id", "name", "description", "status", "created_at", "updated_at", "start_date", "target_date",
    }
    assert set(raw["tasks"][0]) == {
        "id", "title", "description", "project_id", "status", "priority", "created_at", "updated_at",
        "due_date", "estimated_hours", "dependencies",
    }


@pytest.mark.parametrize("payload", [
    "not-json",
    {"version": True, "projects": [], "tasks": []},
    {"version": 2, "projects": [], "tasks": []},
    {"version": 1, "projects": {}, "tasks": []},
    {"version": 1, "projects": [], "tasks": [], "extra": []},
    {"version": 1, "projects": [{"id": "missing"}], "tasks": []},
    {"version": 1, "projects": [], "tasks": [{"id": "bad"}]},
])
def test_malformed_schemas_fail_closed(tmp_path, payload):
    store = UserProjectStore(tmp_path)
    store.path.write_text(payload if isinstance(payload, str) else json.dumps(payload))

    with pytest.raises(ValueError, match="Invalid project task state"):
        store.load()


def test_load_rejects_stored_references_that_are_not_local(tmp_path):
    store = UserProjectStore(tmp_path)
    store.upsert_project(make_project(), confirmed=True)
    store.upsert_task(make_task(), confirmed=True)
    payload = json.loads(store.path.read_text())
    payload["projects"] = []
    store.path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="Invalid project task state"):
        store.load()


def test_missing_file_does_not_create_directory_and_symlink_is_rejected(tmp_path):
    store = UserProjectStore(tmp_path / "missing")
    assert store.load() == ([], [])
    assert not store.path.parent.exists()

    target = tmp_path / "target.json"
    target.write_text('{"version": 1, "projects": [], "tasks": []}')
    symlink_store = UserProjectStore(tmp_path / "symlink")
    symlink_store.path.parent.mkdir()
    symlink_store.path.symlink_to(target)
    with pytest.raises(ValueError):
        symlink_store.load()
    with pytest.raises(ValueError):
        symlink_store.upsert_project(make_project(), confirmed=True)


def test_failed_atomic_replacement_keeps_prior_state(tmp_path, monkeypatch):
    store = UserProjectStore(tmp_path)
    original = make_project()
    store.upsert_project(original, confirmed=True)

    monkeypatch.setattr("services.user_project_store.os.replace",
                        lambda *_: (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        store.upsert_project(make_project(name="Changed"), confirmed=True)

    assert store.load() == ([original], [])
