from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.telegram_learning_flow import TelegramLearningFlow
from tests.test_telegram_learning_flow import flow_app


def _assigned(app, lesson):
    app.learning._save_material(lesson, {'text':'задали номера 1–3'})
    return app.learning.store.get_lesson(lesson['id'])


def _button(reply, text):
    button = next(b for row in reply['buttons'] for b in row if b['text'] == text)
    assert len(button['callback_data'].encode()) <= 64
    return button['callback_data']


def _open(app, lesson, field):
    reply = {'buttons': app.learning._homework_controls(lesson)}
    return app.learning.handle_callback(_button(reply, 'Изменить время' if field == 'duration' else 'Изменить срок'))


def test_duration_presets_require_confirmation_then_sync_once(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    calls = []
    monkeypatch.setattr(app, 'sync_learning', lambda: calls.append('sync') or 'Google Calendar обновлён.')
    picker = _open(app, lesson, 'duration')
    assert 'Оценка сейчас' in picker['text']
    assert [len(row) for row in picker['buttons'][:2]] == [3, 3]
    preset = _button(picker, '45 мин')
    confirm = app.handle_callback('101', preset)
    assert '45 мин' in confirm['text'] and 'Сохранить?' in confirm['text']
    assert app.learning.store.get_lesson(lesson['id'])['duration_minutes'] == lesson['duration_minutes']
    assert not calls
    result = app.handle_callback('101', _button(confirm, 'Сохранить'))
    assert app.learning.store.get_lesson(lesson['id'])['duration_minutes'] == 45
    assert calls == ['sync']
    assert 'Время на ДЗ сохранено' in result['text']


def test_duration_custom_invalid_retry_cancel_and_no_mutation(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    before = app.learning.store.get_lesson(lesson['id'])['duration_minutes']
    picker = _open(app, lesson, 'duration')
    custom = app.handle_callback('101', _button(picker, 'Ввести своё значение'))
    invalid = app.handle_text('101', '0')
    assert 'от 1 минуты до 7 дней' in invalid['text']
    assert app.learning.store.get_lesson(lesson['id'])['duration_minutes'] == before
    confirm = app.handle_text('101', '75 минут')
    assert '75 мин' in confirm['text'] and 'Сохранить?' in confirm['text']
    cancelled = app.handle_callback('101', _button(confirm, 'Отмена'))
    assert 'отменено' in cancelled['text'].lower()
    assert app.learning.store.get_lesson(lesson['id'])['duration_minutes'] == before


def test_due_date_and_time_presets_are_draft_until_confirmed(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    calls = []
    monkeypatch.setattr(app, 'sync_learning', lambda: calls.append(1) or 'Google Calendar обновлён.')
    picker = _open(app, lesson, 'deadline')
    assert 'Срок сейчас:' in picker['text'] and 'Asia/Tomsk' in picker['text']
    date = (app.now().astimezone(ZoneInfo('Asia/Tomsk')).date() + timedelta(days=1)).strftime('%d.%m.%Y')
    time_picker = app.handle_callback('101', _button(picker, 'Завтра ' + date))
    confirm = app.handle_callback('101', _button(time_picker, '18:00'))
    assert date in confirm['text'] and '18:00' in confirm['text'] and 'Сохранить?' in confirm['text']
    assert app.learning.store.get_lesson(lesson['id']).get('due_at') is None
    assert not calls
    result = app.handle_callback('101', _button(confirm, 'Сохранить'))
    saved = app.learning.store.get_lesson(lesson['id'])
    assert datetime.fromisoformat(saved['due_at']).astimezone(ZoneInfo('Asia/Tomsk')).strftime('%d.%m.%Y %H:%M') == date + ' 18:00'
    assert calls == [1]
    assert 'Срок сдачи сохранён' in result['text']


def test_custom_deadline_validates_past_date_and_time_and_retries(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    picker = _open(app, lesson, 'deadline')
    custom_date = app.handle_callback('101', _button(picker, 'Другая дата'))
    past = (app.now().date() - timedelta(days=1)).strftime('%d.%m.%Y')
    rejected = app.handle_text('101', past)
    assert 'Не распознал дату' in rejected['text']
    future = (app.now().date() + timedelta(days=3)).strftime('%d.%m.%Y')
    time_picker = app.handle_text('101', future)
    custom_time = app.handle_callback('101', _button(time_picker, 'Другое время'))
    invalid_time = app.handle_text('101', '25:00')
    assert 'будущее время' in invalid_time['text']
    selected_time = app.handle_text('101', '12:00')
    assert 'Сохранить?' in selected_time['text'] and future in selected_time['text']
    assert app.learning.store.get_lesson(lesson['id']).get('due_at') is None


def test_stale_revision_and_replayed_step_callbacks_cannot_mutate_lesson(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    picker = _open(app, lesson, 'duration')
    first = _button(picker, '45 мин')
    confirm = app.handle_callback('101', first)
    stale_step = app.handle_callback('101', first)
    assert 'не изменено' in stale_step['text']
    assert app.learning.store.get_lesson(lesson['id'])['duration_minutes'] != 45
    app.learning._save_material(app.learning.store.get_lesson(lesson['id']), {'text':'изменённое ДЗ'})
    stale_confirm = app.handle_callback('101', _button(confirm, 'Сохранить'))
    assert 'Занятие изменилось' in stale_confirm['text']
    assert app.learning.store.get_lesson(lesson['id'])['duration_minutes'] != 45


def test_draft_survives_learning_flow_restart_and_binds_current_lesson(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    picker = _open(app, lesson, 'duration')
    confirm = app.handle_callback('101', _button(picker, '90 мин'))
    app.learning = TelegramLearningFlow(app)
    assert app.learning.store.get_context()['lesson_id'] == lesson['id']
    saved = app.handle_callback('101', _button(confirm, 'Сохранить'))
    assert '90 мин' in saved['text']
    assert app.learning.store.get_lesson(lesson['id'])['duration_minutes'] == 90


def test_pending_callback_isolated_from_another_users_learning_state(tmp_path, monkeypatch):
    from tests.test_closed_beta_runtime import application
    app, lessons = flow_app(tmp_path / 'first', monkeypatch)
    lesson = _assigned(app, lessons[0])
    picker = _open(app, lesson, 'duration')
    pending = app.handle_callback('101', _button(picker, '45 мин'))
    confirm_callback = _button(pending, 'Сохранить')
    other = application(tmp_path / 'second')
    result = other.handle_callback('101', confirm_callback)
    assert 'устарела' in result['text'].lower()
    assert all(row.get('duration_minutes') != 45 for row in other.learning.store.lessons())


def test_date_presets_roll_into_next_calendar_year(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    app.now = lambda: datetime(2026, 12, 30, 10, tzinfo=ZoneInfo('Asia/Tomsk'))
    picker = _open(app, lesson, 'deadline')
    picker = app.learning.handle_callback(_button(picker, 'пт 01.01.2027'))
    assert '01.01.2027' in picker['text']
    assert 'Сохранить?' not in picker['text']


def test_november_twelfth_midnight_is_reachable_entirely_by_buttons(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    app.now = lambda: datetime(2026, 11, 7, 9, tzinfo=ZoneInfo('Asia/Tomsk'))
    picker = _open(app, lesson, 'deadline')
    date_button = next(b for row in picker['buttons'] for b in row if '12.11.2026' in b['text'])
    time_picker = app.learning.handle_callback(date_button['callback_data'])
    confirm = app.learning.handle_callback(_button(time_picker, '00:00'))
    assert '12.11.2026 00:00' in confirm['text']
    result = app.learning.handle_callback(_button(confirm, 'Сохранить'))
    actual = datetime.fromisoformat(app.learning.store.get_lesson(lesson['id'])['due_at'])
    assert actual.astimezone(ZoneInfo('Asia/Tomsk')).strftime('%d.%m.%Y %H:%M') == '12.11.2026 00:00'
    assert '12.11.2026 00:00' in result['text']


def test_date_week_pages_rotate_tokens_and_cancel(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    first = _open(app, lesson, 'deadline')
    next_week_callback = _button(first, 'Следующая неделя →')
    second = app.learning.handle_callback(next_week_callback)
    assert any('29.09.2026' in b['text'] or '30.09.2026' in b['text'] or '01.10.2026' in b['text']
               for row in second['buttons'] for b in row)
    old_replay = app.learning.handle_callback(next_week_callback)
    assert 'устарела' in old_replay['text'].lower()
    back = app.learning.handle_callback(_button(second, '← Предыдущая неделя'))
    assert any('Сегодня' in b['text'] for row in back['buttons'] for b in row)
    cancelled = app.learning.handle_callback(_button(back, 'Отмена'))
    assert 'отменено' in cancelled['text'].lower()
    assert app.learning._state().get('estimate_input') is None
    assert app.learning.store.get_lesson(lesson['id']).get('due_at') is None


def test_expired_time_error_reply_stays_bound_to_original_lesson(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson_a, lesson_b = lessons[:2]
    lesson_a = _assigned(app, lesson_a)
    syncs = []
    monkeypatch.setattr(app, 'sync_learning', lambda: syncs.append(1) or 'Google Calendar обновлён.')
    picker = _open(app, lesson_a, 'deadline')
    date = (app.now().date()).strftime('%d.%m.%Y')
    time_picker = app.learning.handle_callback(_button(picker, 'Сегодня ' + date))
    confirm = app.learning.handle_callback(_button(time_picker, '12:00'))
    app.learning.store.set_context(lesson_b['id'], kind='attendance')
    app.now = lambda: datetime(2026, 9, 22, 13, tzinfo=ZoneInfo('Asia/Tomsk'))
    expired = app.handle_callback('101', _button(confirm, 'Сохранить'))
    assert expired['_learning_lesson_id'] == lesson_a['id']
    assert expired['_learning_kind'] == 'estimate_deadline'
    assert app.learning.store.get_context()['lesson_id'] == lesson_a['id']
    unthreaded = app.handle_text('101', 'к пятнице')
    assert 'Сохранить?' in unthreaded['text']
    assert app.learning.store.get_lesson(lesson_a['id']).get('due_at') is None
    assert app.learning.store.get_lesson(lesson_b['id']).get('due_at') is None
    assert not app.learning.store.get_lesson(lesson_b['id'])['materials']
    assert not syncs
    app.learning.bind_message(8101, expired['_learning_lesson_id'], expired['_learning_kind'])
    retry = app.handle_text('101', 'По Физике задали номера 99', message={
        'message_id': 8102, 'reply_to_message': {'message_id': 8101}})
    assert retry['_learning_lesson_id'] == lesson_a['id']
    assert app.learning.store.get_lesson(lesson_a['id']).get('due_at') is None
    assert not app.learning.store.get_lesson(lesson_b['id'])['materials']
    assert not syncs
