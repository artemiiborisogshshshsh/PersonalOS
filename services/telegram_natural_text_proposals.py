"""Ownership-safe Telegram preview/confirm boundary for natural text."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

from services.ai.intent_interpreter import IntentInterpreter
from services.application.command import CreateTaskCommand, CreateUniversityEventCommand
from services.application.proposed_command import ProposedCommand, ProposedCommandService
from services.natural_text_application import NaturalTextProposalStore
from services.natural_text_application import PerUserNaturalCommandExecutor
from services.natural_text_application import CompleteIntakeTaskCommand
from services.user_registry import UserAccount

TEXT_PREVIEW_REQUESTS = frozenset({
    'покажи план', 'покажи расписание', 'мой план', 'моё расписание',
    'мое расписание', 'обнови расписание', 'обнови план',
    'план', 'расписание', 'покажи мой план', 'покажи мое расписание',
    'покажи расписание на неделю', 'план на неделю',
    'показать план', 'показать расписание',
})
TEXT_TASK_REQUESTS = frozenset({'покажи задачи', 'покажи мои задачи', 'мои задачи',
                               'список задач', 'задачи', 'что нужно сделать', 'что мне нужно сделать',
                               'показать мои задачи', 'показать задачи'})
TASK_PREFIX = re.compile(r'^(?:(?:добавь|добавить|создай|создать|запиши)\s+задачу|'
                         r'добавь в задачи|задача|нужно сделать|надо сделать|мне нужно)\b\s*[:—-]?\s*', re.I)
TASK_COMPLETION = re.compile(r'(?:заверши задачу|отметь выполненной задачу|выполнил задачу|'
                             r'выполнила задачу)\s+(.+)', re.I)


def clean_request(text):
    if not isinstance(text, str):
        return ''
    value = ' '.join(text.strip().split())
    value = re.sub(r'^(?:пожалуйста[, ]+|можешь\s+|можно\s+)', '', value, flags=re.I)
    return re.sub(r'[, ]+пожалуйста[.!?]*$', '', value, flags=re.I).strip()


def normalized_request(text):
    return clean_request(text).casefold().replace('ё', 'е').strip(' .!?')


class TelegramNaturalTextProposalFlow:
    """Keeps interpreted commands inert until the owning chat confirms."""

    def __init__(self, owner_chat_id: str, interpreter: IntentInterpreter,
                 proposals: ProposedCommandService,
                 store: NaturalTextProposalStore | None = None):
        self.owner_chat_id = str(owner_chat_id)
        self.interpreter = interpreter
        self.proposals = proposals
        self.store = store
        self.pending: ProposedCommand | None = store.load() if store is not None else None
        self.awaiting_title = False

    @staticmethod
    def menu():
        return {'text': 'Что сделать?\n\nМожно нажать кнопку или написать:\n'
                '«Покажи план»\n«Добавь задачу купить учебник»\n«Мои задачи»\n\n'
                'В списке задач можно отметить выполненные.\n'
                'Команды: /task название, /tasks, /done название, /cancel.\n'
                'Переносы и создание встреч текстом ещё не поддерживаются.',
                'buttons': [[{'text': 'Показать план', 'callback_data': 'nt:plan'},
                             {'text': 'Мои задачи', 'callback_data': 'nt:tasks:1'}],
                            [{'text': 'Добавить задачу', 'callback_data': 'nt:new'}]]}

    def cancel(self):
        self.awaiting_title = False
        self.pending = None
        if self.store is not None:
            self.store.clear()
        return {'text': 'Действие отменено.', 'buttons': self.menu()['buttons']}

    def _offer(self, command, text='', confidence=1.0):
        proposal = ProposedCommand(command, text, command.get_command_type(), confidence)
        if not self.proposals.validate(proposal):
            return {'text': 'Проверь название задачи: от 1 до 300 символов.', 'buttons': []}
        if self.store is not None:
            self.store.save(proposal)
        self.pending = proposal
        self.awaiting_title = False
        return self._pending_reply()

    def _pending_reply(self):
        proposal = self.pending
        if proposal is None:
            return self.menu()
        completion = isinstance(proposal.command, CompleteIntakeTaskCommand)
        label = ('Отметить выполненной' if completion else 'Создать задачу'
                 if isinstance(proposal.command, CreateTaskCommand) else 'Создать событие')
        buttons = [[{'text': label,
                     'callback_data': f'nl:confirm:{proposal.id}'},
                    {'text': 'Отмена', 'callback_data': f'nl:reject:{proposal.id}'}]]
        if isinstance(proposal.command, CreateTaskCommand):
            buttons.append([{'text': 'Изменить название', 'callback_data': f'nt:edit:{proposal.id}'}])
        return {'text': self._preview(proposal.command), 'buttons': buttons}

    def _unknown_reply(self):
        response = self.menu()
        response['text'] = 'Нужно уточнить действие.\n\n' + response['text']
        if self.pending is not None:
            response['text'] += '\nПредыдущее предложение сохранено.'
            response['buttons'].append([{'text': 'Вернуться к предложению', 'callback_data': 'nt:resume'}])
        return response

    def request_completion(self, title):
        tasks = self.proposals.application_service.snapshot()['tasks']
        matches = [task for task in tasks if task['title'].casefold() == title.strip().casefold()
                   and task['status'] != 'done']
        if len(matches) == 1:
            return self._offer(CompleteIntakeTaskCommand(matches[0]))
        if matches:
            return {'text': 'Есть несколько задач с таким названием. Выбери нужную:',
                    'buttons': [[{'text': f'{index}. {task["title"][:40]}',
                                 'callback_data': f'nt:done:{task["id"]}'}]
                                for index, task in enumerate(matches[:10], 1)]}
        return {'text': 'Не нашёл незавершённую задачу с таким названием. Выбери её в списке.',
                'buttons': [[{'text': 'Мои задачи', 'callback_data': 'nt:tasks:1'}]]}

    def accepts_task_text(self, text):
        """Task intake works before Calendar setup without consuming setup input."""
        cleaned = clean_request(text)
        return self.awaiting_title or self.pending is not None or bool(
            TASK_PREFIX.match(cleaned) or TASK_COMPLETION.fullmatch(cleaned))

    def propose(self, chat_id: str, text: str, *, now: datetime | None = None) -> dict | None:
        if str(chat_id) != self.owner_chat_id:
            return None
        if not text.strip() or text.lstrip().startswith('/'):
            return {'text': 'Напиши действие обычным текстом.', 'buttons': []}
        if len(text) > 2000:
            return {'text': 'Запрос слишком длинный. Название задачи может содержать до 300 символов.',
                    'buttons': self.menu()['buttons']}
        if self.awaiting_title:
            title = text.strip()
            if not 1 <= len(title) <= 300:
                return {'text': 'Название должно содержать от 1 до 300 символов. Напиши его ещё раз или /cancel.',
                        'buttons': []}
            return self._offer(CreateTaskCommand(title, 'Created from confirmed natural-language proposal'))
        text = clean_request(text)
        completion = TASK_COMPLETION.fullmatch(text)
        if completion:
            return self.request_completion(completion[1])
        prefix = TASK_PREFIX.match(text)
        if prefix and 'create_task' in self.interpreter.supported_intents:
            title = text[prefix.end():].strip()
            if not title:
                return self._task_callback('nt:new')
            if len(title) <= 300:
                return self._offer(CreateTaskCommand(title, 'Created from confirmed natural-language proposal'))
            return {'text': 'Сократи название задачи до 300 символов или нажми «Добавить задачу».',
                    'buttons': self.menu()['buttons']}
        if set(self.interpreter.supported_intents) == {'create_task'}:
            return self._unknown_reply()
        self._run(self.interpreter.initialize())
        interpreted = self._run(self.interpreter.interpret_intent(text, {'now': now} if now else None))
        if not interpreted.success or interpreted.data is None:
            return self._unknown_reply()
        try:
            return self._offer(interpreted.data, text, interpreted.confidence)
        except ValueError:
            return {'text': 'Это действие пока не поддерживается.', 'buttons': self.menu()['buttons']}

    def handle_callback(self, chat_id: str, data: str) -> dict | None:
        if str(chat_id) != self.owner_chat_id:
            return None
        if data.startswith('nt:'):
            return self._task_callback(data)
        parts = data.split(':', maxsplit=2)
        if len(parts) != 3 or parts[0] != 'nl':
            return {'text': 'Кнопка не распознана.', 'buttons': []}
        action, proposal_id = parts[1], parts[2]
        proposal = self.pending
        if proposal is None or proposal.id != proposal_id:
            return {'text': 'Это предложение уже не актуально.', 'buttons': []}
        if action == 'reject':
            self.proposals.reject(proposal)
            self.pending = None
            self.awaiting_title = False
            if self.store is not None:
                self.store.clear()
            return {'text': 'Предложение отклонено. Данные не изменены.', 'buttons': self.menu()['buttons']}
        if action != 'confirm':
            return {'text': 'Кнопка не распознана.', 'buttons': []}
        if self.awaiting_title:
            return {'text': 'Сначала закончи ввод названия или вернись к прежнему предложению.',
                    'buttons': [[{'text': 'Вернуться к предложению', 'callback_data': 'nt:resume'},
                                 {'text': 'Отмена', 'callback_data': 'nt:cancel'}]]}
        self.proposals.approve(proposal, approved_by='telegram_owner')
        try:
            self._run(self.proposals.execute(proposal))
        except Exception:
            self.pending = None
            if self.store is not None:
                self.store.clear()
            return {'text': 'Не удалось безопасно выполнить команду. Открой /tasks и повтори действие.',
                    'buttons': self.menu()['buttons']}
        self.pending = None
        self.awaiting_title = False
        if self.store is not None:
            self.store.clear()
        message = 'Подтверждённое действие выполнено.'
        if isinstance(proposal.command, CreateTaskCommand):
            message += ' Задача сохранена в /tasks. Время в Calendar ей ещё не назначено.'
        if isinstance(proposal.command, CompleteIntakeTaskCommand):
            message = f'Готово: «{proposal.command.task["title"]}» отмечена выполненной.'
        return {'text': message, 'buttons': [[{'text': 'Мои задачи', 'callback_data': 'nt:tasks:1'},
                                              {'text': 'Добавить ещё', 'callback_data': 'nt:new'}]]}

    def _task_callback(self, data):
        if data == 'nt:new':
            self.awaiting_title = True
            return {'text': 'Что нужно сделать? Напиши название задачи одним сообщением.\n'
                    'Например: «Купить учебник». Для выхода — /cancel.',
                    'buttons': [[{'text': 'Отмена', 'callback_data': 'nt:cancel'}]]}
        if data == 'nt:cancel':
            return self.cancel()
        if data == 'nt:resume':
            self.awaiting_title = False
            return self._pending_reply()
        if data == 'nt:menu':
            self.awaiting_title = False
            return self.menu()
        if self.pending is not None and data == f'nt:edit:{self.pending.id}' and isinstance(self.pending.command, CreateTaskCommand):
            self.awaiting_title = True
            return {'text': f'Сейчас: «{self.pending.command.title}».\nНапиши новое название или /cancel.',
                    'buttons': [[{'text': 'Оставить прежнее', 'callback_data': 'nt:resume'},
                                 {'text': 'Отмена', 'callback_data': 'nt:cancel'}]]}
        page = re.fullmatch(r'nt:(tasks|completed):([0-9]{1,6})', data)
        if page:
            self.awaiting_title = False
            return self.list_tasks(self.owner_chat_id, int(page[2]), completed=page[1] == 'completed')
        done = re.fullmatch(r'nt:done:(task-[0-9a-f]{20})', data)
        if done:
            tasks = self.proposals.application_service.snapshot()['tasks']
            matches = [task for task in tasks if task['id'] == done[1] and task['status'] != 'done']
            if len(matches) == 1:
                return self._offer(CompleteIntakeTaskCommand(matches[0]))
            return {'text': 'Эта задача уже выполнена или недоступна. Обнови список.',
                    'buttons': [[{'text': 'Мои задачи', 'callback_data': 'nt:tasks:1'}]]}
        return {'text': 'Кнопка устарела. Открой меню или список задач.', 'buttons': self.menu()['buttons']}

    def list_tasks(self, chat_id: str, page: int = 1, *, completed=False) -> dict | None:
        if str(chat_id) != self.owner_chat_id:
            return None
        tasks = self.proposals.application_service.snapshot()['tasks']
        tasks = [task for task in tasks if (task['status'] == 'done') == completed]
        pages = max(1, (len(tasks) + 9) // 10)
        if not 1 <= page <= pages:
            return {'text': 'Такой страницы задач нет. Открой /tasks.', 'buttons': []}
        lines = [f'{"Выполненные" if completed else "Мои"} задачи · {page}/{pages}']
        lines += [f'• {task["title"]}' for task in tasks[(page - 1) * 10:page * 10]]
        if not tasks:
            lines.append('Пока пусто.' if completed else 'Пока пусто. Напиши: «Добавь задачу купить учебник».')
        lines.append('Это список задач; время в Calendar им ещё не назначено.')
        navigation = []
        for target, label in ((page - 1, '‹ Назад'), (page + 1, 'Дальше ›')):
            if 1 <= target <= pages:
                navigation.append({'text': label, 'callback_data': f'nt:{"completed" if completed else "tasks"}:{target}'})
        buttons = []
        if not completed:
            buttons = [[{'text': '✓ ' + task['title'][:40], 'callback_data': f'nt:done:{task["id"]}'}]
                       for task in tasks[(page - 1) * 10:page * 10]]
            if tasks:
                lines.append('Нажми на задачу ниже, чтобы отметить её выполненной.')
        if navigation:
            buttons.append(navigation)
        buttons.append([{'text': 'Добавить задачу', 'callback_data': 'nt:new'},
                        {'text': 'Активные' if completed else 'Выполненные',
                         'callback_data': 'nt:tasks:1' if completed else 'nt:completed:1'}])
        return {'text': '\n'.join(lines), 'buttons': buttons}

    @staticmethod
    def _preview(command: Any) -> str:
        if isinstance(command, CreateTaskCommand):
            return f'Создать задачу: {command.title}'
        if isinstance(command, CompleteIntakeTaskCommand):
            return f'Отметить выполненной: «{command.task["title"]}»?'
        if isinstance(command, CreateUniversityEventCommand):
            return f'Создать личное событие: {command.summary} — {command.dtstart:%d.%m %H:%M}'
        return f'Выполнить: {command.get_command_type()}'

    @staticmethod
    def _run(awaitable):
        return asyncio.run(awaitable)


def create_per_user_natural_text_flow(
    account: UserAccount, state_directory, *, supported_intents=None,
) -> TelegramNaturalTextProposalFlow:
    """Compose the flow only from the router-owned account and directory."""
    executor = PerUserNaturalCommandExecutor(state_directory / 'natural_commands.json')
    return TelegramNaturalTextProposalFlow(
        account.telegram_chat_id,
        IntentInterpreter({'supported_intents': supported_intents} if supported_intents is not None else None),
        ProposedCommandService(executor),
        NaturalTextProposalStore(state_directory / 'natural_text_proposal.json'),
    )
