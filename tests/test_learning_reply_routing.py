"""Route replies to homework prompts before standalone natural-task parsing."""
from unittest.mock import Mock

import pytest

from tests.test_telegram_learning_flow import flow_app


@pytest.mark.parametrize('text', ['нужно сделать отчёт', 'сделать отчёт'])
def test_homework_prompt_reply_is_not_intercepted_as_a_standalone_task(tmp_path, monkeypatch, text):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    app.learning.bind_message(801, lesson['id'], 'material')
    monkeypatch.setattr(app.natural_text, 'accepts_task_text', Mock(return_value=True))
    propose = Mock(return_value={'text': 'Standalone task preview', 'buttons': [['confirm']]})
    monkeypatch.setattr(app.natural_text, 'propose', propose)

    result = app.handle_text('101', text, message={
        'message_id': 802, 'reply_to_message': {'message_id': 801},
    })

    assert 'Сохранено к занятию' in result['text']
    assert app.learning.store.get_lesson(lesson['id'])['materials'][-1]['text'] == text
    propose.assert_not_called()


def test_unknown_prompt_reply_does_not_use_stale_lesson_or_task_routing(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    app.learning.store.set_context(lesson['id'])
    monkeypatch.setattr(app.natural_text, 'accepts_task_text', Mock(return_value=True))
    propose = Mock(return_value={'text': 'Standalone task preview', 'buttons': [['confirm']]})
    monkeypatch.setattr(app.natural_text, 'propose', propose)

    result = app.handle_text('101', 'нужно сделать отчёт', message={
        'message_id': 811, 'reply_to_message': {'message_id': 999},
    })

    assert result['buttons']
    assert not app.learning.store.get_lesson(lesson['id'])['materials']
    propose.assert_not_called()


def test_explicit_slash_task_command_still_works_in_a_reply(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    app.learning.bind_message(821, lesson['id'], 'material')
    propose = Mock(return_value={'text': 'Standalone task preview', 'buttons': [['confirm']]})
    monkeypatch.setattr(app.natural_text, 'propose', propose)

    result = app.handle_text('101', '/task сделать отчёт', message={
        'message_id': 822, 'reply_to_message': {'message_id': 821},
    })

    assert result['text'] == 'Standalone task preview'
    propose.assert_called_once()


@pytest.mark.parametrize('exit_route', ['main:open', '/menu', '/cancel'])
def test_menu_or_cancel_closes_homework_estimate_editor(tmp_path, monkeypatch, exit_route):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    original_duration = app.learning.store.get_lesson(lesson['id'])['duration_minutes']
    buttons = app.learning._homework_controls(lesson)
    duration_button = next(button['callback_data'] for row in buttons for button in row
                           if button['text'] == 'Изменить время')
    prompt = app.learning.handle_callback(duration_button)
    app.learning.bind_message(831, lesson['id'], prompt['_learning_kind'])

    if exit_route == 'main:open':
        app.handle_callback('101', exit_route)
    else:
        app.handle_text('101', exit_route)

    assert app.learning._state().get('estimate_input') is None
    assert app.learning.reply_context({'reply_to_message': {'message_id': 831}}) == (None, True)
    app.handle_text('101', '2 часа')
    assert app.learning.store.get_lesson(lesson['id'])['duration_minutes'] == original_duration
