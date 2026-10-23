"""Conversational paths and durable confirmations in Update 1."""
from functools import partial
from unittest.mock import Mock

import pytest

from services.closed_beta_runtime import ClosedBetaApplication, PilotSource
from services.invited_beta_runtime import InvitedBot
from services.natural_text_application import PerUserNaturalCommandExecutor
from tests.test_closed_beta_runtime import Calendar, ICS, NOW, confirmation
from tests.test_invited_beta import envelope, onboard


@pytest.fixture
def bot(tmp_path, monkeypatch):
    calendars = {'101': Calendar(), '202': Calendar()}
    def fetch(url, path, **kwargs):
        path.write_bytes(ICS)
    factory = partial(ClosedBetaApplication, enable_plan_updates=True, now=lambda: NOW,
                      source_factory=lambda store: PilotSource(store, fetcher=fetch))
    instance = InvitedBot('synthetic', tmp_path, calendars.__getitem__, factory)
    monkeypatch.setattr(instance, 'send_message', Mock())
    for chat in calendars:
        instance.invites.set_access(chat, True)
        onboard(instance, chat)
    counter = {'101': 100, '202': 100}
    def send(text=None, data=None, chat='101'):
        counter[chat] += 1
        return instance.process_update(envelope(counter[chat], chat, text or '', data=data))
    instance.send = send
    instance.calendars = calendars
    return instance


def button(reply, prefix):
    return next(item['callback_data'] for row in reply['buttons'] for item in row
                if item['callback_data'].startswith(prefix))


def snapshot(bot, chat='101'):
    app = bot.applications[chat][1]
    return PerUserNaturalCommandExecutor(app.directory / 'natural_commands.json').snapshot()


@pytest.mark.parametrize('text', ['план', 'Пожалуйста, покажи план!', 'можешь показать план?',
                                'покажи расписание на неделю', 'обнови расписание, пожалуйста'])
def test_plan_aliases_use_same_preview_and_progress_ack(bot, text):
    reply = bot.send(text)
    assert confirmation(reply).startswith('pilot:apply:')
    bot.send_message.assert_called_once_with('101', bot.PREVIEW_ACK)
    assert not bot.calendars['101'].writes


@pytest.mark.parametrize('text', ['Добавь в задачи Купить учебник', 'Запиши задачу Купить учебник',
                                'Пожалуйста, добавь задачу Купить учебник', 'Мне нужно Купить учебник'])
def test_task_phrasings_preserve_title_case_and_require_confirmation(bot, text):
    reply = bot.send(text)
    assert reply['text'] == 'Создать задачу: Купить учебник'
    assert snapshot(bot)['tasks'] == []
    bot.send(data=confirmation(reply))
    assert snapshot(bot)['tasks'][0]['title'] == 'Купить учебник'


def test_menu_wizard_edit_cancel_and_completion(bot):
    menu = bot.send('/help')
    prompt = bot.send(data=button(menu, 'nt:new'))
    assert 'Что нужно сделать' in prompt['text']
    preview = bot.send('Купить учебник')
    edit = bot.send(data=button(preview, 'nt:edit:'))
    assert 'Купить учебник' in edit['text']
    assert 'Сначала закончи' in bot.send(data=confirmation(preview))['text']
    revised = bot.send('Купить два учебника')
    assert 'уже не актуально' in bot.send(data=confirmation(preview))['text']
    bot.send(data=confirmation(revised))
    tasks = bot.send('можешь показать мои задачи?')
    completion = bot.send(data=button(tasks, 'nt:done:'))
    assert snapshot(bot)['tasks'][0]['status'] == 'todo'
    bot.send(data=confirmation(completion))
    assert snapshot(bot)['tasks'][0]['status'] == 'done'
    assert 'Пока пусто' in bot.send('/tasks')['text']
    completed = bot.send(data='nt:completed:1')
    assert 'Купить два учебника' in completed['text']
    bot.send(data='nt:new')
    bot.send('/cancel')
    assert not bot.applications['101'][1].natural_text.awaiting_title
    assert all(not calendar.writes for calendar in bot.calendars.values())


def test_incomplete_create_prompts_for_title_and_back_restores_previous(bot):
    old = bot.send('Добавь задачу купить учебник')
    prompt = bot.send('Создай задачу')
    assert 'Что нужно сделать' in prompt['text']
    assert snapshot(bot)['tasks'] == []
    assert 'Сначала закончи' in bot.send(data=confirmation(old))['text']
    resumed = bot.send(data='nt:resume')
    assert confirmation(resumed) == confirmation(old)
    bot.send(data=confirmation(resumed))
    assert len(snapshot(bot)['tasks']) == 1


def test_unknown_or_negative_request_does_not_replace_pending_task(bot):
    old = bot.send('Добавь задачу купить учебник')
    for text in ('не добавь задачу новое название', 'что-нибудь сделай', 'не показывай план'):
        reply = bot.send(text)
        assert 'предложение сохранено' in reply['text']
    bot.send(data=confirmation(old))
    assert [task['title'] for task in snapshot(bot)['tasks']] == ['купить учебник']
    bot.send_message.assert_not_called()


def test_completion_restart_replay_and_foreign_buttons(bot):
    created = bot.send('Добавь задачу купить учебник')
    bot.send(data=confirmation(created))
    completion = bot.send('Заверши задачу купить учебник')
    callback = confirmation(completion)
    foreign = bot.send(data=callback, chat='202')
    assert 'уже не актуально' in foreign['text']
    bot.applications.pop('101')  # Reconstruct from saved per-user state.
    assert 'отмечена выполненной' in bot.send(data=callback)['text']
    assert 'уже не актуально' in bot.send(data=callback)['text']
    assert snapshot(bot)['tasks'][0]['status'] == 'done'
    assert snapshot(bot, '202')['tasks'] == []


def test_task_change_after_completion_preview_stops_confirmation(bot):
    created = bot.send('Добавь задачу купить учебник')
    bot.send(data=confirmation(created))
    completion = bot.send('/done купить учебник')
    app = bot.applications['101'][1]
    executor = PerUserNaturalCommandExecutor(app.directory / 'natural_commands.json')
    state = executor.snapshot()
    state['tasks'][0]['title'] = 'Изменённая задача'
    executor._save(state)
    reply = bot.send(data=confirmation(completion))
    assert 'Не удалось' in reply['text']
    assert executor.snapshot()['tasks'][0]['status'] == 'todo'


def test_duplicate_titles_require_explicit_selection(bot):
    for _ in range(2):
        created = bot.send('Добавь задачу купить учебник')
        bot.send(data=confirmation(created))
    choices = bot.send('Заверши задачу купить учебник')
    assert 'несколько задач' in choices['text']
    assert len(choices['buttons']) == 2
    completion = bot.send(data=choices['buttons'][1][0]['callback_data'])
    bot.send(data=confirmation(completion))
    assert [task['status'] for task in snapshot(bot)['tasks']] == ['todo', 'done']


def test_pagination_does_not_retarget_old_task_button(bot):
    for index in range(11):
        preview = bot.send(f'Добавь задачу Учебник {index}')
        bot.send(data=confirmation(preview))
    first_page = bot.send('/tasks')
    old_button = button(first_page, 'nt:done:')
    next_page = bot.send(data=button(first_page, 'nt:tasks:2'))
    assert 'Учебник 10' in next_page['text']
    completion = bot.send(data=old_button)
    bot.send(data=confirmation(completion))
    assert snapshot(bot)['tasks'][0]['status'] == 'done'
    assert snapshot(bot)['tasks'][10]['status'] == 'todo'


def test_oversized_title_does_not_create_or_lose_pending_task(bot):
    old = bot.send('Добавь задачу купить учебник')
    assert '300' in bot.send('Добавь задачу ' + 'я' * 301)['text']
    bot.send(data=confirmation(old))
    assert len(snapshot(bot)['tasks']) == 1


def test_menu_plan_button_acknowledges_and_does_not_publish(bot):
    menu = bot.send('/menu')
    preview = bot.send(data=button(menu, 'nt:plan'))
    assert confirmation(preview).startswith('pilot:apply:')
    bot.send_message.assert_called_once_with('101', bot.PREVIEW_ACK)
    assert not bot.calendars['101'].writes


def test_task_slash_command_and_cancel_clear_pending_confirmation(bot):
    assert 'Что нужно сделать' in bot.send('/task')['text']
    preview = bot.send('Купить учебник')
    bot.send('/cancel')
    assert 'уже не актуально' in bot.send(data=confirmation(preview))['text']
    direct = bot.send('/task Купить билет')
    bot.send(data=confirmation(direct))
    assert [task['title'] for task in snapshot(bot)['tasks']] == ['Купить билет']
