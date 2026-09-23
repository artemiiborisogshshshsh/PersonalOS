from datetime import datetime, timezone
import asyncio
import json
import os

from models import Task, TaskPriority, TaskStatus
from services.application.command import CreateTaskCommand
from services.natural_text_application import PerUserNaturalCommandExecutor
from services.telegram_task_planning import TelegramTaskPlanningFlow
from services.user_project_store import UserProjectStore


NOW = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)


def _intake(directory, *, command_id="first", title="Купить учебник", status="todo"):
    executor = PerUserNaturalCommandExecutor(directory / "natural_commands.json")
    asyncio.run(executor.process_command(CreateTaskCommand(
        title, "Описание", status=status, command_id=command_id,
    )))
    return executor.snapshot()["tasks"][0]["id"]


def test_duration_preview_is_inert_and_rejection_keeps_source_unchanged(tmp_path):
    source_id = _intake(tmp_path)
    flow = TelegramTaskPlanningFlow("101", tmp_path, now_provider=lambda: NOW)

    picked = flow.handle_callback("101", f"tp:pick:{source_id}")
    assert "Купить учебник" in picked["text"]
    assert not (tmp_path / "project_tasks.json").exists()

    proposed = flow.handle_callback("101", f"tp:estimate:{source_id}:30")
    assert "30" in proposed["text"]
    assert (tmp_path / "task_planning_proposal.json").exists()
    assert not (tmp_path / "project_tasks.json").exists()
    reject = proposed["buttons"][0][1]["callback_data"]
    assert "отклонено" in flow.handle_callback("101", reject)["text"].lower()
    assert not (tmp_path / "project_tasks.json").exists()
    assert len(PerUserNaturalCommandExecutor(tmp_path / "natural_commands.json").snapshot()["tasks"]) == 1


def _proposal(flow, source_id, minutes=30):
    return flow.handle_callback("101", f"tp:estimate:{source_id}:{minutes}")


def test_confirm_survives_restart_and_replay_keeps_one_stable_task(tmp_path):
    source_id = _intake(tmp_path)
    first = TelegramTaskPlanningFlow("101", tmp_path, now_provider=lambda: NOW)
    pending = _proposal(first, source_id, 45)
    confirm = pending["buttons"][0][0]["callback_data"]

    result = TelegramTaskPlanningFlow("101", tmp_path).handle_callback("101", confirm)
    assert "добавлена" in result["text"].lower()
    _, tasks = UserProjectStore(tmp_path).load()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.id == f"natural-task:{source_id}"
    assert task.project_id is None and task.status is TaskStatus.TODO
    assert task.estimated_hours == 0.75
    assert task.created_at == NOW == task.updated_at

    stale = TelegramTaskPlanningFlow("101", tmp_path).handle_callback("101", confirm)
    assert "не актуально" in stale["text"].lower()
    assert len(UserProjectStore(tmp_path).load()[1]) == 1


def test_invalid_duration_pagination_and_callbacks_are_bounded(tmp_path):
    ids = [_intake(tmp_path, command_id=f"command-{number}", title=f"Task {number}")
           for number in range(12)]
    flow = TelegramTaskPlanningFlow("101", tmp_path, now_provider=lambda: NOW)

    page = flow.handle_text("101", "/tasks")
    assert len(page["buttons"]) == 11  # ten tasks plus next-page navigation
    assert all(len(button["callback_data"]) <= 64 for row in page["buttons"] for button in row)
    second = flow.handle_callback("101", "tp:page:2")
    assert "2/2" in second["text"]
    assert "1/2" in flow.handle_text("101", "/tasks@planner_bot")["text"]
    assert "целое число" in flow.handle_text("101", f"/task_estimate {ids[0]} 14")["text"]
    assert "целое число" in flow.handle_callback("101", f"tp:estimate:{ids[0]}:30.0")["text"]
    assert not (tmp_path / "project_tasks.json").exists()


def test_wrong_owner_and_malformed_callbacks_do_no_state_io(tmp_path, monkeypatch):
    import services.telegram_task_planning as planning

    flow = TelegramTaskPlanningFlow("101", tmp_path)

    monkeypatch.setattr(planning.PerUserNaturalCommandExecutor, "snapshot",
                        lambda _self: (_ for _ in ()).throw(AssertionError("read")))
    monkeypatch.setattr(planning.UserProjectStore, "load",
                        lambda _self: (_ for _ in ()).throw(AssertionError("read")))
    assert flow.handle_text("202", "/tasks") is None
    assert flow.handle_callback("202", "tp:page:1") is None
    assert "распознана" in flow.handle_callback("101", "garbage")["text"]


def test_replacement_stale_source_change_and_collision_never_overwrite(tmp_path):
    source_id = _intake(tmp_path)
    flow = TelegramTaskPlanningFlow("101", tmp_path, now_provider=lambda: NOW)
    first = _proposal(flow, source_id)
    second = _proposal(flow, source_id, 60)
    assert "не актуально" in flow.handle_callback("101", first["buttons"][0][0]["callback_data"])["text"]

    state_path = tmp_path / "natural_commands.json"
    state = json.loads(state_path.read_text())
    state["tasks"][0]["title"] = "Изменённая задача"
    state_path.write_text(json.dumps(state))
    assert "изменилась" in flow.handle_callback("101", second["buttons"][0][0]["callback_data"])["text"]
    assert not (tmp_path / "project_tasks.json").exists()

    state["tasks"][0]["title"] = "Купить учебник"
    state_path.write_text(json.dumps(state))
    third = _proposal(flow, source_id)
    collision = Task(
        id=f"natural-task:{source_id}", title="Existing", description="", project_id=None,
        status=TaskStatus.DONE, priority=TaskPriority.LOW, created_at=NOW, updated_at=NOW,
        due_date=None, estimated_hours=None, dependencies=[],
    )
    UserProjectStore(tmp_path).upsert_task(collision, confirmed=True)
    result = flow.handle_callback("101", third["buttons"][0][0]["callback_data"])
    assert "изменилась" in result["text"]
    assert UserProjectStore(tmp_path).load()[1] == [collision]


def test_clear_failure_is_idempotent_and_does_not_reset_edited_target(tmp_path, monkeypatch):
    source_id = _intake(tmp_path)
    flow = TelegramTaskPlanningFlow("101", tmp_path, now_provider=lambda: NOW)
    pending = _proposal(flow, source_id)
    confirm = pending["buttons"][0][0]["callback_data"]
    monkeypatch.setattr(flow, "_clear_pending", lambda: (_ for _ in ()).throw(ValueError))

    retry = flow.handle_callback("101", confirm)
    assert "повторите" in retry["text"].lower()
    target = UserProjectStore(tmp_path).load()[1][0]
    target.status = TaskStatus.DONE
    target.estimated_hours = None
    UserProjectStore(tmp_path).upsert_task(target, confirmed=True)

    replay = TelegramTaskPlanningFlow("101", tmp_path).handle_callback("101", confirm)
    assert "изменилась" in replay["text"]
    assert UserProjectStore(tmp_path).load()[1][0].status is TaskStatus.DONE


def test_malformed_pending_and_symlinked_state_fail_closed(tmp_path):
    source_id = _intake(tmp_path)
    pending_path = tmp_path / "task_planning_proposal.json"
    pending_path.write_text('{"version": 1, "proposal": {"id": "bad"}}')
    flow = TelegramTaskPlanningFlow("101", tmp_path)
    assert "безопасно" in flow.handle_callback("101", "tp:confirm:" + "a" * 32)["text"]
    assert not (tmp_path / "project_tasks.json").exists()

    source_path = tmp_path / "natural_commands.json"
    copied = tmp_path / "source-copy.json"
    source_path.replace(copied)
    os.symlink(copied, source_path)
    assert "безопасно" in flow.handle_text("101", "/tasks")["text"]


def _promoted_flow(directory):
    source_id = _intake(directory)
    flow = TelegramTaskPlanningFlow('101', directory, now_provider=lambda: NOW)
    promotion = _proposal(flow, source_id)
    flow.handle_callback('101', promotion['buttons'][0][0]['callback_data'])
    return flow


def _completion(flow):
    listing = flow.handle_text('101', '/planned_tasks@planner_bot')
    return flow.handle_callback('101', listing['buttons'][0][0]['callback_data'])


def test_completion_preview_reject_restart_and_replay(tmp_path):
    flow = _promoted_flow(tmp_path)
    source = (tmp_path / 'natural_commands.json').read_bytes()
    initial = (tmp_path / 'project_tasks.json').read_bytes()
    proposal = _completion(flow)
    assert (tmp_path / 'project_tasks.json').read_bytes() == initial
    flow.handle_callback('101', proposal['buttons'][0][1]['callback_data'])
    assert (tmp_path / 'project_tasks.json').read_bytes() == initial
    proposal = _completion(flow)
    confirm = proposal['buttons'][0][0]['callback_data']
    assert flow.handle_callback('202', confirm) is None
    restarted = TelegramTaskPlanningFlow('101', tmp_path)
    assert 'выполненной' in restarted.handle_callback('101', confirm)['text']
    completed = (tmp_path / 'project_tasks.json').read_bytes()
    restarted.handle_callback('101', confirm)
    assert (tmp_path / 'project_tasks.json').read_bytes() == completed
    assert (tmp_path / 'natural_commands.json').read_bytes() == source
    assert UserProjectStore(tmp_path).load()[1][0].status is TaskStatus.DONE
    assert not restarted.handle_text('101', '/planned_tasks')['buttons']
    assert not restarted.handle_text('101', '/tasks')['buttons']


def test_completion_rejects_changed_task_and_replaced_proposal(tmp_path):
    flow = _promoted_flow(tmp_path)
    first = _completion(flow)
    second = _completion(flow)
    assert 'не актуально' in flow.handle_callback('101', first['buttons'][0][0]['callback_data'])['text']
    task = UserProjectStore(tmp_path).load()[1][0]
    task.title = 'Updated title'
    UserProjectStore(tmp_path).upsert_task(task, confirmed=True)
    assert 'изменилась' in flow.handle_callback('101', second['buttons'][0][0]['callback_data'])['text']
    assert UserProjectStore(tmp_path).load()[1][0] == task


def test_completion_recovers_after_clear_failure_without_rewriting_task(tmp_path, monkeypatch):
    flow = _promoted_flow(tmp_path)
    proposal = _completion(flow)
    confirm = proposal['buttons'][0][0]['callback_data']
    monkeypatch.setattr(flow, '_clear_pending', lambda: (_ for _ in ()).throw(ValueError))
    assert 'Повторите' in flow.handle_callback('101', confirm)['text']
    saved = (tmp_path / 'project_tasks.json').read_bytes()
    restarted = TelegramTaskPlanningFlow('101', tmp_path)
    assert 'уже отмечена' in restarted.handle_callback('101', confirm)['text']
    assert (tmp_path / 'project_tasks.json').read_bytes() == saved


def test_completion_write_failure_leaves_task_and_proposal_retryable(tmp_path, monkeypatch):
    flow = _promoted_flow(tmp_path)
    proposal = _completion(flow)
    confirm = proposal['buttons'][0][0]['callback_data']
    saved = (tmp_path / 'project_tasks.json').read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(UserProjectStore, '_write', lambda *args: (_ for _ in ()).throw(OSError))
        assert 'безопасно' in flow.handle_callback('101', confirm)['text']
    assert (tmp_path / 'project_tasks.json').read_bytes() == saved
    assert 'выполненной' in flow.handle_callback('101', confirm)['text']


def test_planned_pagination_supports_long_unicode_ids(tmp_path):
    store = UserProjectStore(tmp_path)
    for i in range(11):
        store.upsert_task(Task(id='задача' * 50 + str(i), title=f'Task {i}', description='',
                               project_id=None, status=TaskStatus.TODO, priority=TaskPriority.LOW,
                               created_at=NOW, updated_at=NOW, due_date=None,
                               estimated_hours=1, dependencies=[]), confirmed=True)
    flow = TelegramTaskPlanningFlow('101', tmp_path)
    listing = flow.handle_text('101', '/planned_tasks')
    assert len(listing['buttons']) == 11
    assert all(len(b['callback_data'].encode()) <= 64 for row in listing['buttons'] for b in row)
    assert '2/2' in flow.handle_callback('101', 'tp:planned:2')['text']
    assert 'нет' in flow.handle_callback('101', 'tp:planned:0')['text']
    assert 'выполненной' in _completion(flow)['text']


def test_malformed_completion_proposal_fails_closed(tmp_path):
    flow = _promoted_flow(tmp_path)
    proposal = _completion(flow)
    confirm = proposal['buttons'][0][0]['callback_data']
    pending = tmp_path / 'task_planning_proposal.json'
    payload = json.loads(pending.read_text())
    payload['proposal']['task']['status'] = 'done'
    pending.write_text(json.dumps(payload))
    saved = (tmp_path / 'project_tasks.json').read_bytes()
    assert 'безопасно' in flow.handle_callback('101', confirm)['text']
    assert (tmp_path / 'project_tasks.json').read_bytes() == saved


def test_completion_retry_never_overwrites_later_edit(tmp_path, monkeypatch):
    flow = _promoted_flow(tmp_path)
    proposal = _completion(flow)
    confirm = proposal['buttons'][0][0]['callback_data']
    monkeypatch.setattr(flow, '_clear_pending', lambda: (_ for _ in ()).throw(ValueError))
    flow.handle_callback('101', confirm)
    task = UserProjectStore(tmp_path).load()[1][0]
    task.title = 'Edited after completion'
    UserProjectStore(tmp_path).upsert_task(task, confirmed=True)
    restarted = TelegramTaskPlanningFlow('101', tmp_path)
    assert 'изменилась' in restarted.handle_callback('101', confirm)['text']
    assert UserProjectStore(tmp_path).load()[1][0] == task
