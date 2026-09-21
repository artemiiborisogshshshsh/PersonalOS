"""Per-user weekly previews keep confirmed personal events as local blockers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from copy import deepcopy
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from models import WeeklyCapacityModel
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType
from services.application.command import CreateUniversityEventCommand
from services.natural_text_application import PerUserNaturalCommandExecutor
from services.onboarding_service import OnboardingState, OnboardingStore, OnboardingStep, TelegramOnboardingService
from services.per_user_weekly_preview import PerUserWeeklyPreview
from services.product_state import UserProductStateStore
from services.schedule_source_service import ScheduleSourceService
from services.telegram_multi_user_runtime import TelegramMultiUserDispatcher
from services.telegram_onboarding_handler import TelegramOnboardingHandler
from services.telegram_natural_text_proposals import create_per_user_natural_text_flow
from services.telegram_user_router import TelegramUserRouter
from services.user_planning_profile_store import UserPlanningProfileStore
from services.user_registry import UserRegistryStore, UserStatePaths
from services.weekly_plan_service import WeeklyPlan, WeeklyPlanPipeline, WeeklyPlanStatus


TZ = ZoneInfo("Asia/Tomsk")
START = datetime(2026, 9, 21, 9, tzinfo=TZ)


def _base_plan() -> WeeklyPlan:
    return WeeklyPlan(
        week_start=START,
        week_end=START + timedelta(hours=4),
        items=[PlanningItem(
            id="project:focus", title="Проект", description="",
            item_type=PlanningItemType.PROJECT_TASK, duration_minutes=60,
            earliest_start=START + timedelta(hours=1), latest_end=START + timedelta(hours=2),
            flexible=True, priority=3,
        )],
    )


def _selectable_base_plan() -> WeeklyPlan:
    plan = _base_plan()
    plan.items[0].earliest_start = START
    plan.items[0].latest_end = START + timedelta(hours=1)
    return plan


def _pipeline() -> WeeklyPlanPipeline:
    capacity = WeeklyCapacityModel(
        total_week_minutes=240, sleep_block=0, fixed_commitments=0,
        university_load=0, teaching_load=0, travel_load=0,
        recovery_block=0, buffer=0,
    )
    return WeeklyPlanPipeline(PlanningEngine(weekly_capacity=capacity, num_candidates=4))


def _save_state(directory, events):
    (directory / "natural_commands.json").parent.mkdir(parents=True, exist_ok=True)
    (directory / "natural_commands.json").write_text(json.dumps({
        "version": 1, "tasks": [], "personal_events": events, "executed": {},
    }), encoding="utf-8")


def _event(event_id, title, start, end):
    return {"id": event_id, "summary": title, "start": start, "end": end}


def test_confirmed_personal_event_blocks_a_flexible_project_task(tmp_path):
    """A confirmed durable event must reach the existing scheduler as fixed work."""
    UserPlanningProfileStore(tmp_path / "planning_profile.json").save(
        UserPlanningProfileStore(tmp_path / "planning_profile.json").load(),
    )
    executor = PerUserNaturalCommandExecutor(tmp_path / "natural_commands.json")
    asyncio.run(executor.process_command(CreateUniversityEventCommand(
        "personal", "Встреча", "", "", START + timedelta(hours=1),
        START + timedelta(hours=2), command_id="event-1",
    )))
    preview = PerUserWeeklyPreview(tmp_path, _base_plan, pipeline_factory=_pipeline)

    selected = preview.preview()

    assert selected.status is WeeklyPlanStatus.INFEASIBLE
    assert selected.infeasibility is not None
    assert selected.infeasibility.unplaced_item_ids == ["project:focus"]
    event = next(item for item in selected.fixed_commitments if item.title == "Встреча")
    assert event.start == START + timedelta(hours=1)


def test_pending_or_rejected_proposals_are_not_blockers(tmp_path):
    """Only executor state is consumed; an unexecuted proposal remains inert."""
    UserPlanningProfileStore(tmp_path / "planning_profile.json").save(
        UserPlanningProfileStore(tmp_path / "planning_profile.json").load(),
    )
    account = UserRegistryStore(tmp_path / "registry.json").get_or_create("101")
    flow = create_per_user_natural_text_flow(account, tmp_path)
    bridge = PerUserWeeklyPreview(tmp_path, _base_plan, pipeline_factory=_pipeline)
    pending = flow.propose("101", "Запланируй встречу сегодня в 10:00", now=START)

    assert bridge.preview().fixed_commitments == []
    assert "отклонено" in flow.handle_callback("101", pending["buttons"][0][1]["callback_data"])["text"]
    assert bridge.preview().fixed_commitments == []

    confirmed = flow.propose("101", "Запланируй встречу сегодня в 10:00", now=START)
    assert "выполнено" in flow.handle_callback("101", confirmed["buttons"][0][0]["callback_data"])["text"]
    assert len(bridge.preview().fixed_commitments) == 1


def test_preview_reloads_durable_state_without_mutating_plan_or_capacity(tmp_path):
    store = UserPlanningProfileStore(tmp_path / "planning_profile.json")
    store.save(store.load())
    source_plan = _selectable_base_plan()
    source_plan.status = WeeklyPlanStatus.COMMITTED
    source_plan.approved_at = START
    source_plan.committed_at = START + timedelta(minutes=1)
    source_plan.metadata = {"approved_by": "user", "projection_result": "old", "input": "keep"}
    source_before = deepcopy(source_plan)
    created = []

    class ProjectorTrap:
        def __deepcopy__(self, memo):
            raise AssertionError("Preview must not copy a projector")

        def project(self, plan):
            raise AssertionError("Preview must not project")

    configured = _pipeline()
    configured.projector = ProjectorTrap()

    def factory():
        created.append(configured)
        return configured

    _save_state(tmp_path, [_event(
        "one", "Личное", (START + timedelta(hours=1)).isoformat(),
        (START + timedelta(hours=2)).isoformat(),
    )])
    state_bytes = (tmp_path / "natural_commands.json").read_bytes()
    bridge = PerUserWeeklyPreview(tmp_path, lambda: source_plan, pipeline_factory=factory)
    with patch.object(WeeklyPlanPipeline, "approve") as approve, patch.object(WeeklyPlanPipeline, "commit") as commit:
        first = bridge.preview()
        second = bridge.preview()
        restarted = PerUserWeeklyPreview(tmp_path, lambda: source_plan, pipeline_factory=factory).preview()
        approve.assert_not_called()
        commit.assert_not_called()

    assert first.status is WeeklyPlanStatus.SELECTED
    assert second.status is restarted.status is WeeklyPlanStatus.SELECTED
    assert len(created) == 3
    assert configured.engine.weekly_capacity.fixed_commitments == 0
    assert configured.engine.metrics == {}
    assert source_plan.fixed_commitments == []
    assert source_plan == source_before
    assert first.approved_at is first.committed_at is None
    assert first.metadata == {"input": "keep"}
    assert [
        (slot.scheduled_item_id, slot.start, slot.end)
        for slot in first.selected_candidate.schedule.slots
    ] == [
        (slot.scheduled_item_id, slot.start, slot.end)
        for slot in restarted.selected_candidate.schedule.slots
    ]
    assert (tmp_path / "natural_commands.json").read_bytes() == state_bytes


def test_edge_clipping_naive_local_time_and_outside_events(tmp_path):
    utc = ZoneInfo("UTC")
    start = datetime(2026, 9, 21, 0, tzinfo=utc)
    plan = WeeklyPlan(
        week_start=start, week_end=start + timedelta(hours=3), items=[],
    )
    profile = UserPlanningProfileStore(tmp_path / "planning_profile.json")
    profile.save(profile.load())
    # Naive 06:30 in Tomsk is 23:30 UTC on the preceding date; only its last
    # half hour intersects the UTC horizon. The second event is fully outside.
    _save_state(tmp_path, [
        _event("edge", "Край", "2026-09-21T06:30:00", "2026-09-21T07:30:00"),
        _event("aware", "UTC", "2026-09-21T01:00:00+00:00", "2026-09-21T02:00:00+00:00"),
        _event("outside", "Вне", "2026-09-22T12:00:00", "2026-09-22T13:00:00"),
    ])

    result = PerUserWeeklyPreview(tmp_path, lambda: plan, pipeline_factory=_pipeline).preview()

    assert result.status is WeeklyPlanStatus.SELECTED
    assert [(item.title, item.start, item.end) for item in result.fixed_commitments] == [
        ("Край", start, start + timedelta(minutes=30)),
        ("UTC", start + timedelta(hours=1), start + timedelta(hours=2)),
    ]
    assert result.fixed_commitments[0].metadata["original_start"].endswith("+00:00")


def test_invalid_state_and_identifier_collision_fail_closed_without_user_data(tmp_path):
    profile = UserPlanningProfileStore(tmp_path / "planning_profile.json")
    profile.save(profile.load())
    malformed_title = "секретное-название"
    _save_state(tmp_path, [_event("bad", malformed_title, "not-a-time", "2026-09-21T11:00:00")])
    bridge = PerUserWeeklyPreview(tmp_path, _base_plan, pipeline_factory=_pipeline)

    with patch.object(WeeklyPlanPipeline, "approve") as approve, patch.object(WeeklyPlanPipeline, "commit") as commit:
        assert "секретное" not in bridge()
        approve.assert_not_called()
        commit.assert_not_called()
    with pytest.raises(ValueError):
        bridge.preview()

    _save_state(tmp_path, [_event(
        "same", "Личное", (START + timedelta(hours=1)).isoformat(),
        (START + timedelta(hours=2)).isoformat(),
    )])
    colliding = WeeklyPlan(
        week_start=START, week_end=START + timedelta(hours=4), items=[PlanningItem(
            id="natural-command:personal-event:same", title="Проект", description="",
            item_type=PlanningItemType.PROJECT_TASK, duration_minutes=60,
        )],
    )
    with pytest.raises(ValueError):
        PerUserWeeklyPreview(tmp_path, lambda: colliding, pipeline_factory=_pipeline).preview()


@pytest.mark.parametrize("bad_root", ["[]", "null"])
def test_malformed_natural_state_root_is_safe_in_telegram_text(tmp_path, bad_root):
    profile = UserPlanningProfileStore(tmp_path / "planning_profile.json")
    profile.save(profile.load())
    secret = "личный-секрет"
    (tmp_path / "natural_commands.json").write_text(bad_root, encoding="utf-8")

    text = PerUserWeeklyPreview(tmp_path, _base_plan, pipeline_factory=_pipeline)()

    assert "Не удалось безопасно" in text
    assert secret not in text


@pytest.mark.parametrize("timezone_value", [None, []])
def test_malformed_profile_timezone_is_safe_in_telegram_text(tmp_path, timezone_value):
    (tmp_path / "planning_profile.json").write_text(json.dumps({
        "version": 1, "profile": {"timezone": timezone_value},
    }), encoding="utf-8")

    text = PerUserWeeklyPreview(tmp_path, _base_plan, pipeline_factory=_pipeline)()

    assert "Не удалось безопасно" in text


def test_naive_horizon_fixed_only_overlap_and_off_grid_event_fail_truthfully(tmp_path):
    profile = UserPlanningProfileStore(tmp_path / "planning_profile.json")
    profile.save(profile.load())
    _save_state(tmp_path, [_event(
        "off-grid", "Точная встреча", (START + timedelta(hours=1, minutes=10)).isoformat(),
        (START + timedelta(hours=2, minutes=10)).isoformat(),
    )])
    source = _base_plan()
    source_before = deepcopy(source)

    result = PerUserWeeklyPreview(tmp_path, lambda: source, pipeline_factory=_pipeline).preview()

    assert result.status is WeeklyPlanStatus.INFEASIBLE
    assert source == source_before
    off_grid_text = PerUserWeeklyPreview(tmp_path, lambda: source, pipeline_factory=_pipeline)()
    assert "шагом 15 мин." in off_grid_text
    assert "гибкие задачи" not in off_grid_text

    fixed_only = WeeklyPlan(week_start=START, week_end=START + timedelta(hours=4), items=[])
    _save_state(tmp_path, [
        _event("first", "Первое", (START + timedelta(hours=1)).isoformat(),
               (START + timedelta(hours=2)).isoformat()),
        _event("second", "Второе", (START + timedelta(hours=1, minutes=30)).isoformat(),
               (START + timedelta(hours=2, minutes=30)).isoformat()),
    ])
    fixed_only_bridge = PerUserWeeklyPreview(tmp_path, lambda: fixed_only, pipeline_factory=_pipeline)
    assert fixed_only_bridge.preview().status is WeeklyPlanStatus.INFEASIBLE
    assert "шагом 15 мин." in fixed_only_bridge()
    assert "гибкие задачи" not in fixed_only_bridge()

    naive = WeeklyPlan(
        week_start=START.replace(tzinfo=None), week_end=(START + timedelta(hours=1)).replace(tzinfo=None),
        items=[],
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        PerUserWeeklyPreview(tmp_path, lambda: naive, pipeline_factory=_pipeline).preview()


def _completed_handler(account, directory, preview):
    onboarding_store = OnboardingStore(directory / "onboarding.json")
    onboarding_store.save(OnboardingState(step=OnboardingStep.COMPLETE))
    return TelegramOnboardingHandler(
        account=account,
        state_directory=directory,
        onboarding=TelegramOnboardingService(onboarding_store),
        source_service=ScheduleSourceService(UserProductStateStore(directory / "product_state.json")),
        events_for_source=lambda _source: (),
        profile_store=UserPlanningProfileStore(directory / "planning_profile.json"),
        calendar_connected=lambda: True,
        weekly_preview=preview,
    )


def test_router_dispatcher_composes_each_completed_onboarding_preview_from_own_state(tmp_path):
    registry = UserRegistryStore(tmp_path / "registry.json")
    handlers = {}

    def blank_plan():
        return WeeklyPlan(
            week_start=START, week_end=START + timedelta(hours=4), items=[PlanningItem(
                id="flex", title="Гибкая работа", description="",
                item_type=PlanningItemType.PROJECT_TASK, duration_minutes=60,
                earliest_start=START, latest_end=START + timedelta(hours=1),
            )],
        )

    def factory(account, directory):
        profile = UserPlanningProfileStore(directory / "planning_profile.json")
        profile.save(profile.load())
        handler = _completed_handler(
            account, directory,
            PerUserWeeklyPreview(directory, blank_plan, pipeline_factory=_pipeline),
        )
        handlers[account.telegram_chat_id] = handler
        return handler

    dispatcher = TelegramMultiUserDispatcher(
        TelegramUserRouter(registry, UserStatePaths(tmp_path), factory),
    )
    dispatcher.handle_update({"message": {"chat": {"id": "101"}, "text": "/start"}})
    dispatcher.handle_update({"message": {"chat": {"id": "202"}, "text": "/start"}})
    _save_state(handlers["101"].state_directory, [_event(
        "first", "Только первый", (START + timedelta(hours=1)).isoformat(),
        (START + timedelta(hours=2)).isoformat(),
    )])

    _, first = dispatcher.handle_update({"message": {"chat": {"id": "101"}, "text": "/weekly_preview"}})
    _, second = dispatcher.handle_update({"message": {"chat": {"id": "202"}, "text": "/weekly_preview"}})

    assert "Только первый" in first["text"]
    assert "Гибкая работа" in first["text"]
    assert "21.09 09:00" in first["text"]
    assert "Только первый" not in second["text"]
    assert handlers["101"].state_directory != handlers["202"].state_directory
