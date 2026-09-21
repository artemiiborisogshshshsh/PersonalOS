"""Owned project tasks enter the same deterministic, non-projecting preview."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from models import WeeklyCapacityModel, Task, TaskStatus, TaskPriority
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType, create_planning_item_from_task
from services.per_user_weekly_preview import PerUserWeeklyPreview
from services.weekly_plan_service import WeeklyPlan, WeeklyPlanPipeline, WeeklyPlanStatus


START = datetime(2026, 9, 21, 9, tzinfo=ZoneInfo('Asia/Tomsk'))


def project(**changes):
    return dict(id='project', name='Synthetic project', description='', status='active',
                created_at=START.isoformat(), updated_at=START.isoformat(),
                start_date=None, target_date=None) | changes


def task(**changes):
    return dict(id='task', title='Synthetic task', description='', project_id='project',
                status='todo', priority='medium', created_at=START.isoformat(),
                updated_at=START.isoformat(), due_date=(START + timedelta(hours=5)).isoformat(),
                estimated_hours=1.0, dependencies=[]) | changes


def save_fixture(directory, projects=None, tasks=None):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'project_tasks.json').write_text(json.dumps({
        'version': 1, 'projects': [project()] if projects is None else projects,
        'tasks': [task()] if tasks is None else tasks,
    }), encoding='utf-8')


def base_plan():
    return WeeklyPlan(START, START + timedelta(hours=6), items=[])


def pipeline():
    return WeeklyPlanPipeline(PlanningEngine(
        weekly_capacity=WeeklyCapacityModel(
            total_week_minutes=360, sleep_block=0, recovery_block=0, buffer=0,
        ), num_candidates=4,
    ))


def test_owned_project_task_is_a_real_scheduling_input(tmp_path):
    save_fixture(tmp_path)

    plan = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline).preview()

    assert plan.status is WeeklyPlanStatus.SELECTED
    assert [item.id for item in plan.items] == ['project-task:task']
    assert plan.items[0].duration_minutes == 60
    assert plan.items[0].earliest_start == START
    slots = [slot for slot in plan.selected_candidate.schedule.slots
             if slot.scheduled_item_id == 'project-task:task']
    assert sum(slot.duration_minutes for slot in slots) == 60


def test_task_converter_uses_explicit_time_without_reading_wall_clock():
    value = Task('one', 'Task', '', None, TaskStatus.TODO, TaskPriority.MEDIUM,
                 START, START, estimated_hours=0.5)
    with patch('planning_engine.datetime') as clock:
        clock.now.side_effect = AssertionError('No wall-clock time in deterministic conversion')
        item = create_planning_item_from_task(value, earliest_start=START)
    assert item.earliest_start == START


def test_dependencies_finish_fully_before_dependents_even_with_higher_priority(tmp_path):
    save_fixture(tmp_path, tasks=[
        task(id='z-parent', estimated_hours=1.5),
        task(id='a-child', estimated_hours=0.75, dependencies=['z-parent'], priority='high'),
    ])
    plan = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline).preview()
    assert plan.status is WeeklyPlanStatus.SELECTED
    slots = plan.selected_candidate.schedule.slots
    parent_end = max(slot.end for slot in slots if slot.scheduled_item_id == 'project-task:z-parent')
    child_start = min(slot.start for slot in slots if slot.scheduled_item_id == 'project-task:a-child')
    assert parent_end <= child_start


def test_dependency_bounds_propagate_through_a_chain_without_editing_deadlines(tmp_path):
    deadline = START + timedelta(hours=2)
    save_fixture(tmp_path, tasks=[
        task(id='last', dependencies=['middle'], estimated_hours=0.25, due_date=deadline.isoformat()),
        task(id='middle', dependencies=['first'], estimated_hours=1 / 3, due_date=deadline.isoformat()),
        task(id='first', estimated_hours=0.5, due_date=deadline.isoformat()),
    ])
    before = (tmp_path / 'project_tasks.json').read_bytes()

    result = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline).preview()

    assert result.status is WeeklyPlanStatus.SELECTED
    by_id = {item.id: item for item in result.items}
    assert by_id['project-task:middle'].latest_end == deadline - timedelta(minutes=15)
    assert by_id['project-task:first'].latest_end == deadline - timedelta(minutes=45)
    assert (tmp_path / 'project_tasks.json').read_bytes() == before


@pytest.mark.parametrize('done_status', ['done', 'completed'])
def test_completed_dependencies_are_satisfied_but_excluded_work_is_not(tmp_path, done_status):
    save_fixture(tmp_path, tasks=[
        task(id='parent', status=done_status, estimated_hours=None),
        task(id='child', dependencies=['parent']),
    ])
    preview = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline)
    plan = preview.preview()
    assert plan.status is WeeklyPlanStatus.SELECTED
    assert [item.id for item in plan.items] == ['project-task:child']
    assert plan.items[0].dependencies == set()

    save_fixture(tmp_path, tasks=[
        task(id='parent', status='inbox', estimated_hours=None),
        task(id='child', dependencies=['parent']),
    ])
    blocked = preview.preview()
    assert blocked.status is WeeklyPlanStatus.INFEASIBLE
    assert blocked.infeasibility.unplaced_item_ids == ['project-task:child']


@pytest.mark.parametrize('project_status', ['planning', 'on_hold', 'completed', 'cancelled'])
def test_inactive_project_tasks_are_excluded_while_standalone_work_remains(tmp_path, project_status):
    save_fixture(tmp_path, projects=[project(status=project_status)], tasks=[
        task(), task(id='standalone', project_id=None, status='in_progress'),
        task(id='review', project_id=None, status='review', estimated_hours=None),
        task(id='archived', project_id=None, status='archived', estimated_hours=None),
    ])
    result = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline).preview()
    assert result.status is WeeklyPlanStatus.SELECTED
    assert [item.id for item in result.items] == ['project-task:standalone']


def test_project_start_and_task_deadline_bound_the_horizon_and_overdue_work_is_visible(tmp_path):
    begin = START + timedelta(hours=1)
    due = START + timedelta(hours=2)
    save_fixture(tmp_path, projects=[project(start_date=begin.astimezone(ZoneInfo('UTC')).isoformat())],
                 tasks=[task(due_date=due.isoformat())])
    preview = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline)
    selected = preview.preview()
    assert selected.status is WeeklyPlanStatus.SELECTED
    slots = [slot for slot in selected.selected_candidate.schedule.slots if slot.scheduled_item_id]
    assert min(slot.start for slot in slots) == begin
    assert max(slot.end for slot in slots) == due

    save_fixture(tmp_path, tasks=[task(due_date=(START - timedelta(minutes=15)).isoformat())])
    assert preview.preview().status is WeeklyPlanStatus.INFEASIBLE
    save_fixture(tmp_path, projects=[project(start_date=(START + timedelta(days=7)).isoformat())])
    assert preview.preview().items == []


def test_all_three_domains_share_one_selected_schedule_and_restart_is_stable(tmp_path):
    from services.tutoring_service import TutoringSession, TutoringMode
    from services.user_tutoring_store import UserTutoringStore

    save_fixture(tmp_path)
    UserTutoringStore(tmp_path).upsert(TutoringSession(
        'lesson', 'Synthetic student', START + timedelta(hours=2), 45, TutoringMode.ONLINE,
    ), confirmed=True)
    (tmp_path / 'natural_commands.json').write_text(json.dumps({
        'version': 1, 'tasks': [], 'executed': {}, 'personal_events': [{
            'id': 'meeting', 'summary': 'Personal meeting',
            'start': (START + timedelta(hours=1)).isoformat(),
            'end': (START + timedelta(hours=2)).isoformat(),
        }],
    }), encoding='utf-8')
    names = ['project_tasks.json', 'tutoring_sessions.json', 'natural_commands.json']
    before = {name: (tmp_path / name).read_bytes() for name in names}
    first = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline).preview()
    second = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline).preview()
    assert first.status is second.status is WeeklyPlanStatus.SELECTED
    def spans(plan):
        return [(slot.start, slot.end, slot.scheduled_item_id)
                for slot in plan.selected_candidate.schedule.slots if slot.scheduled_item_id]
    assert spans(first) == spans(second)
    assert {slot[2] for slot in spans(first)} == {
        'project-task:task', 'tutoring:lesson', 'natural-command:personal-event:meeting',
    }
    occupied = spans(first)
    assert all(previous[1] <= current[0] for previous, current in zip(occupied, occupied[1:]))
    assert before == {name: (tmp_path / name).read_bytes() for name in names}


def test_missing_estimate_and_base_id_collision_never_silently_produce_a_plan(tmp_path):
    save_fixture(tmp_path, tasks=[task(estimated_hours=None)])
    preview = PerUserWeeklyPreview(tmp_path, base_plan, pipeline_factory=pipeline)
    with pytest.raises(ValueError):
        preview.preview()
    assert 'Не удалось безопасно' in preview()

    save_fixture(tmp_path)
    base = base_plan()
    base.items.append(PlanningItem('project-task:task', 'Duplicate', '', PlanningItemType.PROJECT_TASK))
    with pytest.raises(ValueError, match='identifier'):
        PerUserWeeklyPreview(tmp_path, lambda: base, pipeline_factory=pipeline).preview()


def test_base_plan_cannot_substitute_an_excluded_owned_dependency(tmp_path):
    save_fixture(tmp_path, tasks=[
        task(id='parent', status='inbox', estimated_hours=None),
        task(id='child', dependencies=['parent']),
    ])
    base = base_plan()
    base.items.append(PlanningItem(
        'project-task:parent', 'Stale prerequisite', '', PlanningItemType.PROJECT_TASK,
        duration_minutes=30, earliest_start=START, latest_end=START + timedelta(hours=1),
    ))
    preview = PerUserWeeklyPreview(tmp_path, lambda: base, pipeline_factory=pipeline)
    with pytest.raises(ValueError, match='identifier'):
        preview.preview()


def test_project_task_updates_reload_through_owned_telegram_handlers(tmp_path):
    from models import Project, ProjectStatus
    from services.onboarding_service import OnboardingState, OnboardingStep, OnboardingStore, TelegramOnboardingService
    from services.product_state import UserProductStateStore
    from services.schedule_source_service import ScheduleSourceService
    from services.telegram_multi_user_runtime import TelegramMultiUserDispatcher
    from services.telegram_onboarding_handler import TelegramOnboardingHandler
    from services.telegram_user_router import TelegramUserRouter
    from services.user_planning_profile_store import UserPlanningProfileStore
    from services.user_project_store import UserProjectStore
    from services.user_registry import UserRegistryStore, UserStatePaths

    registry = UserRegistryStore(tmp_path / 'registry.json')
    paths = UserStatePaths(tmp_path)
    def factory(account, directory):
        onboarding = OnboardingStore(directory / 'onboarding.json')
        onboarding.save(OnboardingState(step=OnboardingStep.COMPLETE))
        return TelegramOnboardingHandler(
            account, directory, TelegramOnboardingService(onboarding),
            ScheduleSourceService(UserProductStateStore(directory / 'product_state.json')),
            lambda _: (), UserPlanningProfileStore(directory / 'planning_profile.json'),
            lambda: False, PerUserWeeklyPreview(directory, base_plan, pipeline_factory=pipeline),
        )
    runtime = TelegramMultiUserDispatcher(TelegramUserRouter(registry, paths, factory))
    def reply(chat, command='/weekly_preview'):
        return runtime.handle_update({'message': {'chat': {'id': chat}, 'text': command}})[1]['text']
    for chat in ('101', '202'):
        reply(chat, '/start')
    first_store = UserProjectStore(paths.directory(registry.resolve('101').id))
    second_store = UserProjectStore(paths.directory(registry.resolve('202').id))
    value = Project('same', 'Project', '', ProjectStatus.ACTIVE, START, START)
    first_store.upsert_project(value, confirmed=True)
    second_store.upsert_project(value, confirmed=True)
    first_task = Task('same', 'First user private task', '', 'same', TaskStatus.TODO,
                      TaskPriority.MEDIUM, START, START, estimated_hours=0.5)
    first_store.upsert_task(first_task, confirmed=True)
    second_store.upsert_task(replace(first_task, title='Second user task'), confirmed=True)
    assert 'First user private task' in reply('101')
    assert 'First user private task' not in reply('202')
    assert 'Second user task' in reply('202')
    first_store.upsert_task(replace(first_task, status=TaskStatus.DONE), confirmed=True)
    assert 'First user private task' not in reply('101')
    assert 'Second user task' in reply('202')
