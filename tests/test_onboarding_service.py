from services.onboarding_service import OnboardingStore, OnboardingStep, TelegramOnboardingService


def test_onboarding_resumes_without_duplicate_state(tmp_path):
    service = TelegramOnboardingService(OnboardingStore(tmp_path / 'onboarding.json'))
    assert service.start()['step'] == 'source'
    assert service.source_connected('tpu-8i41')['step'] == 'timezone'
    assert service.set_timezone('Asia/Tomsk')['step'] == 'calendar'
    assert service.calendar_connected()['step'] == 'style'
    service.style_completed()
    restarted = TelegramOnboardingService(OnboardingStore(tmp_path / 'onboarding.json'))
    assert restarted.start()['step'] == 'profile'
    restarted.profile_setting_saved('sleep'); restarted.profile_setting_saved('travel')
    assert restarted.calendar_checked()['step'] == OnboardingStep.COMPLETE.value


def test_profile_prompt_only_requests_missing_sleep_and_travel(tmp_path):
    service = TelegramOnboardingService(OnboardingStore(tmp_path / 'onboarding.json'))
    service.source_connected('tpu-8i41')
    service.set_timezone('Asia/Tomsk')
    service.calendar_connected()
    both_missing = service.style_completed()
    assert 'Выбери кнопками ниже настройки: сон и дорогу.' in both_missing['text']
    assert 'сохранятся после подтверждения' in both_missing['text']
    assert '/sleep' not in both_missing['text']
    assert '/travel' not in both_missing['text']
    assert [button['text'] for button in both_missing['buttons'][0]] == [
        'Настроить сон', 'Настроить дорогу',
    ]

    sleep_saved = service.profile_setting_saved('sleep')
    assert sleep_saved['step'] == 'profile'
    assert 'дорогу' in sleep_saved['text']
    assert 'сон' not in sleep_saved['text']
    assert '/sleep' not in sleep_saved['text']
    assert '/travel' not in sleep_saved['text']

    travel_saved = service.profile_setting_saved('travel')
    assert travel_saved['step'] == 'complete'
    assert 'Проверь посещаемость' in travel_saved['text']
    assert 'предпросмотр перед публикацией' in travel_saved['text']
    assert [button['text'] for row in travel_saved['buttons'] for button in row] == [
        'Показать план', 'Посещаемость', 'Настроить сон',
        'Настроить дорогу', 'Главное меню',
    ]
    callbacks = [button['callback_data'] for row in travel_saved['buttons'] for button in row]
    assert callbacks == [
        'main:plan', 'main:attendance', 'main:sleep',
        'main:travel', 'main:open',
    ]
