"""Synthetic Telegram path from confirmed intake to an owned scheduling preview."""

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.application.command import CreateTaskCommand
from services.natural_text_application import PerUserNaturalCommandExecutor
from services.onboarding_service import OnboardingState, OnboardingStep, OnboardingStore, TelegramOnboardingService
from services.per_user_weekly_preview import PerUserWeeklyPreview
from services.product_state import UserProductStateStore
from services.schedule_source_service import ScheduleSourceService
from services.telegram_multi_user_runtime import TelegramMultiUserDispatcher
from services.telegram_natural_text_proposals import create_per_user_natural_text_flow
from services.telegram_onboarding_handler import TelegramOnboardingHandler
from services.telegram_user_router import TelegramUserRouter
from services.user_planning_profile_store import UserPlanningProfileStore
from services.user_registry import UserRegistryStore, UserStatePaths
from services.weekly_plan_service import WeeklyPlan


START = datetime(2026, 9, 22, 9, tzinfo=ZoneInfo('Asia/Tomsk'))


def runtime_for(root, *, fixed_clock=False):
    registry = UserRegistryStore(root / 'registry.json')
    paths = UserStatePaths(root)

    def factory(account, directory):
        onboarding = OnboardingStore(directory / 'onboarding.json')
        onboarding.save(OnboardingState(step=OnboardingStep.COMPLETE))
        extra = {}
        if fixed_clock:
            from services.telegram_task_planning import TelegramTaskPlanningFlow
            extra['task_planning'] = TelegramTaskPlanningFlow(
                account.telegram_chat_id, directory, now_provider=lambda: START,
            )
        return TelegramOnboardingHandler(
            account, directory, TelegramOnboardingService(onboarding),
            ScheduleSourceService(UserProductStateStore(directory / 'product_state.json')),
            lambda _: (), UserPlanningProfileStore(directory / 'planning_profile.json'),
            lambda: False,
            PerUserWeeklyPreview(directory, lambda: WeeklyPlan(START, START + timedelta(hours=6), [])),
            natural_text_proposals=create_per_user_natural_text_flow(account, directory),
            **extra,
        )

    return TelegramMultiUserDispatcher(TelegramUserRouter(registry, paths, factory)), registry, paths


def text(runtime, chat, value):
    return runtime.handle_update({'message': {'chat': {'id': chat}, 'text': value}})[1]


def callback(runtime, chat, value):
    return runtime.handle_update({'callback_query': {
        'message': {'chat': {'id': chat}}, 'data': value,
    }})[1]


def button(response, prefix):
    return next(value['callback_data'] for row in response['buttons'] for value in row
                if value.get('callback_data', '').startswith(prefix))


def test_default_owned_handler_exposes_confirmed_intake_tasks(tmp_path):
    runtime, registry, paths = runtime_for(tmp_path)
    text(runtime, '101', '/start')
    directory = paths.directory(registry.resolve('101').id)
    asyncio.run(PerUserNaturalCommandExecutor(directory / 'natural_commands.json').process_command(
        CreateTaskCommand('Купить учебник', '', command_id='synthetic-task'),
    ))

    response = text(runtime, '101', '/tasks')

    assert 'учебник' in json.dumps(response, ensure_ascii=False)
    assert not (directory / 'project_tasks.json').exists()


def test_confirmed_text_task_reaches_owned_weekly_preview_after_restart(tmp_path):
    from services.user_project_store import UserProjectStore

    runtime, registry, paths = runtime_for(tmp_path, fixed_clock=True)
    text(runtime, '101', '/start')
    text(runtime, '202', '/start')
    directory = paths.directory(registry.resolve('101').id)
    other = paths.directory(registry.resolve('202').id)
    natural = text(runtime, '101', 'Создай задачу купить учебник')
    callback(runtime, '101', button(natural, 'nl:confirm:'))
    source = (directory / 'natural_commands.json').read_bytes()

    listing = text(runtime, '101', '/tasks@personalos_bot')
    choice = callback(runtime, '101', button(listing, 'tp:pick:'))
    preview = callback(runtime, '101', button(choice, 'tp:estimate:'))
    confirmation = button(preview, 'tp:confirm:')
    assert not (directory / 'project_tasks.json').exists()
    callback(runtime, '202', confirmation)
    assert not (other / 'project_tasks.json').exists()

    restarted, _, _ = runtime_for(tmp_path, fixed_clock=True)
    callback(restarted, '101', confirmation)
    saved = (directory / 'project_tasks.json').read_bytes()
    callback(restarted, '101', confirmation)
    assert (directory / 'project_tasks.json').read_bytes() == saved
    assert (directory / 'natural_commands.json').read_bytes() == source
    _, tasks = UserProjectStore(directory).load()
    assert len(tasks) == 1
    assert tasks[0].estimated_hours > 0
    weekly = text(restarted, '101', '/weekly_preview')
    assert 'учебник' in weekly['text'].lower()
    assert 'tp:pick:' not in json.dumps(text(restarted, '101', '/tasks'))


def test_pending_task_estimate_survives_owned_backup_restore(tmp_path):
    from services.user_project_store import UserProjectStore
    from services.user_state_backup import UserStateBackupService

    runtime, registry, paths = runtime_for(tmp_path, fixed_clock=True)
    text(runtime, '101', '/start')
    user_id = registry.resolve('101').id
    directory = paths.directory(user_id)
    natural = text(runtime, '101', 'Создай задачу купить учебник')
    callback(runtime, '101', button(natural, 'nl:confirm:'))
    choice = callback(runtime, '101', button(text(runtime, '101', '/tasks'), 'tp:pick:'))
    proposal = callback(runtime, '101', button(choice, 'tp:estimate:'))
    confirmation = button(proposal, 'tp:confirm:')
    backup = UserStateBackupService(tmp_path)
    archive = tmp_path / 'synthetic.zip'
    manifest = backup.backup(user_id, archive)
    assert 'task_planning_proposal.json' in manifest['files']
    backup.lifecycle.delete(user_id, confirmed=True)
    backup.restore(user_id, archive)

    restarted, _, _ = runtime_for(tmp_path, fixed_clock=True)
    callback(restarted, '101', confirmation)
    assert len(UserProjectStore(directory).load()[1]) == 1


def test_completion_after_backup_restore_removes_task_from_next_preview(tmp_path):
    from services.user_project_store import UserProjectStore
    from services.user_state_backup import UserStateBackupService
    from models import TaskStatus

    runtime, registry, paths = runtime_for(tmp_path, fixed_clock=True)
    text(runtime, '101', '/start')
    text(runtime, '202', '/start')
    user_id = registry.resolve('101').id
    directory = paths.directory(user_id)
    natural = text(runtime, '101', 'Создай задачу купить учебник')
    callback(runtime, '101', button(natural, 'nl:confirm:'))
    choice = callback(runtime, '101', button(text(runtime, '101', '/tasks'), 'tp:pick:'))
    promotion = callback(runtime, '101', button(choice, 'tp:estimate:'))
    callback(runtime, '101', button(promotion, 'tp:confirm:'))
    assert 'учебник' in text(runtime, '101', '/weekly_preview')['text'].lower()
    listing = text(runtime, '101', '/planned_tasks@personalos_bot')
    proposal = callback(runtime, '101', button(listing, 'tp:done:'))
    confirmation = button(proposal, 'tp:confirm:')
    callback(runtime, '202', confirmation)
    assert UserProjectStore(directory).load()[1][0].status is TaskStatus.TODO
    backup = UserStateBackupService(tmp_path)
    archive = tmp_path / 'completion.zip'
    backup.backup(user_id, archive)
    backup.lifecycle.delete(user_id, confirmed=True)
    backup.restore(user_id, archive)
    restarted, _, _ = runtime_for(tmp_path, fixed_clock=True)
    callback(restarted, '101', confirmation)
    assert UserProjectStore(directory).load()[1][0].status is TaskStatus.DONE
    assert 'учебник' not in text(restarted, '101', '/weekly_preview')['text'].lower()
