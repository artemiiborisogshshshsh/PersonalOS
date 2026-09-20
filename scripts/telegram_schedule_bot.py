#!/usr/bin/env python3
"""Run the Telegram command bot for manually refreshing the TPU schedule."""

import argparse
import asyncio
import os
import ssl
import sys
from dataclasses import replace
from functools import partial
from hashlib import sha256
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_university_schedule import read_feed_url  # noqa: E402
from services.telegram_schedule_bot import TelegramScheduleBot  # noqa: E402
from services.attendance_preferences import (  # noqa: E402
    AttendancePreferenceStore,
)
from services.telegram_attendance_onboarding import (  # noqa: E402
    TelegramAttendanceOnboarding,
)
from scripts.parse_ics import parse_ics  # noqa: E402
from models import (  # noqa: E402
    EventType, PersonalAttendanceRule, PersonalEventState, UniversityEvent,
    UniversityEventStatus,
)
from services.attendance_service import AttendanceRuleService  # noqa: E402
from services.calendar.personal_event_sync_service import (  # noqa: E402
    create_personal_event_sync_service,
)
from services.personal_schedule_projection import (  # noqa: E402
    build_personal_calendar_events,
)
from services.adaptive_preparation_service import (  # noqa: E402
    AdaptivePreparationService, DraftCalendarProjector, DraftPlanSyncService,
)
from services.draft_operation_store import DraftOperationStore  # noqa: E402
from services.preparation_draft_workflow import PreparationDraftWorkflow  # noqa: E402
from services.user_planning_profile_store import (  # noqa: E402
    UserPlanningProfileStore,
)
from services.calendar_availability_service import CalendarAvailabilityService  # noqa: E402
from services.runtime_schedule import RuntimeSchedule  # noqa: E402
from services.tpu_schedule_source import fetch_tpu_group_schedule  # noqa: E402
from services.university_schedule_source import (  # noqa: E402
    UniversityScheduleFetchError, schedule_hash_from_ics,
)
from services.alfacrm_schedule_source import (  # noqa: E402
    AlfaCRMConnection, AlfaCRMError, AlfaCRMScheduleSource,
)
from services.work_schedule_service import (  # noqa: E402
    WorkPlanningStateStore, WorkPreparationPlanner, WorkPreparationWorkflow,
    WorkScheduleService,
)
from services.calendar_integrity_service import CalendarIntegrityService  # noqa: E402
from services.system_edit_service import SystemEditStore  # noqa: E402
from services.project_service import ProjectService  # noqa: E402
from services.user_state_migration import migrate_missing_user_state  # noqa: E402
from services.user_registry import UserRegistryStore, UserStatePaths  # noqa: E402
from services.onboarding_service import OnboardingStore, TelegramOnboardingService  # noqa: E402
from services.product_state import ScheduleSource, UserProductStateStore  # noqa: E402
from services.schedule_source_service import ScheduleSourceService  # noqa: E402
from services.product_analytics import ProductAnalyticsStore  # noqa: E402
from services.weekly_plan_service import FixedCommitment, CommitmentType  # noqa: E402
from services.shared_preparation_workflow import SharedPreparationWorkflow  # noqa: E402
from services.university_snapshot_store import UniversitySnapshotStore  # noqa: E402


def load_events(path: Path) -> list[UniversityEvent]:
    return [
        UniversityEvent(
            uid=item['uid'], summary=item['summary'],
            description=item['description'], location=item['location'],
            dtstart=item['dtstart'], dtend=item['dtend'],
            event_type=EventType.from_string(item['event_type'] or ''),
            is_group_event=item['is_group_event'],
            status=UniversityEventStatus.from_string(item['status']),
            sequence=item['sequence'],
        )
        for item in parse_ics(path)
    ]


def personal_schedule_events(schedule_path: Path, preferences_path: Path):
    """Return every TPU event with its personal attendance state.

    The preparation planner needs this complete list to reserve the full
    university day.  It independently creates preparation requirements only
    for CONFIRMED/MOVED events.
    """
    preferences = AttendancePreferenceStore.load(preferences_path)
    return build_personal_calendar_events(load_events(schedule_path), preferences)


def selected_events(schedule_path: Path, preferences_path: Path):
    """Events authorised for the personal university Calendar projection."""
    return [
        event for event in personal_schedule_events(schedule_path, preferences_path)
        if event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
    ]


def main() -> int:
    default_data_dir = Path(os.environ.get(
        'PERSONAL_OS_DATA_DIR', str(PROJECT_ROOT / 'data'),
    ))
    parser = argparse.ArgumentParser(
        description='Run the Telegram bot for /update_schedule.'
    )
    parser.add_argument(
        '--source-file', type=Path,
        default=PROJECT_ROOT / '01-University/03-Schedule/TPU-iCal-source.md',
    )
    parser.add_argument(
        '--output', type=Path,
        default=default_data_dir / 'university_schedule.ics',
    )
    parser.add_argument(
        '--source-url',
        default=os.environ.get('UNIVERSITY_SCHEDULE_URL'),
        help='ICS URL selected by this user; overrides --source-file.',
    )
    parser.add_argument(
        '--tpu-view-url', default=os.environ.get('TPU_SCHEDULE_VIEW_URL'),
        help='Stable TPU group page; refreshes its private iCal link automatically.',
    )
    parser.add_argument(
        '--tpu-export-variant', type=int, choices=(1, 2, 3),
        default=int(os.environ.get('TPU_SCHEDULE_EXPORT_VARIANT', '2')),
        help='TPU range: 1=week, 2=two weeks, 3=month (default: 2).',
    )
    args = parser.parse_args()
    try:
        bot = TelegramScheduleBot.from_environment(
            source_url=(args.tpu_view_url or args.source_url or read_feed_url(args.source_file)),
            output_path=args.output,
        )
    except ValueError:
        print(
            'Telegram schedule bot configuration error.',
            file=sys.stderr,
        )
        return 2
    if args.tpu_view_url:
        bot.fetcher = partial(
            fetch_tpu_group_schedule,
            export_variant_id=args.tpu_export_variant,
            auto_period=True,
        )
    runtime_data_dir = args.output.parent
    registry = UserRegistryStore(runtime_data_dir / 'users_registry.json')
    existing_account = registry.resolve(str(bot.allowed_chat_id))
    user_account = registry.get_or_create(str(bot.allowed_chat_id))
    analytics = ProductAnalyticsStore(runtime_data_dir / 'product_analytics.json')
    if existing_account is None:
        analytics.record_once(user_account.id, 'registered')
    user_data_dir = UserStatePaths(runtime_data_dir).directory(user_account.id)
    # Older single-user installs used the raw Telegram chat ID as their
    # directory. Copy only allow-listed local state into the opaque user
    # directory; credentials, databases and source files stay untouched.
    migrated_state = []
    for legacy_dir in (
        runtime_data_dir / 'users' / str(bot.allowed_chat_id),
        PROJECT_ROOT / 'data' / 'users' / str(bot.allowed_chat_id),
    ):
        for filename in migrate_missing_user_state(legacy_dir, user_data_dir):
            if filename not in migrated_state:
                migrated_state.append(filename)
    if migrated_state:
        print('Migrated portable user state: ' + ', '.join(migrated_state), flush=True)
    preferences = AttendancePreferenceStore.load(user_data_dir / 'attendance_preferences.json')
    bot.attendance_onboarding = TelegramAttendanceOnboarding(preferences)
    bot.events_loader = lambda: load_events(args.output)
    if args.output.exists():
        bot.last_schedule_hash = schedule_hash_from_ics(args.output.read_bytes())
    preferences_path = user_data_dir / 'attendance_preferences.json'
    system_edits = SystemEditStore(user_data_dir / 'system_edits.json')
    university_snapshot_store = UniversitySnapshotStore(
        user_data_dir / 'university_reconciliation.json'
    )

    def active_personal_schedule_events():
        """TPU events remaining in this user's personal plan.

        A Telegram delete is a local visibility override, not a mutation of
        the upstream TPU feed.  Hiding an event also removes it from the
        planner's hard commitments, so a deleted personal pair cannot keep
        consuming time forever.
        """
        stored_events = university_snapshot_store.personal_events()
        events = stored_events or personal_schedule_events(args.output, preferences_path)
        return [
            event for event in events
            if not system_edits.university_hidden(event.id)
        ]

    def active_selected_events():
        return [
            event for event in active_personal_schedule_events()
            if event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
        ]

    def course_and_session_type(event):
        """Read planner metadata without coupling Telegram to TPU wording."""
        course = str(event.metadata.get('course') or event.title.split('(', 1)[0]).strip()
        session_type = str(event.metadata.get('session_type') or '')
        if not session_type:
            title = event.title.casefold()
            session_type = 'lab' if 'лб' in title else 'practical' if 'пр' in title else 'lecture'
        return course, session_type

    def preparation_university_events():
        return [
            event for event in active_personal_schedule_events()
            if not system_edits.preparation_suppressed('university', event.id)
            and not system_edits.preparation_disabled(*course_and_session_type(event))
        ]

    bot.calendar_preview = lambda: '\n'.join(
        ['События для Google Calendar:'] + [
            f'• {event.start_time:%d.%m %H:%M} — {event.title}'
            for event in active_selected_events()
        ]
    )

    def apply_calendar() -> str:
        service = create_personal_event_sync_service({'calendar': {}})
        if not asyncio.run(service.calendar_adapter.initialize()):
            return 'Не удалось авторизоваться в Google Calendar.'
        events = active_selected_events()
        service.sync_personal_events_to_calendar(
            events, 'Personal University Schedule',
        )
        return f'Готово: синхронизировано {len(events)} событий.'

    bot.calendar_apply = apply_calendar
    profile_store = UserPlanningProfileStore(
        user_data_dir / 'planning_profile.json'
    )
    onboarding = TelegramOnboardingService(OnboardingStore(user_data_dir / 'onboarding.json'))
    source_registry = ScheduleSourceService(UserProductStateStore(user_data_dir / 'product_state.json'))

    def onboarding_action(data: str) -> dict:
        parts = data.split(':', maxsplit=2)
        if parts[:2] != ['ob', 'timezone'] or len(parts) != 3:
            return {'text': 'Неизвестное действие onboarding. Открой /start.', 'buttons': []}
        try:
            return onboarding.set_timezone(parts[2])
        except Exception:
            return {'text': 'Часовой пояс не принят. Открой /start и выбери вариант.', 'buttons': []}

    def apply_onboarding_timezone(timezone_name: str) -> None:
        settings = profile_store.load()
        settings.profile = replace(settings.profile, timezone=timezone_name)
        profile_store.save(settings)

    def connect_tpu_source(url: str) -> dict:
        try:
            source = ScheduleSource('tpu-primary', 'tpu_group_page', url, 'TPU')
            preview = source_registry.connect_and_preview(user_account.id, source)
            source_registry.activate(user_account.id, source.id)
            analytics.record_once(user_account.id, 'source_connected')
            bot.source_url = source.location
            bot.fetcher = partial(
                fetch_tpu_group_schedule,
                export_variant_id=args.tpu_export_variant,
                auto_period=True,
            )
            return onboarding.source_connected(source.id) | {
                'text': (f'TPU-источник проверен: {preview.event_count} событий.\n'
                         'Теперь настрой посещаемость: /attendance'),
            }
        except Exception:
            return {'text': 'Не удалось безопасно проверить TPU URL. Проверь ссылку и повтори.', 'buttons': []}

    bot.onboarding_start = onboarding.start
    bot.onboarding_action = onboarding_action
    bot.onboarding_timezone_apply = apply_onboarding_timezone
    bot.onboarding_source_connect = connect_tpu_source
    def attendance_completed():
        onboarding.attendance_completed()
        analytics.record_once(user_account.id, 'attendance_completed')

    def calendar_completed():
        onboarding.calendar_checked()

    bot.onboarding_attendance_complete = attendance_completed
    bot.onboarding_profile_complete = onboarding.profile_completed
    bot.onboarding_calendar_complete = calendar_completed
    bot.analytics_first_plan = lambda: analytics.record_once(user_account.id, 'first_plan')
    bot.analytics_weekly_active = lambda: analytics.record_once(user_account.id, 'weekly_active')

    def configured_alfacrm_source() -> tuple[AlfaCRMScheduleSource | None, str | None]:
        """Create a read-only work source only when its secrets are supplied.

        API keys deliberately stay in the host's secret store/environment; no
        credential is written to ``data/users`` or sent through Telegram.
        """
        values = {
            'ALFACRM_BASE_URL': os.environ.get('ALFACRM_BASE_URL', ''),
            'ALFACRM_BRANCH_ID': os.environ.get('ALFACRM_BRANCH_ID', ''),
            'ALFACRM_EMAIL': os.environ.get('ALFACRM_EMAIL', ''),
            'ALFACRM_API_KEY': os.environ.get('ALFACRM_API_KEY', ''),
            'ALFACRM_TEACHER_ID': os.environ.get('ALFACRM_TEACHER_ID', ''),
        }
        if not any(values.values()):
            return None, 'AlfaCRM не подключён.'
        missing = [key for key, value in values.items() if not value]
        if missing:
            return None, 'Не хватает настроек AlfaCRM: ' + ', '.join(missing)
        try:
            return AlfaCRMScheduleSource(AlfaCRMConnection(
                base_url=values['ALFACRM_BASE_URL'],
                branch_id=values['ALFACRM_BRANCH_ID'],
                email=values['ALFACRM_EMAIL'],
                api_key=values['ALFACRM_API_KEY'],
                teacher_id=values['ALFACRM_TEACHER_ID'],
                timezone=profile_store.load().profile.timezone,
            )), None
        except ValueError:
            return None, 'Некорректные настройки AlfaCRM.'

    alfacrm_source, alfacrm_configuration_error = configured_alfacrm_source()
    alfacrm_last_commitments = []
    alfacrm_last_lessons = []
    alfacrm_source_lessons = []
    alfacrm_verified_horizon = None

    def fetch_work_lessons(days: int = 30, *, start=None, end=None):
        nonlocal alfacrm_last_commitments, alfacrm_last_lessons, alfacrm_source_lessons
        nonlocal alfacrm_verified_horizon
        if alfacrm_source is None:
            raise AlfaCRMError(alfacrm_configuration_error or 'AlfaCRM не подключён.')
        now = start or datetime.now(ZoneInfo(profile_store.load().profile.timezone))
        until = end or now + timedelta(days=days)
        alfacrm_source_lessons = alfacrm_source.fetch(now, until)
        alfacrm_verified_horizon = (now, until)
        # The source remains untouched. A local Telegram hide affects only
        # the Personal OS projection and its downstream preparations.
        alfacrm_last_lessons = [
            lesson for lesson in alfacrm_source_lessons
            if not system_edits.work_hidden(lesson.id)
        ]
        alfacrm_last_commitments = [
            lesson.to_fixed_commitment() for lesson in alfacrm_last_lessons
        ]
        return alfacrm_last_lessons

    def work_schedule_preview() -> str:
        try:
            lessons = fetch_work_lessons(14)
        except AlfaCRMError:
            return 'Не удалось получить рабочее расписание AlfaCRM. Повтори попытку позже.'
        if not lessons:
            return 'AlfaCRM: запланированных рабочих занятий на ближайшие 14 дней нет.'
        return '\n'.join([
            f'AlfaCRM: рабочих занятий на ближайшие 14 дней: {len(lessons)}.',
            *[
                f'• {lesson.start:%d.%m %H:%M}–{lesson.end:%H:%M} — '
                f'{lesson.title}' + (f' ({lesson.location})' if lesson.location else '')
                for lesson in lessons
            ],
        ])

    bot.work_schedule_preview = work_schedule_preview

    def profile_preview() -> str:
        settings = profile_store.load()
        profile = settings.profile
        return (
            'Профиль планирования:\n'
            f'• Сон: {settings.sleep_start}–{settings.sleep_end}\n'
            f'• Выходные: сон до {settings.weekend_sleep_end}\n'
            f'• Дорога: {profile.travel_minutes_each_way} мин в каждую сторону\n'
            f'• Recovery после университета: {profile.recovery_minutes_after_university} мин\n'
            f'• Подготовка ЛК/ПР/ЛБ: {profile.lecture_minutes}/'
            f'{profile.practical_minutes}/{profile.lab_minutes} мин\n'
            f'• Окно подготовки: до {profile.preparation_window_days} дней до 06:00 учебного дня\n'
            f'• Срочность: за {profile.preparation_lead_hours} ч до учебного дня\n'
            '• Горизонт: текущая и следующая календарные недели\n'
            f'• Deep work: до {profile.max_continuous_deep_work_minutes} мин подряд\n\n'
            'Изменение прямо в Telegram:\n'
            '/sleep 23:00 06:40\n/sleep_weekend 08:00\n/travel 60\n/recovery 60\n'
            '/prep_durations 20 60 60\n/prep_window 7\n/prep_urgency 36\n/deep_work 120\n\n'
            'ЛК, ПР и ЛБ создаются отдельными preparation blocks.'
        )

    def profile_update(command: str, raw: str) -> str:
        nonlocal draft_workflow, work_draft_workflow
        settings = profile_store.load()
        try:
            values = raw.split()
            if command == '/sleep' and len(values) == 2:
                time.fromisoformat(values[0])
                time.fromisoformat(values[1])
                settings.sleep_start, settings.sleep_end = values
            elif command == '/sleep_weekend' and len(values) == 1:
                time.fromisoformat(values[0])
                settings.weekend_sleep_end = values[0]
            elif command == '/travel' and len(values) == 1:
                settings.profile = replace(
                    settings.profile, travel_minutes_each_way=int(values[0]),
                )
            elif command == '/prep_durations' and len(values) == 3:
                settings.profile = replace(
                    settings.profile, lecture_minutes=int(values[0]),
                    practical_minutes=int(values[1]), lab_minutes=int(values[2]),
                )
            elif command == '/recovery' and len(values) == 1:
                recovery_minutes = int(values[0])
                if recovery_minutes < 0:
                    return 'Recovery не может быть отрицательным.'
                settings.profile = replace(
                    settings.profile,
                    recovery_minutes_after_university=recovery_minutes,
                )
            elif command in {'/prep_window', '/prep_deadline'} and len(values) == 1:
                window_days = int(values[0])
                if window_days <= 0:
                    return 'Длина окна подготовки должна быть больше нуля.'
                settings.profile = replace(
                    settings.profile, preparation_window_days=window_days,
                )
            elif command == '/prep_urgency' and len(values) == 1:
                urgency_hours = int(values[0])
                if urgency_hours <= 0:
                    return 'Граница срочности должна быть больше нуля.'
                settings.profile = replace(
                    settings.profile, preparation_lead_hours=urgency_hours,
                )
            elif command == '/deep_work' and len(values) == 1:
                settings.profile = replace(
                    settings.profile, max_continuous_deep_work_minutes=int(values[0]),
                )
            else:
                return 'Неверный формат. Открой /planning_profile для примеров.'
        except ValueError:
            return 'Не удалось прочитать значение. Проверь формат времени и минут.'
        profile_store.save(settings)
        draft_workflow = None
        # Both planners close over profile settings. Recreate them so a
        # Telegram profile change is effective on the very next draft, not
        # only after the bot process restarts.
        work_draft_workflow = None
        return 'Настройки сохранены. Следующий черновой план учтёт их.'

    bot.profile_preview = profile_preview
    bot.profile_update = profile_update

    draft_service = create_personal_event_sync_service({'calendar': {}})
    from services.calendar.projection_state import CalendarProjectionState
    draft_service.projection_state = CalendarProjectionState(
        user_data_dir / 'university_calendar_projection.json'
    )
    work_service = WorkScheduleService(
        draft_service.calendar_adapter,
        WorkPlanningStateStore(user_data_dir / 'work_planning_state.json'),
    )
    draft_workflow = None
    work_draft_workflow = None

    def preparation_workflow() -> PreparationDraftWorkflow:
        nonlocal draft_workflow
        if draft_workflow is None:
            settings = profile_store.load()

            def calendar_availability():
                """Read busy intervals without changing third-party events."""
                if not ensure_draft_calendar():
                    raise RuntimeError('Не удалось проверить занятое время Google Calendar.')
                now = datetime.now(ZoneInfo(settings.profile.timezone))
                horizon = settings.profile.planning_horizon_end(now)
                availability = CalendarAvailabilityService(draft_service.calendar_adapter)
                events = availability.load(now, horizon)
                return availability.hard_commitments(events), []

            def commitments():
                nonlocal alfacrm_last_commitments
                external, _ = calendar_availability()
                now = datetime.now(ZoneInfo(settings.profile.timezone))
                personal_events = list(active_selected_events())
                if alfacrm_source is not None:
                    try:
                        fetch_work_lessons(30)
                    except AlfaCRMError as error:
                        # Never replace known work commitments with an empty
                        # result after a transient provider failure. A first
                        # sync failure is surfaced to Telegram and leaves the
                        # last approved plan intact.
                        if not alfacrm_last_commitments:
                            raise AlfaCRMError(
                                'не удалось безопасно построить подготовки: '
                                'рабочее расписание ещё не загружено'
                            ) from error
                return [
                    *settings.sleep_commitments(now, now + timedelta(days=30)),
                    *settings.routine_commitments(
                        now, now + timedelta(days=30), personal_events,
                    ),
                    *alfacrm_last_commitments,
                    *[FixedCommitment(
                        f'work-recovery:{lesson.id}', 'Восстановление после работы',
                        lesson.end, lesson.end + timedelta(minutes=30), CommitmentType.RECOVERY,
                    ) for lesson in alfacrm_last_lessons],
                    *external,
                ]

            def flexible_items():
                # Replanning may replace only its own preparation operation.
                # It never displaces a Google event, project task or lesson.
                return []

            def apply_profile_estimate(session_type: str, minutes: int) -> None:
                field_by_type = {
                    'lecture': 'lecture_minutes',
                    'practical': 'practical_minutes',
                    'lab': 'lab_minutes',
                }
                field = field_by_type[session_type]
                current = profile_store.load()
                current.profile = replace(current.profile, **{field: minutes})
                profile_store.save(current)

            draft_workflow = PreparationDraftWorkflow(
                planner=AdaptivePreparationService(settings.profile),
                draft_sync=DraftPlanSyncService(),
                calendar_projector=DraftCalendarProjector(draft_service.calendar_adapter),
                operation_store=DraftOperationStore(
                    user_data_dir / 'draft_operations.json'
                ),
                # Pass every TPU event so gaps between university classes can
                # never become preparation slots.  The domain service filters
                # attendance before it creates any preparation requirement.
                events_provider=preparation_university_events,
                commitments_provider=commitments,
                flexible_items_provider=flexible_items,
                profile_estimate_apply=apply_profile_estimate,
            )
        return draft_workflow

    def preview_preparations() -> str:
        if alfacrm_source is not None:
            return with_draft_calendar(lambda: '\n\n'.join(rebuild_shared_preparation_queue()))
        workflow = preparation_workflow()
        # A previous process may have left a draft that was valid when it was
        # made but now intersects a newly synced AlfaCRM lesson or its
        # recovery window.  Repair only events that carry an app marker (or
        # are explicitly present in an app snapshot), then rebuild instead of
        # showing a misleading preview.
        try:
            integrity, _ = audit_preparation_calendar()
        except (AlfaCRMError, RuntimeError):
            integrity = None
        if integrity and integrity.repaired:
            try:
                preview = workflow.replan(source_changed=True)
            except AlfaCRMError:
                return 'Подготовки не пересчитаны из-за недоступности AlfaCRM. Попробуй позже.'
            if (workflow.current_operation and workflow.current_operation.blocks
                    and not workflow.current_operation.calendar_event_ids):
                if ensure_draft_calendar():
                    workflow.stage()
            return (
                'Некорректные системные черновики удалены и построены заново. '
                'Пары и личные события не изменялись.\n\n' + preview
            )
        try:
            # This is an explicit request to calculate preparations. A saved
            # draft is only a snapshot; reuse would ignore changed rules,
            # AlfaCRM lessons and Calendar busy time.
            preview = (
                workflow.replan(source_changed=True)
                if workflow.current_operation and workflow.current_operation.status == 'draft'
                else workflow.preview()
            )
        except AlfaCRMError:
            return 'Подготовки не пересчитаны из-за недоступности AlfaCRM. Попробуй позже.'
        operation = workflow.current_operation
        if operation and operation.blocks and not operation.calendar_event_ids:
            if ensure_draft_calendar():
                return workflow.stage()
            return preview + '\n\nGoogle Calendar пока недоступен: черновики сохранены локально.'
        return preview

    def stage_preparations() -> str:
        if not ensure_draft_calendar():
            return 'Не удалось подключиться к Google Calendar; черновики не созданы.'
        return preparation_workflow().stage()

    def ensure_draft_calendar() -> bool:
        if draft_service.calendar_adapter.is_initialized:
            return True
        return asyncio.run(draft_service.calendar_adapter.initialize())

    def shared_calendar_availability():
        """Read every visible Calendar event as an immutable busy interval."""
        if not ensure_draft_calendar():
            raise RuntimeError('Не удалось проверить занятое время Google Calendar.')
        profile = profile_store.load().profile
        now = datetime.now(ZoneInfo(profile.timezone))
        availability = CalendarAvailabilityService(draft_service.calendar_adapter)
        events = availability.load(now, profile.planning_horizon_end(now))
        return availability.hard_commitments(events)

    def work_preparation_workflow() -> WorkPreparationWorkflow:
        nonlocal work_draft_workflow
        if work_draft_workflow is None:
            settings = profile_store.load()

            def work_commitments():
                external = shared_calendar_availability()
                now = datetime.now(ZoneInfo(settings.profile.timezone))
                return [
                    *settings.sleep_commitments(now, now + timedelta(days=30)),
                    *settings.routine_commitments(
                        now, now + timedelta(days=30),
                        list(active_selected_events()),
                    ),
                    *external,
                ]

            work_draft_workflow = WorkPreparationWorkflow(
                # Work preparations are deliberately bundled into one
                # uninterrupted session where a common free window exists.
                WorkPreparationPlanner(
                    work_service,
                    break_minutes=0,
                    max_continuous_minutes=settings.profile.max_continuous_deep_work_minutes,
                    series_break_minutes=settings.profile.preparation_break_minutes,
                ),
                DraftCalendarProjector(draft_service.calendar_adapter),
                DraftOperationStore(user_data_dir / 'work_draft_operations.json'),
                lessons_provider=lambda: [
                    lesson for lesson in alfacrm_last_lessons
                    if not system_edits.preparation_suppressed('work', lesson.id)
                ],
                university_provider=active_personal_schedule_events,
                commitments_provider=work_commitments,
            )
        return work_draft_workflow

    def work_needs_route(lesson) -> bool:
        if work_service.mode_for(lesson) != 'offline' or lesson.id in work_service.state.routes:
            return False
        attended = [event for event in active_selected_events()
                    if event.start_time.date() == lesson.start.date()
                    and event.end_time <= lesson.start]
        return bool(attended)

    def immutable_work_conflicts(lessons):
        """Report source-on-source overlaps; neither source may be moved."""
        conflicts = []
        for lesson in lessons:
            for event in active_selected_events():
                if lesson.start < event.end_time and lesson.end > event.start_time:
                    conflicts.append((lesson, event))
        return conflicts

    def sync_work_schedule() -> dict:
        if not ensure_draft_calendar():
            raise RuntimeError('Не удалось подключиться к Google Calendar.')
        lessons = fetch_work_lessons(30)
        result = work_service.sync(lessons, verified_horizon=alfacrm_verified_horizon)
        result['overlapping_preparations'] = work_service.remove_overlapping_work_preparations(
            lessons,
        )
        # A university preparation may be an older projection in a different
        # calendar. Audit every owned preparation against both immutable TPU
        # and AlfaCRM lessons whenever work is refreshed.
        integrity, _ = audit_preparation_calendar(lessons=lessons)
        result['global_repaired_preparations'] = integrity.repaired
        return result

    def work_recovery_intervals(lessons):
        """Every work lesson reserves 30 minutes after it for every planner."""
        return [
            (lesson.end, lesson.end + timedelta(minutes=30))
            for lesson in lessons
        ]

    def known_preparation_event_ids() -> set[str]:
        """Return only IDs recorded by Personal OS operation snapshots."""
        event_ids: set[str] = set()
        for filename in ('draft_operations.json', 'work_draft_operations.json'):
            for operation in DraftOperationStore(user_data_dir / filename).load_all().values():
                event_ids.update(str(event_id) for event_id in operation.calendar_event_ids.values())
        return event_ids

    def audit_preparation_calendar(lessons=None, verify_work_sources: bool = True):
        """Audit drafts against every immutable class and recovery interval."""
        if not ensure_draft_calendar():
            raise RuntimeError('Не удалось подключиться к Google Calendar.')
        verified = verify_work_sources
        if lessons is None:
            try:
                lessons = fetch_work_lessons(30) if alfacrm_source is not None else []
            except AlfaCRMError:
                lessons = list(alfacrm_last_lessons)
                verified = bool(lessons)
        profile = profile_store.load().profile
        now = datetime.now(ZoneInfo(profile.timezone))
        from services.preparation_integrity import calendar_horizon

        report = CalendarIntegrityService(draft_service.calendar_adapter).inspect_and_cleanup(
            active_personal_schedule_events(), lessons,
            now=now,
            verify_work_sources=verified,
            protected_intervals=work_recovery_intervals(lessons),
            owned_event_ids=known_preparation_event_ids(),
            horizon=calendar_horizon(now, profile.timezone),
            verified_work_horizon=alfacrm_verified_horizon if verified else None,
        )
        return report, lessons

    def rebuild_shared_preparation_queue() -> tuple[str, str]:
        """Build one deterministic Saturday queue: university, then work."""
        # Validate source and Calendar access before rolling back any draft.
        if alfacrm_source is not None:
            fetch_work_lessons(30)
        list(preparation_university_events())
        shared_calendar_availability()
        queue = SharedPreparationWorkflow(
            user_data_dir / 'shared_preparation.json',
            preparation_workflow(), work_preparation_workflow(),
        )
        from services.preparation_integrity import calendar_horizon

        def authorize_retirement(scope, source_id):
            work_scope = scope == 'work-preparation'
            if work_scope:
                prep_id = 'work-prep:' + sha256(source_id.encode()).hexdigest()[:20]
                if work_service.state.preparation_feedback.get(prep_id, {}).get('outcome') == 'done':
                    return None
            excluded = system_edits.preparation_suppressed(
                'work' if work_scope else 'university', source_id,
            )
            hidden = (system_edits.work_hidden(source_id) if work_scope
                      else system_edits.university_hidden(source_id))
            if excluded or hidden:
                return 'user_excluded'
            if (work_scope and source_id in work_service.state.cancelled_lessons
                    and not any(lesson.id == source_id for lesson in alfacrm_source_lessons)):
                return 'source_cancelled'
            return None

        timezone = profile_store.load().profile.timezone
        horizon = calendar_horizon(datetime.now(ZoneInfo(timezone)), timezone)
        queue.retire_retained(queue.study, authorize_retirement, horizon, study=True)
        queue.retire_retained(queue.work, authorize_retirement, horizon)
        return queue.run()

    def ensure_university_schedule_file() -> str:
        """Bootstrap a fresh portable data directory before planning work.

        Work preparation cannot be safely placed without university lessons.
        On a new machine/data directory, fetch the configured TPU source once
        rather than exposing an internal FileNotFoundError in Telegram.
        """
        if args.output.exists():
            return ''
        result = bot.fetcher(bot.source_url, args.output)
        bot.last_schedule_hash = result.content_hash
        return f'Сначала загружено TPU-расписание: {result.event_count} событий.\n'

    def work_schedule_flow():
        try:
            bootstrap = ensure_university_schedule_file()
        except (UniversityScheduleFetchError, ValueError):
            return (
                'Не удалось получить TPU-расписание для безопасного планирования '
                'работы. Попробуй /update_schedule.'
            )
        try:
            result = sync_work_schedule()
        except (AlfaCRMError, RuntimeError):
            return 'Не удалось получить рабочее расписание AlfaCRM. Повтори попытку позже.'
        lines = [
            *([bootstrap] if bootstrap else []),
            f'AlfaCRM: занятий на ближайшие 30 дней: {len(alfacrm_last_lessons)}.',
            f'Календарь «Работа»: создано {result["created"]}, обновлено {result["updated"]}, удалено {result["deleted"]}.',
        ]
        if result['overlapping_preparations']:
            lines.append(
                f'Найдены пересекающиеся системные подготовки: '
                f'{result["overlapping_preparations"]}. Они сохранены; '
                'для изменения выбери явное перепланирование.'
            )
        if result['global_repaired_preparations']:
            lines.append(
                f'Убрано системных подготовок с пересечением или неверным источником: '
                f'{result["global_repaired_preparations"]}. Пары не изменялись.'
            )
        for lesson, event in immutable_work_conflicts(alfacrm_last_lessons):
            lines.append(
                f'⚠️ Конфликт источников: работа «{lesson.display_name}» '
                f'({lesson.start:%d.%m %H:%M}–{lesson.end:%H:%M}) пересекается '
                f'с ВУЗом «{event.title}». Пары не сдвигались; нужна ручная договорённость.'
            )
        incomplete = [lesson for lesson in alfacrm_last_lessons
                      if not lesson.subject or not (lesson.group or lesson.students)]
        if incomplete:
            lines.append(
                'AlfaCRM не передал предмет, группу или учеников для части занятий; '
                'использовано безопасное название «Занятие AlfaCRM». '
                'Проверь карточки в AlfaCRM, если нужны полные подписи.'
            )
        buttons = []
        unknown = work_service.unknown_mode_lessons(alfacrm_last_lessons)
        if unknown:
            lesson = unknown[0]
            lines.append(f'Уточни формат: {lesson.display_name}, {lesson.start:%d.%m %H:%M}.')
            buttons.append([
                {'text': 'Онлайн', 'callback_data': f'work:mode:{lesson.id}:online'},
                {'text': 'Офлайн', 'callback_data': f'work:mode:{lesson.id}:offline'},
            ])
        else:
            route_lessons = [lesson for lesson in alfacrm_last_lessons if work_needs_route(lesson)]
            if route_lessons:
                lesson = route_lessons[0]
                lines.append(f'После университета перед «{lesson.display_name}» заедешь домой?')
                buttons.append([
                    {'text': 'Еду сразу', 'callback_data': f'work:route:{lesson.id}:direct'},
                    {'text': 'Заеду домой', 'callback_data': f'work:route:{lesson.id}:home'},
                ])
            else:
                _, preview = rebuild_shared_preparation_queue()
                lines.append(preview)
                lines.append(
                    'Субботняя очередь: сначала университет, затем работа. '
                    'Пары и личные события не перемещались.'
                )
                buttons.append([
                    {'text': 'Подтвердить подготовки', 'callback_data': 'work:prep:confirm'},
                    {'text': 'Откатить подготовки', 'callback_data': 'work:prep:rollback'},
                ])
        return {'text': '\n'.join(lines), 'buttons': buttons}

    bot.work_schedule_preview = work_schedule_flow

    def work_mode_apply(lesson_id: str, mode: str) -> str:
        lesson = next((item for item in alfacrm_last_lessons if item.id == lesson_id), None)
        if lesson is None:
            fetch_work_lessons(30)
            lesson = next((item for item in alfacrm_last_lessons if item.id == lesson_id), None)
        if lesson is None:
            return 'Эта рабочая пара уже исчезла из AlfaCRM.'
        work_service.set_mode(lesson, mode)
        # Update the description immediately, preserving source ownership.
        work_service.sync(alfacrm_last_lessons)
        return f'Формат «{lesson.display_name}» сохранён: {"онлайн" if mode == "online" else "офлайн"}. Открой /work_schedule для маршрута и подготовок.'

    def work_route_apply(lesson_id: str, route: str) -> str:
        work_service.set_route(lesson_id, route)
        return 'Маршрут сохранён. Открой /work_schedule: подготовка будет построена с учётом дороги.'

    def work_feedback_complete(lesson_id: str, outcome: str, homework: str,
                               deadline: str, comment: str) -> str:
        work_service.record_feedback(lesson_id, outcome, homework, deadline, comment)
        if alfacrm_last_lessons:
            work_service.sync(alfacrm_last_lessons)
        return 'Отчёт сохранён в приложении и в системном событии «Работа». AlfaCRM не изменялся.'

    bot.work_mode_apply = work_mode_apply
    bot.work_route_apply = work_route_apply
    bot.work_feedback_complete = work_feedback_complete
    bot.work_preparation_confirm = lambda: work_preparation_workflow().confirm()
    bot.work_preparation_rollback = lambda: work_preparation_workflow().rollback()

    def work_preparation_feedback_preview() -> dict:
        operation = work_preparation_workflow().current_operation
        if operation is None or not operation.blocks:
            return {'text': 'Нет рабочих preparation blocks для feedback.', 'buttons': []}
        return {
            'text': 'Как прошла подготовка к рабочей паре?',
            'buttons': [[
                {'text': 'Сделал', 'callback_data': f'wpfb:{block.id}:done'},
                {'text': 'Частично', 'callback_data': f'wpfb:{block.id}:partial'},
                {'text': 'Не сделал', 'callback_data': f'wpfb:{block.id}:skipped'},
            ] for block in operation.blocks],
        }

    def work_preparation_feedback_detail(block_id: str, outcome: str, minutes: int,
                                         difficulty: int, comment: str) -> str:
        work_service.record_preparation_feedback(block_id, outcome, minutes, difficulty, comment)
        return 'Feedback рабочей подготовки сохранён; следующий replan учтёт сложность и фактическое время.'

    bot.work_preparation_feedback_preview = work_preparation_feedback_preview
    bot.work_preparation_feedback_detail = work_preparation_feedback_detail

    def calendar_health() -> str:
        if not ensure_draft_calendar():
            return 'Не удалось подключиться к Google Calendar; ничего не изменено.'
        work_sources_verified = True
        try:
            lessons = fetch_work_lessons(30) if alfacrm_source is not None else []
        except AlfaCRMError:
            # Do not guess that a work preparation is stale while the source
            # is unavailable; university-only cleanup remains safe.
            lessons = list(alfacrm_last_lessons)
            work_sources_verified = bool(lessons)
        report, _ = audit_preparation_calendar(
            lessons=lessons, verify_work_sources=work_sources_verified,
        )
        if report.repaired:
            rebuilt = []
            # Health has removed only an invalid owned preparation. Rebuild
            # the affected draft using the now-strict busy timeline; fixed
            # lessons themselves are never touched.
            try:
                rebuilt.append(replan_after_schedule_change())
                if lessons:
                    work_reply = work_preparation_workflow().replan()
                    if (work_preparation_workflow().current_operation
                            and work_preparation_workflow().current_operation.blocks):
                        work_preparation_workflow().stage()
                    rebuilt.append(work_reply)
            except Exception:
                rebuilt.append('Черновики не удалось пересчитать автоматически. '
                               'Повтори /preparations после проверки Calendar.')
            return report.render() + '\n\nЧерновики пересчитаны с учётом фиксированных пар.\n' + '\n'.join(rebuilt)
        return report.render()

    bot.calendar_health = calendar_health

    def system_events_preview() -> str:
        """Show only Personal OS-owned objects with compact edit codes."""
        if alfacrm_source is not None and not alfacrm_source_lessons:
            try:
                fetch_work_lessons(30)
            except AlfaCRMError:
                # The university and preparation inventory is still useful;
                # never turn a read-only source outage into a failed edit UI.
                pass
        preparation_blocks = []
        university_operation = preparation_workflow().current_operation
        if university_operation is not None:
            preparation_blocks.extend(('university', block) for block in university_operation.blocks)
        work_operation = work_preparation_workflow().current_operation
        if work_operation is not None:
            preparation_blocks.extend(('work', block) for block in work_operation.blocks)
        records = system_edits.refresh_inventory(
            [
                event for event in personal_schedule_events(args.output, preferences_path)
                if event.state in {PersonalEventState.CONFIRMED, PersonalEventState.MOVED}
            ],
            alfacrm_source_lessons or alfacrm_last_lessons,
            preparation_blocks,
        )
        return system_edits.render(records)

    def delete_university_projection(event_id: str) -> None:
        """Delete the exact app projection identified by its stable iCal UID."""
        from services.calendar.projection_state import delete_owned_verified

        calendar_id = draft_service.calendar_adapter._get_or_create_calendar(
            'Personal University Schedule',
        )
        try:
            remote = draft_service.calendar_adapter.get_event_by_uid(
                calendar_id, event_id, strict=True)
        except Exception:
            raise RuntimeError('Calendar: удаление не выполнено, чтение недоступно.') from None
        if not remote:
            draft_service.projection_state.put(event_id, override='deleted', event_id=None)
            return
        if not draft_service._owned(remote, event_id):
            raise RuntimeError('Calendar: удаление остановлено, владелец события не подтверждён.')
        delete_owned_verified(
            draft_service.calendar_adapter, calendar_id, str(remote['id']),
            lambda value: draft_service._owned(value, event_id),
        )
        draft_service.projection_state.put(event_id, override='deleted', event_id=None,
                                           pending=None)

    def rebuild_after_system_edit(kind: str) -> list[str]:
        """Recompute only app preparations after a deliberate local override."""
        reports: list[str] = []
        if kind in {'university', 'university-preparation'}:
            university_reply = preparation_workflow().replan(source_changed=True)
            operation = preparation_workflow().current_operation
            if operation and operation.blocks and not operation.calendar_event_ids:
                preparation_workflow().stage()
            reports.append('Подготовки к ВУЗу пересчитаны.')
        if kind in {'work', 'work-preparation'}:
            if alfacrm_source is not None:
                fetch_work_lessons(30)
                work_service.sync(alfacrm_last_lessons)
            work_reply = work_preparation_workflow().replan()
            operation = work_preparation_workflow().current_operation
            if operation and operation.blocks and not operation.calendar_event_ids:
                work_preparation_workflow().stage()
            reports.append('Подготовки к работе пересчитаны.')
        return reports

    def system_delete(code: str) -> str:
        if not ensure_draft_calendar():
            return 'Не удалось подключиться к Google Calendar; ничего не изменено.'
        record = system_edits.record(code)
        if record is None:
            return 'Неизвестный код. Открой /system_events и используй код из свежего списка.'
        if record.hidden:
            return 'Это событие уже скрыто. Чтобы вернуть его, используй /system_restore ' + record.code
        system_edits.set_hidden(record.kind, record.source_id, True)
        removed_preparations = system_edits.delete_owned_preparations(
            draft_service.calendar_adapter, record.source_id,
        )
        if record.kind == 'university':
            delete_university_projection(record.source_id)
        elif record.kind == 'work':
            # Keep AlfaCRM read-only: filtering the next local projection
            # removes only the owned Google event, never the source lesson.
            fetch_work_lessons(30)
            work_service.sync(alfacrm_last_lessons)
        reports = rebuild_after_system_edit(record.kind)
        labels = {
            'university': 'Пара ВУЗа скрыта из личного календаря',
            'work': 'Рабочая пара скрыта из личного календаря',
            'university-preparation': 'Подготовка к ВУЗу удалена из плана',
            'work-preparation': 'Подготовка к работе удалена из плана',
        }
        suffix = f' Удалено связанных системных подготовок: {removed_preparations}.' if removed_preparations else ''
        return labels[record.kind] + '.' + suffix + '\n' + '\n'.join(reports) + (
            f'\nВернуть: /system_restore {record.code}'
        )

    def system_restore(code: str) -> str:
        if not ensure_draft_calendar():
            return 'Не удалось подключиться к Google Calendar; ничего не изменено.'
        record = system_edits.record(code)
        if record is None:
            return 'Неизвестный код. Открой /system_events и используй код из свежего списка.'
        if not record.hidden:
            return 'Это событие уже активно в личном плане.'
        system_edits.set_hidden(record.kind, record.source_id, False)
        if record.kind == 'university':
            apply_calendar()
        elif record.kind == 'work':
            fetch_work_lessons(30)
            work_service.sync(alfacrm_last_lessons)
        reports = rebuild_after_system_edit(record.kind)
        return 'Событие возвращено в личный план.\n' + '\n'.join(reports)

    bot.system_events_preview = system_events_preview
    bot.system_delete = system_delete
    bot.system_restore = system_restore

    def parse_preparation_rule(raw: str) -> tuple[str, str] | None:
        values = raw.split()
        session_type = 'all'
        if values and SystemEditStore.normalise_session_type(values[-1]) in {
            'lecture', 'practical', 'lab', 'all',
        }:
            session_type = SystemEditStore.normalise_session_type(values.pop())
        course = ' '.join(values).strip()
        if not course:
            return None
        known_courses = {
            SystemEditStore.normalise_course(course_name): course_name
            for course_name, _ in (
                course_and_session_type(event)
                for event in personal_schedule_events(args.output, preferences_path)
            )
        }
        resolved = known_courses.get(SystemEditStore.normalise_course(course))
        if resolved is None:
            return None
        return resolved, session_type

    def preparation_rules_preview() -> str:
        rules = system_edits.preparation_rules()
        if not rules:
            return (
                'Постоянных исключений для preparation нет.\n\n'
                'Отключить все подготовки предмета:\n'
                '/disable_preparation Архитектура ИС\n\n'
                'Отключить только лабораторные:\n'
                '/disable_preparation Архитектура ИС ЛБ'
            )
        labels = {'all': 'все типы', 'lecture': 'ЛК', 'practical': 'ПР', 'lab': 'ЛБ'}
        lines = ['Постоянно отключённые подготовки:']
        lines.extend(f'• {course} — {labels.get(session_type, session_type)}'
                     for course, session_type in rules)
        lines.extend(['', 'Вернуть: /enable_preparation НАЗВАНИЕ [ЛК|ПР|ЛБ|все]'])
        return '\n'.join(lines)

    def change_preparation_rule(raw: str, disabled: bool) -> str:
        parsed = parse_preparation_rule(raw)
        if parsed is None:
            courses = sorted({course_and_session_type(event)[0]
                              for event in personal_schedule_events(args.output, preferences_path)})
            return (
                'Не нашёл такой предмет в текущем TPU-расписании. Доступны: '
                + ', '.join(courses)
            )
        course, session_type = parsed
        if not ensure_draft_calendar():
            return 'Не удалось подключиться к Google Calendar; правило не изменено.'
        system_edits.set_preparation_rule(course, session_type, disabled)
        affected = [
            event for event in active_personal_schedule_events()
            if course_and_session_type(event)[0].casefold() == course.casefold()
            and (session_type == 'all' or course_and_session_type(event)[1] == session_type)
        ]
        removed = 0
        if disabled:
            for event in affected:
                removed += system_edits.delete_owned_preparations(
                    draft_service.calendar_adapter, event.id,
                )
        rebuild_after_system_edit('university-preparation')
        labels = {'all': 'всех типов', 'lecture': 'ЛК', 'practical': 'ПР', 'lab': 'ЛБ'}
        if disabled:
            return (
                f'Подготовки «{course}» ({labels[session_type]}) отключены навсегда. '
                f'Удалено системных блоков: {removed}. Пары и посещаемость не менялись.'
            )
        return (
            f'Подготовки «{course}» ({labels[session_type]}) снова разрешены. '
            'План пересчитан; подходящие блоки появятся как черновики.'
        )

    bot.preparation_rules_preview = preparation_rules_preview
    bot.preparation_rule_disable = lambda raw: change_preparation_rule(raw, True)
    bot.preparation_rule_enable = lambda raw: change_preparation_rule(raw, False)

    def management_home() -> dict:
        return {
            'text': (
                'Центр управления расписанием.\n\n'
                'Здесь меняются только правила и системные проекции Personal OS. '
                'TPU, AlfaCRM и личные события Google Calendar не редактируются.'
            ),
            'buttons': [
                [
                    {'text': '🎓 Учёба', 'callback_data': 'manage:study'},
                    {'text': '💼 Работа', 'callback_data': 'manage:work'},
                ],
                [
                    {'text': '🧠 Подготовки', 'callback_data': 'manage:preparations'},
                    {'text': '📁 Проекты', 'callback_data': 'manage:projects'},
                ],
                [{'text': '🗓 Системные события', 'callback_data': 'manage:events'}],
            ],
        }

    def management_courses() -> list[str]:
        return sorted({
            course_and_session_type(event)[0]
            for event in personal_schedule_events(args.output, preferences_path)
        }, key=str.casefold)

    def management_course_screen(index: int, prefix: str = '') -> dict:
        courses = management_courses()
        if index < 0 or index >= len(courses):
            return {
                'text': 'Список предметов изменился. Выбери предмет заново.',
                'buttons': [[{'text': 'К предметам', 'callback_data': 'manage:study'}]],
            }
        course = courses[index]
        labels = [('all', 'Все подготовки'), ('lecture', 'ЛК'), ('practical', 'ПР'), ('lab', 'ЛБ')]
        buttons = []
        for session_type, label in labels:
            disabled = system_edits.preparation_disabled(course, session_type)
            action = 'enable' if disabled else 'disable'
            title = ('✅ Вернуть ' if disabled else '🚫 Убрать ') + label
            buttons.append([{
                'text': title,
                'callback_data': f'manage:course:{index}:{action}:{session_type}',
            }])
        buttons.append([{'text': '← К предметам', 'callback_data': 'manage:study'}])
        rules = []
        for session_type, label in labels:
            if system_edits.preparation_disabled(course, session_type):
                rules.append(label)
        state = ', '.join(rules) if rules else 'нет исключений'
        return {
            'text': (prefix + '\n\n' if prefix else '') +
                    f'Настройки подготовок: {course}\nОтключено: {state}.\n\n'
                    'Нажатие сразу пересчитает черновики, но не изменит пары.',
            'buttons': buttons,
        }

    def management_action(data: str) -> dict:
        if data == 'manage:home':
            return management_home()
        if data == 'manage:study':
            courses = management_courses()
            return {
                'text': 'Учёба: выбери предмет для настройки preparation.',
                'buttons': [
                    [{'text': course, 'callback_data': f'manage:course:{index}'}]
                    for index, course in enumerate(courses)
                ] + [[{'text': '← Назад', 'callback_data': 'manage:home'}]],
            }
        if data == 'manage:preparations':
            return {
                'text': preparation_rules_preview(),
                'buttons': [
                    [{'text': 'Настроить предметы', 'callback_data': 'manage:study'}],
                    [{'text': '← Назад', 'callback_data': 'manage:home'}],
                ],
            }
        if data == 'manage:work':
            if alfacrm_source is not None and not alfacrm_source_lessons:
                try:
                    fetch_work_lessons(30)
                except AlfaCRMError:
                    pass
            lessons = alfacrm_source_lessons or alfacrm_last_lessons
            if lessons:
                text = 'Рабочие занятия AlfaCRM (read-only):\n' + '\n'.join(
                    f'• {lesson.start:%d.%m %H:%M} — {lesson.display_name}'
                    for lesson in lessons
                )
            else:
                text = 'Рабочих занятий пока нет или AlfaCRM не подключён.'
            return {
                'text': text + '\n\nСкрыть системную проекцию можно в «Системных событиях».',
                'buttons': [
                    [{'text': 'Системные события', 'callback_data': 'manage:events'}],
                    [{'text': '← Назад', 'callback_data': 'manage:home'}],
                ],
            }
        if data == 'manage:projects':
            try:
                projects = ProjectService().list_projects()
            except Exception:
                projects = []
            text = ('Проекты:\n' + '\n'.join(
                f'• {project.name} — {project.status.value}' for project in projects
            )) if projects else (
                'В домене Personal OS пока нет проектов. Когда появятся project tasks, '
                'они будут показаны здесь и попадут в тот же безопасный календарный контроль.'
            )
            return {
                'text': text,
                'buttons': [[{'text': '← Назад', 'callback_data': 'manage:home'}]],
            }
        if data == 'manage:events':
            return {
                'text': system_events_preview(),
                'buttons': [[{'text': '← Назад', 'callback_data': 'manage:home'}]],
            }
        parts = data.split(':')
        if len(parts) >= 3 and parts[1] == 'course':
            try:
                index = int(parts[2])
            except ValueError:
                return management_home()
            if len(parts) == 3:
                return management_course_screen(index)
            if len(parts) == 5 and parts[3] in {'enable', 'disable'}:
                courses = management_courses()
                if index < 0 or index >= len(courses):
                    return management_action('manage:study')
                course = courses[index]
                session_type = parts[4]
                labels = {'all': 'все', 'lecture': 'ЛК', 'practical': 'ПР', 'lab': 'ЛБ'}
                reply = change_preparation_rule(
                    f'{course} {labels.get(session_type, session_type)}',
                    parts[3] == 'disable',
                )
                return management_course_screen(index, reply)
        return management_home()

    bot.management_home = management_home
    bot.management_action = management_action

    def is_transient_calendar_error(error: Exception) -> bool:
        return isinstance(error, (ssl.SSLError, ConnectionError, TimeoutError)) or (
            'decryption failed' in str(error).casefold()
        )

    def with_draft_calendar(action) -> str:
        if not ensure_draft_calendar():
            return 'Не удалось подключиться к Google Calendar; текущие черновики не изменены.'
        try:
            return action()
        except Exception as error:
            if not is_transient_calendar_error(error):
                raise
            # Reads already have bounded retries. A whole-action retry could
            # repeat writes with an ambiguous result and multiply that budget.
            return ('Связь с Google Calendar прервалась. Часть изменений могла сохраниться; '
                    'повтори /update_all для сверки и продолжения.')

    bot.preparation_preview = preview_preparations
    from services.update_all_workflow import UpdateAllWorkflow, UpdateAllBlocked
    from services.preparation_integrity import (
        assess_preparation_infeasibility, calendar_horizon, PreparationExpectation,
        PreparationState, validate_preparations,
    )

    update_context = {}

    def reconcile_downloaded_university_source():
        """Apply the verified ICS file to durable personal state once."""
        if not args.output.exists():
            return None
        settings = profile_store.load().profile
        zone = ZoneInfo(settings.timezone)

        def local_time(value):
            return value.astimezone(zone) if value.tzinfo else value.replace(tzinfo=zone)

        source_events = [replace(
            event, dtstart=local_time(event.dtstart), dtend=local_time(event.dtend),
        ) for event in load_events(args.output)]
        return university_snapshot_store.reconcile(
            source_events, AttendanceRuleService(PersonalAttendanceRule(
                id='telegram-attendance-preferences',
                description='Saved Telegram attendance choices',
                metadata={'attendance_preferences': preferences.as_rule_metadata()},
            )),
            required_horizon=calendar_horizon(datetime.now(zone), settings.timezone),
        )

    bot.schedule_refresh_reconcile = reconcile_downloaded_university_source

    def reconciliation_review():
        """Show only durable, source-verified replacement choices.

        Choosing one changes the local PersonalUniversityEvent identity; it
        deliberately does not write Calendar.  The normal /update_all flow
        remains responsible for the differential projection afterwards.
        """
        reviews = sorted(
            (event for event in university_snapshot_store.personal_events()
             if event.state == PersonalEventState.NEEDS_REVIEW),
            key=lambda event: (event.start_time, event.id),
        )
        possible_cancellations = [
            event for event in university_snapshot_store.personal_events()
            if event.state == PersonalEventState.POSSIBLY_CANCELLED
        ]
        if not reviews and not possible_cancellations:
            return {'text': 'Неоднозначных изменений TPU-расписания нет.', 'buttons': []}
        lines, buttons = [], []
        for event_index, event in enumerate(reviews):
            candidates = event.metadata.get('replacement_candidates', [])
            if not isinstance(candidates, list) or not candidates:
                lines.append(
                    f'• {event.title} ({event.start_time:%d.%m %H:%M}) требует ручной проверки.'
                )
                continue
            lines.append(
                f'• Выбери замену для «{event.title}» ({event.start_time:%d.%m %H:%M}):'
            )
            for candidate_index, candidate in enumerate(candidates):
                if not isinstance(candidate, dict):
                    continue
                label = str(candidate.get('title') or 'Занятие TPU')
                start = str(candidate.get('start_time') or '')[:16].replace('T', ' ')
                buttons.append([{
                    'text': f'{label} {start}'.strip()[:60],
                    'callback_data': f'rec:accept:{event_index}:{candidate_index}',
                }])
        for event in possible_cancellations:
            lines.append(
                f'• {event.title} ({event.start_time:%d.%m %H:%M}) временно исчезло из TPU. '
                'Оно не отменено: ждём следующую проверку источника.'
            )
        return {'text': '\n'.join(lines), 'buttons': buttons}

    def apply_replacement_choice(data: str):
        try:
            _, action, event_index, candidate_index = data.split(':')
            if action != 'accept':
                raise ValueError
            event_index, candidate_index = int(event_index), int(candidate_index)
            reviews = sorted(
                (event for event in university_snapshot_store.personal_events()
                 if event.state == PersonalEventState.NEEDS_REVIEW),
                key=lambda event: (event.start_time, event.id),
            )
            event = reviews[event_index]
            candidates = event.metadata.get('replacement_candidates', [])
            candidate_uid = candidates[candidate_index]['uid']
            changed = university_snapshot_store.accept_replacement(event.id, candidate_uid)
        except (IndexError, KeyError, TypeError, ValueError):
            return {
                'text': ('Этот вариант уже устарел или не прошёл проверку источника. '
                         'Открой /schedule_review ещё раз.'),
                'buttons': [],
            }
        return {
            'text': (f'Перенос подтверждён: «{changed.title}» теперь {changed.start_time:%d.%m %H:%M}. '
                     'Личная идентичность сохранена, дубль не создан. '
                     'Запусти /update_all для проекции в Google Calendar.'),
            'buttons': [],
        }

    bot.reconciliation_review = reconciliation_review
    bot.reconciliation_apply = apply_replacement_choice

    def update_sources():
        update_context.clear()
        settings = profile_store.load().profile
        now = datetime.now(ZoneInfo(settings.timezone))
        start, end = calendar_horizon(now, settings.timezone)
        update_context.update(now=now, start=start, end=end, profile=settings)
        result = bot.fetcher(bot.source_url, args.output)
        bot.last_schedule_hash = result.content_hash
        # Floating ICS times follow the user's planning timezone, just as in
        # the preparation planner. Do not compare them with aware horizons or
        # let the host machine's timezone change their Calendar projection.
        zone = ZoneInfo(settings.timezone)
        def local_time(value):
            return value.astimezone(zone) if value.tzinfo else value.replace(tzinfo=zone)
        if args.output.exists():
            reconciliation = reconcile_downloaded_university_source()
            update_context['reconciliation_review_events'] = reconciliation.review_events
        update_context['study'] = [replace(event,
            start_time=local_time(event.start_time), end_time=local_time(event.end_time))
            for event in active_selected_events()]

    def update_work_source():
        update_context['work'] = (fetch_work_lessons(start=update_context['start'],
            end=update_context['end']) if alfacrm_source is not None else [])

    def update_classes():
        if not ensure_draft_calendar():
            raise UpdateAllBlocked('Google Calendar не подключён. Проверь авторизацию Google и перезапусти бота; затем повтори /update_all.')
        adapter = draft_service.calendar_adapter
        study_calendar = adapter._get_or_create_calendar('Personal University Schedule')
        update_context['study_calendar'] = study_calendar
        for event in update_context['study']:
            if not update_context['start'] <= event.start_time < update_context['end']:
                continue
            if not draft_service.sync_personal_event_to_calendar(event, study_calendar):
                raise UpdateAllBlocked(
                    'Google Calendar не подтвердил запись университетской пары. '
                    'Возможны конфликт с существующим событием или отказ записи; '
                    'новые подготовки не публиковались. Повтори /update_all для сверки.')
        if alfacrm_source is not None:
            result = work_service.sync(update_context['work'],
                verified_horizon=(update_context['start'], update_context['end']))
            update_context['work_calendar'] = result['calendar_id']

    def update_preparations():
        # Do not invoke old whole-action retries or destructive calendar health.
        rebuild_shared_preparation_queue()

    def verify_updated_calendar():
        context = update_context
        profile, now = context['profile'], context['now']
        adapter = draft_service.calendar_adapter
        calendars = {item['id']: item for item in adapter.list_visible_calendars()}
        for name in ('study_calendar', 'work_calendar'):
            if context.get(name):
                calendars.setdefault(context[name], {'id': context[name]})
        rows = [(calendar_id, event) for calendar_id in calendars
                for event in adapter.list_events_in_calendar(calendar_id,
                    context['start'] - timedelta(days=7), context['end'])
                if event.get('status') != 'cancelled']
        issues = []
        for event in context.get('reconciliation_review_events', []):
            reason = (
                'занятие исчезло из источника; отмена ожидает следующую проверку'
                if event.state == PersonalEventState.POSSIBLY_CANCELLED
                else 'найдено неоднозначное изменение расписания; открой /attendance после проверки'
            )
            issues.append(dict(
                kind='class', day=event.start_time.strftime('%d.%m'),
                scope='TPU reconciliation', title=event.title, reason=reason,
            ))
        expectations = []
        study_flow = preparation_workflow()
        classes = []
        for event in context['study']:
            if not now <= event.start_time < context['end']:
                continue
            classes.append((event.id, event.title, event.start_time, event.end_time,
                            context['study_calendar'], event.id))
            course, kind = course_and_session_type(event)
            minutes = profile.minutes_for(kind)
            if not minutes:
                continue
            earliest, deadline = profile.preparation_window(event.start_time)
            expectations.append(PreparationExpectation(event.id, 'university-preparation',
                event.title, event.start_time, minutes, earliest, deadline, context['study_calendar'],
                disabled=(system_edits.preparation_suppressed('university', event.id)
                          or system_edits.preparation_disabled(course, kind)),
                completed=event.id in study_flow.completed_source_event_ids))
        for lesson in context['work']:
            if not now <= lesson.start < context['end']:
                continue
            classes.append((lesson.id, lesson.display_name, lesson.start, lesson.end,
                            context['work_calendar'], f'personal-os:work:{lesson.id}'))
            prep_id = 'work-prep:' + sha256(lesson.id.encode()).hexdigest()[:20]
            expectations.append(PreparationExpectation(lesson.id, 'work-preparation',
                lesson.display_name, lesson.start, 20, lesson.start - timedelta(days=7),
                lesson.start, context['work_calendar'],
                disabled=system_edits.preparation_suppressed('work', lesson.id),
                completed=work_service.state.preparation_feedback.get(prep_id, {}).get('outcome') == 'done'))
        prep_ids = {event.get('id') for _, event in rows
                    if CalendarIntegrityService._is_preparation_projection(event)}
        commitments = [*study_flow.commitments_provider(),
                       *work_preparation_workflow().commitments_provider()]
        commitments = [item for item in commitments
                       if item.metadata.get('google_event_id') not in prep_ids]
        commitments.extend(study_flow.planner._university_commitments(
            context['study'], context['start'], context['end']))
        transitions, _ = work_preparation_workflow().planner._university_and_transition_commitments(
            context['work'], context['study'], now, context['end'])
        commitments.extend(transitions)
        no_slot_reasons = {(operation.scope, source): reason
            for operation in (study_flow.current_operation, work_preparation_workflow().current_operation)
            if operation is not None for source, reason in operation.no_slot_reasons.items()}
        checks = validate_preparations(expectations, rows,
            [(item.start, item.end) for item in commitments], timezone=profile.timezone,
            no_slot_reasons=no_slot_reasons)
        waiting = {lesson.id for lesson in context['work'] if work_needs_route(lesson)}
        checks = [replace(check, reason='нужен ответ о маршруте после университета; открой /work_schedule')
                  if check.state == PreparationState.MISSING and check.expectation.scope == 'work-preparation'
                  and check.expectation.source_id in waiting else check for check in checks]
        infeasibility = assess_preparation_infeasibility(checks)
        if infeasibility is not None:
            constraints = '; '.join(infeasibility.hard_constraints)
            issues.append(dict(
                kind='infeasible', day='', scope='weekly-plan', title='План недели',
                reason=(f'не хватает как минимум {infeasibility.capacity_deficit_minutes} минут '
                        f'для {len(infeasibility.unplaced_source_ids)} подготовок; '
                        f'hard constraints: {constraints}. '
                        f'Вариант: {infeasibility.alternatives[0]}'),
            ))
        issues.extend(dict(kind='preparation', day=check.expectation.lesson_start.strftime('%d.%m'),
                       scope=check.expectation.scope, title=check.expectation.title, reason=check.reason)
                  for check in checks if check.state not in {
                      PreparationState.PLACED, PreparationState.COMPLETED, PreparationState.DISABLED})
        for index, (source_id, title, start, end, calendar, uid) in enumerate(classes):
            matching = [event for calendar_id, event in rows
                        if calendar_id == calendar and event.get('iCalUID') == uid]
            reason = None
            if len(matching) != 1:
                reason = 'не найдена единственная подтверждённая проекция занятия'
            elif CalendarIntegrityService._interval(matching[0]) != (start, end):
                reason = 'время занятия в Calendar не совпадает с источником'
            if reason:
                issues.append(dict(kind='class', day=start.strftime('%d.%m'), scope=calendar,
                                   title=title, reason=reason))
            if any(CalendarIntegrityService._overlaps((start, end), (other[2], other[3]))
                   for other in classes[:index]):
                issues.append(dict(kind='class', day=start.strftime('%d.%m'), scope=calendar,
                    title=title, reason='пересечение неподвижных занятий; пары не переносились'))
        return issues

    all_updates = UpdateAllWorkflow(user_data_dir / 'update_all.json', [
        ('загрузка TPU', update_sources), ('загрузка AlfaCRM', update_work_source),
        ('публикация пар в Calendar', update_classes),
        ('размещение подготовок', update_preparations),
    ], verify_updated_calendar)
    bot.update_all = all_updates.run
    bot.update_all_action = all_updates.callback
    bot.preparation_stage = stage_preparations
    bot.preparation_confirm = lambda: with_draft_calendar(
        lambda: preparation_workflow().confirm()
    )
    bot.preparation_rollback = lambda: with_draft_calendar(
        lambda: preparation_workflow().rollback()
    )
    bot.preparation_replan = lambda: with_draft_calendar(
        lambda: '\n\n'.join(rebuild_shared_preparation_queue())
        if alfacrm_source is not None else preparation_workflow().replan()
    )

    def cleanup_preparation_drafts_preview() -> str:
        if not ensure_draft_calendar():
            return 'Не удалось подключиться к Google Calendar; ничего не удалено.'
        workflow = preparation_workflow()
        duplicates = workflow.calendar_projector.duplicate_draft_event_ids(
            workflow.current_operation,
        )
        if not duplicates:
            return 'Дубликатов системных preparation-черновиков не найдено.'
        return (
            f'Найдено дубликатов черновиков: {len(duplicates)}.\n'
            'Будут удалены только события с системной меткой AI Calendar Block; '
            'личные встречи и занятия не затрагиваются.'
        )

    def cleanup_preparation_drafts_confirm() -> str:
        def cleanup() -> str:
            workflow = preparation_workflow()
            removed = workflow.calendar_projector.delete_duplicate_drafts(
                workflow.current_operation,
            )
            return (
                f'Удалено дубликатов preparation-черновиков: {removed}.'
                if removed else 'Дубликатов уже не осталось.'
            )
        return with_draft_calendar(cleanup)

    bot.preparation_cleanup_preview = cleanup_preparation_drafts_preview
    bot.preparation_cleanup_confirm = cleanup_preparation_drafts_confirm

    def reset_preparation_drafts_preview() -> str:
        if not ensure_draft_calendar():
            return 'Не удалось подключиться к Google Calendar; ничего не удалено.'
        count = len(preparation_workflow().calendar_projector.system_draft_event_ids())
        return (
            f'Будет удалено системных preparation-черновиков: {count}.\n'
            'Занятия, подтверждённые блоки и личные события не затрагиваются. '
            'После этого нажми «Перепланировать» в /preparations.'
        )

    def reset_preparation_drafts_confirm() -> str:
        def reset() -> str:
            workflow = preparation_workflow()
            removed = workflow.calendar_projector.delete_all_system_drafts()
            # Google events are now gone, so a persisted operation with their
            # IDs must not make `/preparations` believe that drafts exist.
            workflow.reset_deleted_remote_drafts()
            return (
                f'Удалено системных preparation-черновиков: {removed}.\n'
                'Открой /preparations и нажми «Перепланировать»: будет создан чистый план.'
            )
        return with_draft_calendar(reset)

    bot.preparation_reset_preview = reset_preparation_drafts_preview
    bot.preparation_reset_confirm = reset_preparation_drafts_confirm

    def replan_after_schedule_change() -> str:
        if alfacrm_source is not None:
            return with_draft_calendar(lambda: '\n\n'.join(rebuild_shared_preparation_queue()))
        workflow = preparation_workflow()
        operation = workflow.current_operation
        if operation and operation.status != 'draft':
            return with_draft_calendar(lambda: workflow.replan(source_changed=True))
        if operation and operation.calendar_event_ids:
            return with_draft_calendar(workflow.replan)
        if ensure_draft_calendar():
            reply = workflow.replan()
            if workflow.current_operation and workflow.current_operation.blocks:
                return workflow.stage() + '\n\n' + reply
            return reply
        return workflow.replan()

    bot.schedule_change_replan = replan_after_schedule_change

    runtime_schedule = RuntimeSchedule(
        user_data_dir / 'runtime_schedule.json',
        profile_store.load().normalized_maintenance_hours(),
    )

    def today_summary() -> str:
        timezone = profile_store.load().profile.timezone
        now = datetime.now(ZoneInfo(timezone))
        today = now.date()
        lines = ['Дела на сегодня:']
        for event in active_selected_events():
            if event.start_time.date() == today:
                lines.append(f'• {event.start_time:%H:%M} — {event.title}')
        if alfacrm_source is not None:
            try:
                for lesson in alfacrm_source.fetch(
                    now, now + timedelta(days=1),
                ):
                    if lesson.start.date() == today:
                        lines.append(f'• {lesson.start:%H:%M} — Работа: {lesson.title}')
            except AlfaCRMError:
                lines.append('• Рабочее расписание AlfaCRM временно недоступно.')
        operation = preparation_workflow().current_operation
        if operation:
            for block in operation.blocks:
                if block.start.date() == today:
                    lines.append(f'• {block.start:%H:%M} — {block.title}')
        return '\n'.join(lines) if len(lines) > 1 else 'Дела на сегодня: свободных системных блоков нет.'

    def send_due_work_feedback() -> None:
        if alfacrm_source is None:
            return
        try:
            if not alfacrm_last_lessons:
                fetch_work_lessons(2)
            due = work_service.due_feedback(alfacrm_last_lessons)
            for lesson in due:
                bot.send_message(bot.allowed_chat_id,
                    f'Как прошла рабочая пара «{lesson.display_name}»?', [[
                        {'text': 'Провёл', 'callback_data': f'work:feedback:{lesson.id}:done'},
                        {'text': 'Частично', 'callback_data': f'work:feedback:{lesson.id}:partial'},
                        {'text': 'Отменилось', 'callback_data': f'work:feedback:{lesson.id}:cancelled'},
                    ]])
        except AlfaCRMError:
            # A later scheduled pass retries; never turn a source outage into
            # duplicate Telegram prompts or a stopped long-polling process.
            return

    def scheduled_tick() -> None:
        settings = profile_store.load()
        runtime_schedule.set_hours(settings.normalized_maintenance_hours())
        now = datetime.now(ZoneInfo(settings.profile.timezone))
        send_due_work_feedback()
        for hour in runtime_schedule.due_hours(now):
            try:
                reply = all_updates.run(automatic=True)
                if reply:
                    bot.send_message(bot.allowed_chat_id, reply['text'], reply.get('buttons'))
            finally:
                # A failed maintenance pass is retried at the next scheduled
                # time, rather than becoming a tight loop that starves polling.
                runtime_schedule.mark_completed(hour, now)

    bot.scheduled_tick = scheduled_tick

    def feedback_preview() -> dict:
        workflow = preparation_workflow()
        operation = workflow.current_operation
        if operation is None or not operation.blocks:
            return {'text': 'Нет активных preparation blocks для feedback.', 'buttons': []}
        return {
            'text': (
                'Как прошла подготовка?\n'
                'Для подробного ввода: /feedback НОМЕР done|partial|skipped МИНУТЫ СЛОЖНОСТЬ комментарий'
            ),
            'buttons': [
                [
                    {'text': f'{index + 1}. Сделал', 'callback_data': f'fb:{index}:done'},
                    {'text': 'Частично', 'callback_data': f'fb:{index}:partial'},
                    {'text': 'Не сделал', 'callback_data': f'fb:{index}:skipped'},
                ]
                for index, _ in enumerate(operation.blocks)
            ],
        }

    def feedback_submit(index: str, outcome: str) -> str:
        operation = preparation_workflow().current_operation
        if operation is None:
            return 'Сначала создай preview подготовок.'
        try:
            block = operation.blocks[int(index)]
        except (IndexError, ValueError):
            return 'Неизвестный номер блока подготовки.'
        return preparation_workflow().feedback(block.id, outcome)

    def feedback_detail(raw: str) -> str:
        parts = raw.split(maxsplit=4)
        if len(parts) < 4:
            return 'Формат: /feedback НОМЕР done|partial|skipped МИНУТЫ СЛОЖНОСТЬ комментарий'
        try:
            index, outcome, minutes, difficulty = parts[:4]
            operation = preparation_workflow().current_operation
            if operation is None:
                return 'Сначала создай preview подготовок.'
            block = operation.blocks[int(index) - 1]
            comment = parts[4] if len(parts) == 5 else ''
            return preparation_workflow().feedback(
                block.id, outcome, int(minutes), int(difficulty), comment,
            )
        except (IndexError, ValueError):
            return 'Не удалось принять feedback: проверь номер, минуты и сложность 1–5.'

    bot.feedback_preview = feedback_preview
    bot.feedback_submit = feedback_submit
    bot.feedback_detail = feedback_detail
    bot.feedback_proposal_apply = lambda: preparation_workflow().apply_feedback_proposal()
    bot.feedback_proposal_reject = lambda: preparation_workflow().reject_feedback_proposal()
    print('Telegram bot started. Send /update_all or /help.')
    bot.run_forever()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
