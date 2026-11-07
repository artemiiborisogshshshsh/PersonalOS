from services.telegram_main_menu import menu_reply
from services.telegram_user_router import TelegramUserRouter
from services.user_registry import UserRegistryStore, UserStatePaths
from tests.test_closed_beta_runtime import application, onboard


def test_completed_start_help_and_menu_show_message_attached_menu(tmp_path):
    app = application(tmp_path)
    onboard(app)
    for command in ('/start', '/menu', '/help'):
        response = app.handle_text('101', command)
        assert response['buttons'] == menu_reply()['buttons']
        assert all('callback_data' in button for row in response['buttons'] for button in row)
        assert 'reply_markup' not in response


def test_main_menu_callbacks_route_to_existing_handlers_and_return(tmp_path, monkeypatch):
    app = application(tmp_path)
    onboard(app)
    seen = []
    monkeypatch.setattr(app, 'handle_text', lambda chat_id, text, **kwargs:
                        seen.append(text) or {'text': text, 'buttons': []})
    monkeypatch.setattr(app, 'management_home', lambda: {'text': 'settings', 'buttons': []})
    monkeypatch.setattr(app.handler, 'attendance_editor_start', lambda: {'text': 'attendance', 'buttons': []})
    monkeypatch.setattr(app.learning, 'material_entry', lambda: {'text': 'select lesson', 'buttons': []})
    for action, expected in [('plan', '/weekly_preview'), ('tasks', '/tasks'),
                             ('referral', '/referral')]:
        reply = app.handle_callback('101', 'main:' + action)
        assert seen[-1] == expected
        assert reply['buttons'][-1][0]['callback_data'] == 'main:open'
    assert app.handle_callback('101', 'main:materials')['text'] == 'select lesson'
    assert app.handle_callback('101', 'main:attendance')['text'] == 'attendance'
    settings = app.handle_callback('101', 'main:settings')
    assert any(button['callback_data'] == 'main:calendar'
               for row in settings['buttons'] for button in row)
    assert app.handle_callback('101', 'main:open')['buttons'] == menu_reply()['buttons']


def test_user_router_dispatches_real_menu_callback(tmp_path):
    app = application(tmp_path)
    onboard(app)
    router = TelegramUserRouter(
        UserRegistryStore(tmp_path / 'users_registry.json'), UserStatePaths(tmp_path),
        lambda account, directory: app,
    )
    menu = router.handle_text('101', '/start')
    callback = next(button['callback_data'] for row in menu['buttons'] for button in row
                    if button['text'] == 'Посещаемость')
    result = router.handle_callback('101', callback)
    assert 'Посещаемость' in result['text']
    assert all(len(button['callback_data'].encode()) <= 64
               for row in menu['buttons'] for button in row)


def test_settings_inputs_use_existing_validators_and_can_be_cancelled(tmp_path):
    app = application(tmp_path)
    onboard(app)
    app.handle_callback('101', 'main:sleep')
    invalid = app.handle_text('101', '25:99')
    assert 'не подходит' in invalid['text']
    bedtime = app.handle_text('101', '23:00')
    wake = app.handle_text('101', '07:00')
    saved_sleep = app.handle_callback('101', next(
        button['callback_data'] for row in wake['buttons'] for button in row if button['text'] == 'Сохранить'))
    assert 'Настройка завершена' in saved_sleep['text']
    assert app.profile.load().sleep_start == '23:00'
    assert app.onboarding.store.load().step.value == 'complete'
    app.handle_callback('101', 'main:travel')
    confirm = app.handle_text('101', '35')
    saved_travel = app.handle_callback('101', next(
        button['callback_data'] for row in confirm['buttons'] for button in row if button['text'] == 'Сохранить'))
    assert 'Настройка завершена' in saved_travel['text']
    assert app.profile.load().profile.travel_minutes_each_way == 35
    assert app.onboarding.store.load().step.value == 'complete'
    app.handle_callback('101', 'main:travel')
    invalid_travel = app.handle_text('101', '361')
    assert 'от 0 до 360' in invalid_travel['text']
    assert app.profile.load().profile.travel_minutes_each_way == 35
    app.handle_callback('101', 'main:travel')
    cancelled = app.handle_callback('101', 'main:cancel')
    assert cancelled['buttons'] == menu_reply()['buttons']
    assert app.menu_setting_pending is None


def test_expired_menu_keeps_saved_read_views_and_denies_writes(tmp_path):
    from datetime import timedelta
    from tests.test_closed_beta_runtime import NOW

    app = application(tmp_path)
    onboard(app)
    app.access.store.start_trial(app.account.id, now=NOW)
    app.now = lambda: NOW + timedelta(days=21)
    assert 'Доступ' in app.handle_callback('101', 'main:access')['text']
    assert 'Сохранённые задачи' in app.handle_callback('101', 'main:tasks')['text']
    assert 'Сохранённые материалы' in app.handle_callback('101', 'main:materials_saved')['text']
    denied = app.handle_callback('101', 'main:plan')
    assert 'Данные сохранены' in denied['text']
    denied_material = app.handle_callback('101', 'main:materials')
    assert 'Данные сохранены' in denied_material['text']


def test_leaving_material_picker_invalidates_old_selection_callback(tmp_path):
    app = application(tmp_path)
    onboard(app)
    state = app.learning._state()
    state['selection'] = {'token': 'stale-token', 'pending': {'entry': 'material'}, 'lessons': []}
    app.learning._save(state)
    app.handle_callback('101', 'main:open')
    stale = app.handle_callback('101', 'learn:select:stale-token:0')
    assert 'устарела' in stale['text']
    assert app.learning._state().get('selection') is None


def test_typed_menu_commands_invalidate_old_material_selection(tmp_path):
    app = application(tmp_path)
    onboard(app)
    for command in ('/menu', '/help', '/start'):
        state = app.learning._state()
        state['selection'] = {'token': 'stale-token', 'pending': {'entry': 'material'}, 'lessons': []}
        app.learning._save(state)
        app.handle_text('101', command)
        stale = app.handle_callback('101', 'learn:select:stale-token:0')
        assert 'устарела' in stale['text']
