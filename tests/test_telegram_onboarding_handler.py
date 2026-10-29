from datetime import datetime

import pytest

from models import EventType, UniversityEvent
from services.onboarding_service import OnboardingStore, OnboardingStep, TelegramOnboardingService
from services.attendance_preferences import AttendancePreferenceStore
from services.product_state import UserProductStateStore
from services.product_analytics import ProductAnalyticsStore
from services.schedule_source_service import ScheduleSourceService
from services.telegram_multi_user_runtime import TelegramMultiUserDispatcher
from services.telegram_onboarding_handler import TelegramOnboardingHandler
from services.telegram_natural_text_proposals import create_per_user_natural_text_flow
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
                 state_directory=None, analytics=None, natural_text_proposals=None,
                 calendar_list=None):
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
        natural_text_proposals=natural_text_proposals,
        calendar_list=calendar_list,
    )


def choose_all_attendance(handler, chat_id):
    response = handler.handle_text(chat_id, '/attendance')
    if response.get('attendance_complete'):
        response = handler.handle_callback(chat_id, 'att:reset')
    for _ in range(30):
        if response.get('attendance_complete'):
            return response
        buttons = [button for row in response['buttons'] for button in row]
        save = next((button for button in buttons
                     if button['callback_data'].endswith(':lab:save')), None)
        selected = any(button['text'].startswith('✅') for button in buttons
                       if ':lab:toggle:' in button['callback_data'])
        choice = save if save and selected else buttons[0]
        response = handler.handle_callback(chat_id, choice['callback_data'])
    assert response.get('attendance_complete'), 'Attendance wizard did not complete'
    return response


def test_resumable_onboarding_happy_path_requires_sleep_travel_and_calendar_status(tmp_path):
    registry = UserRegistryStore(tmp_path / 'registry.json')
    account = registry.get_or_create('101')
    connection = {'ok': False}
    handler = make_handler(tmp_path, account, connected=lambda: connection['ok'])

    assert handler.handle_text('101', '/start')['step'] == 'source'
    source = handler.handle_text('101', f'/connect_tpu {TPU_URL}')
    assert source['step'] == 'timezone'
    assert '1 занятий' in source['text']
    assert handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')['step'] == 'calendar'

    complete_attendance = choose_all_attendance(handler, '101')
    assert complete_attendance['attendance_complete'] is True
    preferences = AttendancePreferenceStore.load(handler.state_directory / 'attendance_preferences.json')
    assert preferences.subjects['ОС'].labs_enabled is True
    assert preferences.subjects['ОС'].lab_slots == {'2:14:00'}
    assert handler.onboarding.store.load().step is OnboardingStep.CALENDAR
    assert 'пока не подключ' in handler.handle_text('101', '/calendar_status')['text']
    connection['ok'] = True
    assert handler.handle_text('101', '/calendar_status')['step'] == 'style'
    assert handler.handle_callback('101', 'ob:style:friendly')['step'] == 'profile'
    assert handler.handle_text('101', '/sleep 23:00 06:40')['step'] == 'profile'
    assert handler.handle_text('101', '/travel 60')['step'] == 'complete'
    assert handler.profile_store.load().profile.travel_minutes_each_way == 60

    connection['ok'] = False
    disconnected = handler.handle_text('101', '/calendar_status')
    assert 'пока не подключ' in disconnected['text']
    assert handler.onboarding.store.load().step is OnboardingStep.COMPLETE
    connection['ok'] = True
    connected = handler.handle_callback('101', 'ob:calendar:check')
    assert 'Google Calendar подключён' in connected['text']
    assert 'Черновик недели' in connected['text']
    assert handler.onboarding.store.load().step is OnboardingStep.COMPLETE
    assert 'Google Calendar подключён' in handler.handle_text('101', '/calendar_status')['text']
    connection['ok'] = False
    assert 'пока не подключён' in handler.handle_text('101', '/calendar_status')['text']
    assert handler.onboarding.store.load().step is OnboardingStep.COMPLETE


def _finish_before_calendar_status(handler):
    handler.handle_text('101', '/start')
    handler.handle_text('101', f'/connect_tpu {TPU_URL}')
    handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    choose_all_attendance(handler, '101')
    handler.handle_text('101', '/calendar_status')
    handler.handle_callback('101', 'ob:style:friendly')
    handler.handle_text('101', '/sleep 23:00 06:40')
    handler.handle_text('101', '/travel 30')


def test_calendar_status_shows_sanitized_primary_id_from_existing_list_read(tmp_path):
    registry = UserRegistryStore(tmp_path / 'registry.json')
    account = registry.get_or_create('101')
    calls = []
    calendars = [{'id': ' main\ncalendar-id ' , 'primary': True},
                 {'id': 'secondary', 'primary': False}]
    handler = make_handler(tmp_path, account, connected=lambda: True,
                           calendar_list=lambda: calls.append(1) or calendars)
    _finish_before_calendar_status(handler)

    result = handler.handle_text('101', '/calendar_status')

    assert 'Основной календарь: main calendar-id' in result['text']
    assert len(calls) == 2
    assert handler.handle_text('999', '/calendar_status') is None
    assert len(calls) == 2


@pytest.mark.parametrize('calendars', [
    [], [{'id': 'secondary', 'primary': False}],
    [{'id': 'one', 'primary': True}, {'id': 'two', 'primary': True}],
    [{'id': '  ', 'primary': True}],
])
def test_calendar_status_does_not_claim_primary_without_one_valid_entry(tmp_path, calendars):
    registry = UserRegistryStore(tmp_path / 'registry.json')
    account = registry.get_or_create('101')
    handler = make_handler(tmp_path, account, connected=lambda: True,
                           calendar_list=lambda: calendars)
    _finish_before_calendar_status(handler)

    result = handler.handle_text('101', '/calendar_status')

    assert 'Google Calendar подключён' in result['text']
    assert 'Основной календарь:' not in result['text']


def test_calendar_status_suppresses_calendar_list_detail_errors(tmp_path):
    registry = UserRegistryStore(tmp_path / 'registry.json')
    account = registry.get_or_create('101')

    def failing_list():
        raise RuntimeError('secret provider detail')

    handler = make_handler(tmp_path, account, connected=lambda: True,
                           calendar_list=failing_list)
    _finish_before_calendar_status(handler)
    result = handler.handle_text('101', '/calendar_status')

    assert 'Google Calendar подключён' in result['text']
    assert 'secret provider detail' not in result['text']
    assert 'Основной календарь:' not in result['text']


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
    first.handle_text('101', f'/connect_tpu {TPU_URL}')
    # Simulate a process restart before the source is connected.
    restarted = make_handler(tmp_path, account, connected=lambda: True)
    assert restarted.handle_text('101', '/start')['step'] == 'timezone'
    restarted.handle_text('101', f'/connect_tpu {TPU_URL}')
    restarted.handle_text('101', f'/connect_tpu {TPU_URL}')
    restarted.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    state = restarted.source_service.state_store.load(account.id)
    assert state.active_source_id == 'tpu-primary'
    assert list(state.sources) == ['tpu-primary']
    # A completed /start is a resume screen, not a new user/source creation.
    choose_all_attendance(restarted, '101')
    restarted.handle_text('101', '/calendar_status')
    restarted.handle_callback('101', 'ob:style:friendly')
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
        runtime.handle_update({'message': {'chat': {'id': chat_id}, 'text': f'/connect_tpu {TPU_URL}'}})
        runtime.handle_update({'callback_query': {'data': 'ob:timezone:Asia/Tomsk', 'message': {'chat': {'id': chat_id}}}})

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

    handler.handle_text('101', '/connect_tpu https://example.test/not-tpu')
    assert analytics.funnel_counts()['source_connected'] == 0

    handler.handle_text('101', f'/connect_tpu {TPU_URL}')
    handler.handle_text('101', f'/connect_tpu {TPU_URL}')
    handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    assert analytics.funnel_counts()['source_connected'] == 1

    choose_all_attendance(handler, '101')
    assert analytics.funnel_counts()['attendance_completed'] == 1
    handler.handle_text('101', '/calendar_status')
    assert analytics.funnel_counts()['first_plan'] == 0

    connection['ok'] = True
    handler.handle_text('101', '/calendar_status')
    handler.handle_callback('101', 'ob:style:friendly')
    handler.handle_text('101', '/sleep 23:00 06:40')
    handler.handle_text('101', '/travel 60')
    handler.handle_text('101', '/weekly_preview')
    assert analytics.funnel_counts()['first_plan'] == 1


def test_multi_user_onboarding_routes_natural_commands_to_owned_local_state(tmp_path):
    registry = UserRegistryStore(tmp_path / 'registry.json')
    paths = UserStatePaths(tmp_path)
    handlers = {}

    def factory(account, directory):
        handler = make_handler(
            tmp_path, account, state_directory=directory,
            natural_text_proposals=create_per_user_natural_text_flow(account, directory),
        )
        handlers[account.telegram_chat_id] = handler
        return handler

    runtime = TelegramMultiUserDispatcher(TelegramUserRouter(registry, paths, factory))
    runtime.handle_update({'message': {'chat': {'id': '101'}, 'text': '/start'}})
    runtime.handle_update({'message': {'chat': {'id': '202'}, 'text': '/start'}})
    preview = runtime.handle_update({
        'message': {'chat': {'id': '101'}, 'text': 'Создай задачу купить книгу'},
    })[1]
    callback = preview['buttons'][0][0]['callback_data']

    foreign = runtime.handle_update({
        'callback_query': {'data': callback, 'message': {'chat': {'id': '202'}}},
    })[1]
    assert 'уже не актуально' in foreign['text']
    runtime.handle_update({
        'callback_query': {'data': callback, 'message': {'chat': {'id': '101'}}},
    })

    first_state = handlers['101'].state_directory / 'natural_commands.json'
    second_state = handlers['202'].state_directory / 'natural_commands.json'
    assert first_state.exists()
    assert not second_state.exists()


def test_attendance_uses_each_invited_users_timezone(tmp_path):
    from datetime import timezone, timedelta
    from dataclasses import replace
    registry = UserRegistryStore(tmp_path / 'registry.json')
    for chat, zone, expected in [('101', 'Asia/Tomsk', 'Чт 12:40'),
                                 ('202', 'Asia/Vladivostok', 'Чт 15:40')]:
        handler = make_handler(tmp_path, registry.get_or_create(chat))
        settings = handler.profile_store.load()
        settings.profile = replace(settings.profile, timezone=zone)
        handler.profile_store.save(settings)
        start = datetime(2026, 9, 3, 5, 40, tzinfo=timezone.utc)
        lab = UniversityEvent('lab', 'ОС (ЛБ)', '', '', start,
                              start + timedelta(minutes=95), EventType.LAB, True)
        flow = handler._attendance_flow()
        question = flow.start([lab])
        assert question['buttons'][0][0]['text'] == expected
        selected = flow.handle_callback(question['buttons'][0][0]['callback_data'])
        flow.handle_callback(selected['buttons'][-1][0]['callback_data'])
        resumed = handler._attendance_flow().start([lab])
        assert expected in resumed['text']


def test_single_lab_defaults_use_local_weekday_and_time(tmp_path):
    from datetime import timezone, timedelta
    registry = UserRegistryStore(tmp_path / 'registry.json')
    handler = make_handler(tmp_path, registry.get_or_create('101'))
    start = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    lab = UniversityEvent('lab', 'ОС (ЛБ)', '', '', start,
                          start + timedelta(hours=1), EventType.LAB, True)
    handler.initialize_attendance_defaults([lab])
    saved = AttendancePreferenceStore.load(handler.state_directory / 'attendance_preferences.json')
    assert saved.subjects['ОС'].lab_slots == {'3:05:00'}
