from services.onboarding_service import OnboardingState, OnboardingStep
from services.user_registry import UserRegistryStore
from tests.test_telegram_onboarding_handler import make_handler


def _account(tmp_path, chat='101'):
    return UserRegistryStore(tmp_path / 'registry.json').get_or_create(chat)


def _click(reply, label):
    button = next(b for row in reply['buttons'] for b in row if b['text'] == label)
    assert len(button['callback_data'].encode()) <= 64
    return button['callback_data']


def test_sleep_presets_stage_and_confirm_before_saving(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    before = h.profile_store.load()
    first = h.profile_preferences_start('sleep')
    assert 'Сейчас: 23:00' in first['text']
    assert 'main:keyboard' not in str(first)
    wake = h.handle_callback('101', _click(first, '22:00'))
    assert 'встаёшь' in wake['text']
    confirm = h.handle_callback('101', _click(wake, '07:00'))
    assert 'Сохранить?' in confirm['text']
    assert h.profile_store.load().sleep_start == before.sleep_start
    assert h.profile_store.load().sleep_end == before.sleep_end
    h.handle_callback('101', _click(confirm, 'Сохранить'))
    saved = h.profile_store.load()
    assert (saved.sleep_start, saved.sleep_end) == ('22:00', '07:00')
    assert saved.weekend_sleep_end == '08:00'


def test_sleep_weekday_edit_keeps_weekend_default_and_confirmation_shows_both(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    confirm = h.handle_callback('101', _click(
        h.handle_callback('101', _click(
            h.profile_preferences_start('sleep'), '22:00')), '07:00'))
    assert 'Будни: сон 22:00 → подъём 07:00' in confirm['text']
    assert 'Выходные: подъём 08:00' in confirm['text']
    saved = h.handle_callback('101', _click(confirm, 'Сохранить'))
    assert h.profile_store.load().weekend_sleep_end == '08:00'
    assert saved['text']


def test_sleep_weekend_choice_and_same_as_weekday_are_saved_together(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    confirm = h.handle_callback('101', _click(
        h.handle_callback('101', _click(
            h.profile_preferences_start('sleep'), '22:00')), '07:00'))
    stale_weekend = _click(confirm, 'Сон в выходные')
    weekend = h.handle_callback('101', stale_weekend)
    assert 'выходные' in weekend['text']
    confirm = h.handle_callback('101', _click(weekend, 'Как в будни'))
    assert 'Выходные: подъём 07:00' in confirm['text']
    h.handle_callback('101', _click(confirm, 'Сохранить'))
    saved = h.profile_store.load()
    assert (saved.sleep_start, saved.sleep_end, saved.weekend_sleep_end) == ('22:00', '07:00', '07:00')
    assert 'больше не актуальна' in h.handle_callback('101', stale_weekend)['text']


def test_sleep_explicit_weekend_wake_preset_persists(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    confirm = h.handle_callback('101', _click(
        h.handle_callback('101', _click(
            h.profile_preferences_start('sleep'), '22:00')), '07:00'))
    weekend = h.handle_callback('101', _click(confirm, 'Сон в выходные'))
    confirm = h.handle_callback('101', _click(weekend, '09:00'))
    h.handle_callback('101', _click(confirm, 'Сохранить'))
    saved = h.profile_store.load()
    assert (saved.sleep_start, saved.sleep_end, saved.weekend_sleep_end) == ('22:00', '07:00', '09:00')


def test_sleep_weekend_edit_is_draft_until_saved_and_cancel_keeps_settings(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    before = h.profile_store.load()
    confirm = h.handle_callback('101', _click(
        h.handle_callback('101', _click(
            h.profile_preferences_start('sleep'), '22:00')), '07:00'))
    weekend = h.handle_callback('101', _click(confirm, 'Сон в выходные'))
    confirm = h.handle_callback('101', _click(weekend, '09:00'))
    assert 'Выходные: подъём 09:00' in confirm['text']
    assert h.profile_store.load() == before
    h.handle_callback('101', _click(confirm, 'Отмена'))
    assert h.profile_store.load() == before
    assert 'больше не актуальна' in h.handle_callback('101', _click(weekend, '08:00'))['text']


def test_travel_custom_valid_invalid_and_confirmation(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    first = h.profile_preferences_start('travel')
    custom = h.handle_callback('101', _click(first, 'Другое время'))
    invalid = h.try_handle_profile_preferences('361')
    assert 'не подходит' in invalid['text']
    assert h.profile_store.load().profile.travel_minutes_each_way == 60
    confirm = h.try_handle_profile_preferences('37')
    assert '37 мин' in confirm['text'] and 'Сохранить?' in confirm['text']
    assert h.profile_store.load().profile.travel_minutes_each_way == 60
    h.handle_callback('101', _click(confirm, 'Сохранить'))
    assert h.profile_store.load().profile.travel_minutes_each_way == 37


def test_cancel_invalidates_draft_and_keeps_saved_value(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    saved = h.profile_store.load().profile.travel_minutes_each_way
    reply = h.profile_preferences_start('travel')
    stale = _click(reply, '30 мин')
    cancelled = h.handle_callback('101', _click(reply, 'Отмена'))
    assert 'Отменено' in cancelled['text']
    assert any(b['callback_data'] in {'main:sleep', 'main:travel', 'main:settings'}
               for row in cancelled['buttons'] for b in row)
    assert h.profile_store.load().profile.travel_minutes_each_way == saved
    stale_reply = h.handle_callback('101', stale)
    assert 'больше не актуальна' in stale_reply['text']
    assert any(b['callback_data'] in {'main:sleep', 'main:travel', 'main:settings'}
               for row in stale_reply['buttons'] for b in row)


def test_profile_card_updates_in_place_and_bad_input_keeps_controls(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    first = h.profile_preferences_start('sleep')
    assert first.get('edit_message') is True
    invalid = h.try_handle_profile_preferences('not-a-time')
    assert 'не подходит' in invalid['text']
    assert any(b['text'] == '22:00' for row in invalid['buttons'] for b in row)
    assert invalid.get('edit_message') is True
    next_stage = h.handle_callback('101', _click(invalid, '22:00'))
    assert next_stage.get('edit_message') is True


def test_old_flow_token_and_same_stage_replay_are_rejected(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    flow1 = h.profile_preferences_start('sleep')
    old = _click(flow1, '22:00')
    flow2 = h.profile_preferences_start('sleep')
    assert 'больше не актуальна' in h.handle_callback('101', old)['text']
    current = _click(flow2, '21:00')
    wake = h.handle_callback('101', current)
    assert 'встаёшь' in wake['text']
    assert 'больше не актуальна' in h.handle_callback('101', current)['text']


def test_profile_draft_resumes_after_handler_restart_and_chat_is_owned(tmp_path):
    account = _account(tmp_path)
    h = make_handler(tmp_path, account)
    opened = h.profile_preferences_start('travel')
    callback = _click(opened, '45 мин')
    assert h.handle_callback('999', callback) is None
    restarted = make_handler(tmp_path, account, state_directory=h.state_directory)
    confirm = restarted.handle_callback('101', callback)
    assert '45 мин' in confirm['text']
    assert restarted.profile_store.load().profile.travel_minutes_each_way == 60
    restarted.handle_callback('101', _click(confirm, 'Сохранить'))
    assert restarted.profile_store.load().profile.travel_minutes_each_way == 45


def test_corrupt_profile_draft_is_ignored_without_changing_settings(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path))
    path = h._profile_draft_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{bad json', encoding='utf-8')
    before = h.profile_store.load()
    assert h.try_handle_profile_preferences('45') is None
    assert h.profile_store.load() == before
    stale = h.handle_callback('101', 'pref:travel:oldtoken:confirm')
    assert 'больше не актуальна' in stale['text']
    assert h.profile_store.load() == before


def test_atomic_draft_write_failure_keeps_existing_flow(tmp_path, monkeypatch):
    account = _account(tmp_path)
    h = make_handler(tmp_path, account)
    first = h.profile_preferences_start('travel')
    previous = h._profile_draft_path().read_bytes()
    original_replace = __import__('os').replace
    path = h._profile_draft_path()

    def fail_target(source, destination):
        if destination == path:
            raise OSError('replacement failed')
        return original_replace(source, destination)

    monkeypatch.setattr('os.replace', fail_target)
    import pytest
    with pytest.raises(OSError):
        h._save_profile_draft({**h._profile_draft(), 'travel': 45})
    assert path.read_bytes() == previous
    monkeypatch.undo()
    resumed = make_handler(tmp_path, account, state_directory=h.state_directory)
    cancel = _click(first, 'Отмена')
    assert 'Отменено' in resumed.handle_callback('101', cancel)['text']
    assert resumed.profile_store.load().profile.travel_minutes_each_way == 60
    assert resumed._profile_draft() is None


def test_timezone_store_failure_is_retryable_and_does_not_probe_calendar(tmp_path, monkeypatch):
    h = make_handler(tmp_path, _account(tmp_path), connected=lambda: (_ for _ in ()).throw(AssertionError('probed')))
    h.handle_text('101', '/start')
    h.onboarding.store.save(type(h.onboarding.store.load())(step=OnboardingStep.TIMEZONE, source_id='tpu-primary', timezone_configured=False))
    original = h.source_service.state_store.save
    calls = {'n': 0}
    def fail_once(state):
        calls['n'] += 1
        if calls['n'] == 1: raise OSError('disk')
        return original(state)
    monkeypatch.setattr(h.source_service.state_store, 'save', fail_once)
    failed = h.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    assert 'Не удалось сохранить' in failed['text']
    assert h.onboarding.store.load().step is OnboardingStep.TIMEZONE
    retry = _click(failed, 'Asia/Tomsk')
    assert h.handle_callback('101', retry)['step'] == 'calendar'
    assert h.profile_store.load().profile.timezone == 'Asia/Tomsk'


def test_calendar_disconnected_and_error_states_have_recovery_buttons(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path), connected=lambda: False)
    h.onboarding.store.save(type(h.onboarding.store.load())(step=OnboardingStep.CALENDAR, source_id='tpu-primary', timezone_configured=True))
    disconnected = h.handle_callback('101', 'ob:calendar:check')
    callbacks = [b['callback_data'] for row in disconnected['buttons'] for b in row]
    assert 'main:connect_google' in callbacks and 'ob:calendar:check' in callbacks
    h.calendar_connected = lambda: (_ for _ in ()).throw(RuntimeError('provider secret'))
    failed = h.handle_callback('101', 'ob:calendar:check')
    assert 'Не удалось проверить' in failed['text']
    assert 'provider secret' not in failed['text']
    assert failed['buttons'][0][0]['callback_data'] == 'ob:calendar:check'


def test_calendar_check_when_complete_does_not_build_plan_preview(tmp_path):
    h = make_handler(tmp_path, _account(tmp_path), connected=lambda: True)
    h.onboarding.store.save(OnboardingState(
        step=OnboardingStep.COMPLETE, source_id='tpu-primary', timezone_configured=True,
        style_configured=True, sleep_configured=True, travel_configured=True,
    ))
    h.weekly_preview = lambda: (_ for _ in ()).throw(AssertionError('planner preview called'))

    response = h.handle_callback('101', 'ob:calendar:check')

    assert 'Google Calendar подключён' in response['text']
    assert any(button['callback_data'] == 'main:plan'
               for row in response['buttons'] for button in row)
    assert 'Черновик недели' not in response['text']
