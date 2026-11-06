from tests.test_telegram_learning_flow import flow_app


def _assigned(app, lesson):
    app.learning._save_material(lesson, {'text': 'задали номера 1–3'})
    return app.learning.store.get_lesson(lesson['id'])


def test_date_only_edit_of_assigned_homework_returns_receipt_and_controls(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    count = len(lesson['materials'])
    reply = app.learning.handle_text('к 30.09', lesson_id=lesson['id'])
    updated = app.learning.store.get_lesson(lesson['id'])
    assert len(updated['materials']) == count
    assert 'Подготовка' in reply['text']
    actions = [b['callback_data'] for row in reply['buttons'] for b in row]
    assert sum(action.startswith('learn:estimate:') for action in actions) == 2


def test_duration_edit_and_pending_sync_return_assigned_homework_receipt(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    app.sync_learning = lambda: 'Данные сохранены. Обновление Google пока не завершено.'
    reply = app.learning.handle_text('Нужно 90 минут', lesson_id=lesson['id'])
    assert 'пока не подтверждена в Google Calendar' in reply['text']
    actions = [b['callback_data'] for row in reply['buttons'] for b in row]
    assert sum(action.startswith('learn:estimate:') for action in actions) == 2
    assert len(app.learning.store.get_lesson(lesson['id'])['materials']) == 1


def test_unassigned_duration_and_course_default_do_not_claim_homework_receipt(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    reply = app.learning.handle_text('Обычно нужно 90 минут', lesson_id=lesson['id'])
    assert 'Интервалы из подтверждённого плана' not in reply['text']
    assert 'ДЗ сохранено по занятию' not in reply['text']
    assert not app.learning.store.get_lesson(lesson['id'])['materials']


def test_explicit_deadline_time_and_year_are_preserved_without_adding_material(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = _assigned(app, lessons[0])
    original_count = len(lesson['materials'])
    saved = app.learning.handle_text('срок 12.11 00:00', lesson_id=lesson['id'])
    due = app.learning.store.get_lesson(lesson['id'])['due_at']
    assert due.startswith('2026-11-12T00:00')
    assert len(app.learning.store.get_lesson(lesson['id'])['materials']) == original_count
    assert 'Изменить срок' in str(saved['buttons'])

    callback = next(button['callback_data'] for row in saved['buttons'] for button in row
                    if button['text'] == 'Изменить срок')
    app.learning.handle_callback(callback)
    lesson = app.learning.store.get_lesson(lesson['id'])
    revision = app.learning._state()['estimate_input']['revision']
    app.learning.handle_text('12.11.2026 00:00', lesson_id=lesson['id'],
                             prompt_kind='estimate_deadline', prompt_revision=revision)
    assert app.learning.store.get_lesson(lesson['id'])['due_at'].startswith('2026-11-12T00:00')


def test_deadline_parser_prefix_time_validation_and_legacy_weekday(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    parse = app.learning._deadline
    assert parse('к 12.11 в17:30').hour == 17
    assert parse('к 12.11 в17:30').minute == 30
    assert parse('к 12.11 00:00 нужно сдать отчёт').hour == 0
    assert parse('задали к 12.11 00:00, сделать упражнение').minute == 0
    assert parse('к пятнице').hour == 9
    assert parse('задали к пятнице').hour == 9
    assert parse('задача 12.11') is None
    for invalid in ('к 31.02', 'срок 12.11 25:00', 'до 12.11 10:99',
                    'к 12.11 10:9 нужно сдать'):
        try:
            parse(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{invalid!r} should be rejected')
