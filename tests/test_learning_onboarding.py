from services.onboarding_service import OnboardingStore, OnboardingStep, TelegramOnboardingService
from services.telegram_onboarding_handler import TelegramOnboardingHandler
from services.user_planning_profile_store import UserPlanningProfileStore

from tests.test_telegram_onboarding_handler import make_handler
from services.user_registry import UserRegistryStore


def test_first_time_flow_restarts_and_remembers_style_and_settings(tmp_path):
    account = UserRegistryStore(tmp_path / 'registry.json').get_or_create('101')
    connected = {'value': False}
    calls = []
    handler = make_handler(
        tmp_path, account, connected=lambda: connected['value'],
    )
    handler.weekly_preview = lambda: calls.append('preview') or 'Первый план: 3 пары и подготовка.'

    assert handler.handle_text('101', '/start')['step'] == 'source'
    source = handler.handle_text('101', 'https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html')
    assert source['step'] == 'timezone'
    timezone = handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    assert timezone['step'] == 'calendar'
    assert 'обязательно' in timezone['text'].lower()

    waiting = handler.handle_text('101', '/calendar_status')
    assert 'пока не подключён' in waiting['text']
    connected['value'] = True
    style = handler.handle_text('101', '/calendar_status')
    assert style['step'] == 'style'
    style = handler.handle_callback('101', 'ob:style:direct')
    assert style['step'] == 'profile'
    assert handler.handle_text('101', '/sleep 23:00 06:40')['step'] == 'profile'
    assert handler.handle_text('101', '/travel 45')['step'] == 'complete'

    profile_path = handler.profile_store.path
    restarted = TelegramOnboardingHandler(
        account=account,
        state_directory=handler.state_directory,
        onboarding=TelegramOnboardingService(OnboardingStore(handler.state_directory / 'onboarding.json')),
        source_service=handler.source_service,
        events_for_source=handler.events_for_source,
        profile_store=UserPlanningProfileStore(profile_path),
        calendar_connected=lambda: connected['value'],
        weekly_preview=lambda: calls.append('preview') or 'Первый план: 3 пары и подготовка.',
    )
    assert restarted.handle_text('101', '/start')['step'] == 'complete'
    assert restarted.profile_store.communication_style() == 'direct'
    assert restarted.profile_store.load().profile.travel_minutes_each_way == 45
    assert restarted.handle_text('101', '/weekly_preview')['text'] == 'Первый план: 3 пары и подготовка.'
    assert calls == ['preview']

    assert restarted.try_handle_preferences('Давай дружелюбно') is not None
    assert restarted.profile_store.communication_style() == 'friendly'
    assert restarted.handle_text('101', '/style Неформально')['text'].endswith('Неформально.')
    assert restarted.profile_store.communication_style() == 'casual'


def test_profile_store_migrates_style_default_and_preparation_duration(tmp_path):
    profile = UserPlanningProfileStore(tmp_path / 'profile.json')
    assert profile.communication_style() == 'friendly'
    assert profile.load().profile.minutes_for('lecture') == 60


def test_attendance_defaults_fill_only_missing_choices_and_keep_optouts(tmp_path):
    from dataclasses import replace
    from services.attendance_preferences import AttendancePreferenceStore
    from services.attendance_service import AttendanceRuleService, PersonalAttendanceRule
    from tests.test_telegram_onboarding_handler import university_events

    account = UserRegistryStore(tmp_path / 'registry.json').get_or_create('101')
    handler = make_handler(tmp_path, account)
    preferences = AttendancePreferenceStore.load(handler.state_directory / 'attendance_preferences.json')
    preferences.set_choice('ОС', 'lecture', False)
    preferences.set_choice('ОС', 'lab', '2:14:00')
    preferences.save()

    handler.initialize_attendance_defaults(university_events())
    saved = AttendancePreferenceStore.load(handler.state_directory / 'attendance_preferences.json')
    assert saved.subjects['ОС'].lectures is False
    assert saved.subjects['ОС'].practicals is False
    assert saved.subjects['ОС'].labs_enabled is True
    assert saved.subjects['ОС'].lab_slots == {'2:14:00'}

    fresh = make_handler(tmp_path, UserRegistryStore(tmp_path / 'other_registry.json').get_or_create('202'))
    events = university_events()
    fresh.initialize_attendance_defaults(events)
    rule = AttendanceRuleService(PersonalAttendanceRule(
        id='defaults', description='defaults',
        metadata={'attendance_preferences': AttendancePreferenceStore.load(
            fresh.state_directory / 'attendance_preferences.json').as_rule_metadata()},
    ))
    assert rule.attendance_choice(events[0]) is True
    replacement = replace(events[0], uid='replacement-uid')
    assert rule.attendance_choice(replacement) is None


def test_google_timezone_and_style_buttons_cannot_be_bypassed_or_replayed(tmp_path):
    account = UserRegistryStore(tmp_path / 'registry.json').get_or_create('101')
    handler = make_handler(tmp_path, account, connected=lambda: True)
    assert 'Сначала настрой расписание' in handler.handle_text('101', '/calendar_status')['text']
    handler.handle_text('101', '/connect_tpu https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html')
    assert 'Сначала настрой расписание' in handler.handle_text('101', '/calendar_status')['text']
    assert handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')['step'] == 'calendar'
    assert handler.handle_text('101', '/calendar_status')['step'] == 'style'
    handler.handle_callback('101', 'ob:style:direct')
    assert handler.handle_text('101', '/sleep 23:00 06:40')['step'] == 'profile'
    handler.handle_text('101', '/travel 30')
    stale = handler.handle_callback('101', 'ob:style:casual')
    assert 'больше не актуальна' in stale['text']
    assert handler.profile_store.communication_style() == 'direct'
    stale_timezone = handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    assert 'больше не актуальна' in stale_timezone['text']
    assert handler.onboarding.set_timezone('Asia/Tomsk')['step'] == 'complete'


def test_typed_style_choice_advances_onboarding_and_style_can_change_later(tmp_path):
    account = UserRegistryStore(tmp_path / 'registry.json').get_or_create('101')
    handler = make_handler(tmp_path, account, connected=lambda: True)
    handler.handle_text('101', '/connect_tpu https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html')
    handler.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    assert handler.handle_text('101', '/calendar_status')['step'] == 'style'
    typed = handler.try_handle_preferences('общайся по делу')
    assert typed['step'] == 'profile'
    assert handler.profile_store.communication_style() == 'direct'
    assert handler.try_handle_preferences('общайся неформально')['text'].endswith('Неформально.')
    assert handler.profile_store.communication_style() == 'casual'


def test_closed_beta_new_user_can_preview_and_confirm_without_attendance_setup(tmp_path):
    from tests.test_closed_beta_runtime import application, confirmation, URL

    app = application(tmp_path)
    assert app.handle_text('101', '/start')['step'] == 'source'
    assert app.handle_text('101', '/connect_tpu ' + URL)['step'] == 'timezone'
    assert app.handle_callback('101', 'ob:timezone:Asia/Tomsk')['step'] == 'calendar'
    assert app.handle_text('101', '/calendar_status')['step'] == 'style'
    assert app.handle_callback('101', 'ob:style:friendly')['step'] == 'profile'
    after_sleep = app.handle_text('101', '/sleep 23:00 06:40')
    assert after_sleep['step'] == 'profile'
    assert '/travel 60' in after_sleep['text']
    assert '/sleep' not in after_sleep['text']
    assert app.handle_text('101', '/travel 30')['step'] == 'complete'

    preview = app.handle_text('101', '/weekly_preview')
    assert 'ОС (ЛК)' in preview['text']
    assert preview['buttons']
    applied = app.handle_callback('101', confirmation(preview))
    assert 'опубликован' in applied['text'].lower()
