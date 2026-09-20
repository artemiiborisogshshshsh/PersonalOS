from datetime import datetime

from models import EventType, UniversityEvent
from services.onboarding_service import OnboardingStore, OnboardingStep, TelegramOnboardingService
from services.attendance_preferences import AttendancePreferenceStore
from services.product_state import UserProductStateStore
from services.product_analytics import ProductAnalyticsStore
from services.schedule_source_service import ScheduleSourceService
from services.telegram_multi_user_runtime import TelegramMultiUserDispatcher
from services.telegram_onboarding_handler import TelegramOnboardingHandler
from services.telegram_user_router import TelegramUserRouter
from services.user_planning_profile_store import UserPlanningProfileStore
from services.user_registry import UserRegistryStore, UserStatePaths


TPU_URL = 'https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html'
VALID_ICS = b'''BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:os-lecture\r
DTSTART:20260902T040000Z\r
DTEND:20260902T053000Z\r
SUMMARY:Operating Systems\r
END:VEVENT\r
END:VCALENDAR\r
'''


def university_events():
    start = datetime(2026, 9, 2, 10, 0)
    return [
        UniversityEvent('lecture', 'ОС (ЛК)', '', '', start, start.replace(hour=11), EventType.LECTURE, True),
        UniversityEvent('practical', 'ОС (ПР)', '', '', start.replace(hour=12), start.replace(hour=13), EventType.PRACTICAL, True),
        UniversityEvent('lab', 'ОС (ЛБ)', '', '', start.replace(hour=14), start.replace(hour=15), EventType.LAB, True),
    ]


def make_handler(tmp_path, account, *, connected=lambda: False, reader=lambda _url: VALID_ICS,
                 state_directory=None, analytics=None):
    directory = state_directory or (tmp_path / account.id)
    state_store = UserProductStateStore(directory / 'product_state.json')
    return TelegramOnboardingHandler(
        account=account,
        state_directory=directory,
        onboarding=TelegramOnboardingService(OnboardingStore(directory / 'onboarding.json')),
        source_service=ScheduleSourceService(
            state_store, remote_reader=reader, tpu_discoverer=lambda url: url,
        ),
        events_for_source=lambda _source: university_events(),
        profile_store=UserPlanningProfileStore(directory / 'planning_profile.json'),
        calendar_connected=connected,
        weekly_preview=lambda: 'Черновик недели: 3 занятия, без записи в Calendar.',
        analytics=analytics,
    )


def choose_all_attendance(handler, chat_id):
    response = handler.handle_text(chat_id, '/attendance')
    while not response.get('attendance_complete'):
        response = handler.handle_callback(chat_id, response['buttons'][0][0]['callback_data'])
    return response


def test_resumable_onboarding_happy_path_requires_sleep_travel_and_calendar_status(tmp_path):
    registry = UserRegistryStore(tmp_path / 'registry.json')
    account = registry.get_or_create('101')
    connection = {'ok': False}
    handler = make_handler(tmp_path, account, connected=lambda: connection['ok'])

    assert handler.handle_text('101', '/start')['step'] == 'timezone'
    assert handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')['step'] == 'source'
    source = handler.handle_text('101', f'/connect_tpu {TPU_URL}')
    assert source['step'] == 'attendance'
    assert '1 занятий' in source['text']

    complete_attendance = choose_all_attendance(handler, '101')
    assert complete_attendance['attendance_complete'] is True
    preferences = AttendancePreferenceStore.load(handler.state_directory / 'attendance_preferences.json')
    assert preferences.subjects['ОС'].labs_enabled is True
    assert preferences.subjects['ОС'].lab_slots == {'2:14:00'}
    assert handler.onboarding.store.load().step is OnboardingStep.PROFILE
    assert handler.handle_text('101', '/sleep 23:00 06:40')['step'] == 'profile'
    assert handler.handle_text('101', '/travel 60')['step'] == 'calendar'
    assert handler.profile_store.load().profile.travel_minutes_each_way == 60

    disconnected = handler.handle_text('101', '/calendar_status')
    assert 'пока не подключ' in disconnected['text']
    assert handler.onboarding.store.load().step is OnboardingStep.CALENDAR
    connection['ok'] = True
    connected = handler.handle_callback('101', 'ob:calendar:check')
    assert 'Google Calendar подключён' in connected['text']
    assert 'Черновик недели' in connected['text']
    assert handler.onboarding.store.load().step is OnboardingStep.COMPLETE


def test_source_rejection_and_incomplete_preview_do_not_activate_or_replace_state(tmp_path):
    registry = UserRegistryStore(tmp_path / 'registry.json')
    account = registry.get_or_create('101')
    handler = make_handler(tmp_path, account)
    handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')

    invalid = handler.handle_text('101', '/connect_tpu https://example.test/not-tpu')
    assert 'Не удалось проверить' in invalid['text']
    assert not handler.source_service.state_store.load(account.id).sources

    broken = make_handler(tmp_path, account, reader=lambda _url: b'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n')
    incomplete = broken.handle_text('101', f'/connect_tpu {TPU_URL}')
    assert 'Не удалось проверить' in incomplete['text']
    assert not broken.source_service.state_store.load(account.id).sources


def test_interruption_resume_and_repeat_do_not_duplicate_tpu_source(tmp_path):
    registry = UserRegistryStore(tmp_path / 'registry.json')
    account = registry.get_or_create('101')
    first = make_handler(tmp_path, account)
    first.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    # Simulate a process restart before the source is connected.
    restarted = make_handler(tmp_path, account)
    assert restarted.handle_text('101', '/start')['step'] == 'source'
    restarted.handle_text('101', f'/connect_tpu {TPU_URL}')
    restarted.handle_text('101', f'/connect_tpu {TPU_URL}')
    state = restarted.source_service.state_store.load(account.id)
    assert state.active_source_id == 'tpu-primary'
    assert list(state.sources) == ['tpu-primary']
    # A completed /start is a resume screen, not a new user/source creation.
    choose_all_attendance(restarted, '101')
    restarted.handle_text('101', '/sleep 23:00 06:40')
    restarted.handle_text('101', '/travel 60')
    final = make_handler(tmp_path, account, connected=lambda: True)
    final.handle_text('101', '/calendar_status')
    assert final.handle_text('101', '/start')['step'] == 'complete'
    assert list(final.source_service.state_store.load(account.id).sources) == ['tpu-primary']


def test_dispatcher_keeps_onboarding_state_isolated_between_chats(tmp_path):
    registry = UserRegistryStore(tmp_path / 'registry.json')
    paths = UserStatePaths(tmp_path)
    handlers = {}

    def factory(account, directory):
        handler = make_handler(tmp_path, account, state_directory=directory)
        handlers[account.telegram_chat_id] = handler
        assert handler.state_directory == directory
        return handler

    runtime = TelegramMultiUserDispatcher(TelegramUserRouter(registry, paths, factory))
    for chat_id in ('101', '202'):
        runtime.handle_update({'message': {'chat': {'id': chat_id}, 'text': '/start'}})
        runtime.handle_update({'callback_query': {'data': 'ob:timezone:Asia/Tomsk', 'message': {'chat': {'id': chat_id}}}})
        runtime.handle_update({'message': {'chat': {'id': chat_id}, 'text': f'/connect_tpu {TPU_URL}'}})

    first, second = handlers['101'], handlers['202']
    assert first.account.id != second.account.id
    assert first.state_directory != second.state_directory
    assert first.source_service.state_store.load(first.account.id).active_source_id == 'tpu-primary'
    assert second.source_service.state_store.load(second.account.id).active_source_id == 'tpu-primary'
    assert first.handle_text('202', '/attendance') is None


def test_onboarding_records_only_completed_lifecycle_milestones(tmp_path):
    account = UserRegistryStore(tmp_path / 'registry.json').get_or_create('101')
    analytics = ProductAnalyticsStore(tmp_path / 'analytics.json')
    connection = {'ok': False}
    handler = make_handler(
        tmp_path, account, connected=lambda: connection['ok'], analytics=analytics,
    )

    handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    handler.handle_text('101', '/connect_tpu https://example.test/not-tpu')
    assert analytics.funnel_counts()['source_connected'] == 0

    handler.handle_text('101', f'/connect_tpu {TPU_URL}')
    handler.handle_text('101', f'/connect_tpu {TPU_URL}')
    assert analytics.funnel_counts()['source_connected'] == 1

    choose_all_attendance(handler, '101')
    assert analytics.funnel_counts()['attendance_completed'] == 1
    handler.handle_text('101', '/sleep 23:00 06:40')
    handler.handle_text('101', '/travel 60')
    handler.handle_text('101', '/calendar_status')
    assert analytics.funnel_counts()['first_plan'] == 0

    connection['ok'] = True
    handler.handle_text('101', '/calendar_status')
    handler.handle_text('101', '/weekly_preview')
    assert analytics.funnel_counts()['first_plan'] == 1
