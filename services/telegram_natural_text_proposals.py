"""Ownership-safe Telegram preview/confirm boundary for natural text."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from services.ai.intent_interpreter import IntentInterpreter
from services.application.command import CreateTaskCommand, CreateUniversityEventCommand
from services.application.proposed_command import ProposedCommand, ProposedCommandService
from services.natural_text_application import NaturalTextProposalStore
from services.natural_text_application import PerUserNaturalCommandExecutor
from services.user_registry import UserAccount

TEXT_PREVIEW_REQUESTS = frozenset({
    'покажи план', 'покажи расписание', 'мой план', 'моё расписание',
    'мое расписание', 'обнови расписание', 'обнови план',
})


def normalized_request(text):
    return ' '.join(text.casefold().strip(' .!?').split()) if isinstance(text, str) else ''


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

    def propose(self, chat_id: str, text: str, *, now: datetime | None = None) -> dict | None:
        if str(chat_id) != self.owner_chat_id:
            return None
        if not text.strip() or text.lstrip().startswith('/'):
            return {'text': 'Напиши действие обычным текстом.', 'buttons': []}
        # A new attempt supersedes old buttons even if interpretation fails.
        self.pending = None
        if self.store is not None:
            self.store.clear()
        self._run(self.interpreter.initialize())
        interpreted = self._run(self.interpreter.interpret_intent(text, {'now': now} if now else None))
        if not interpreted.success or interpreted.data is None:
            return {'text': interpreted.error or 'Нужно уточнить действие.', 'buttons': []}
        proposal = ProposedCommand(
            interpreted.data, text, interpreted.data.get_command_type(), interpreted.confidence,
        )
        if not self.proposals.validate(proposal):
            return {'text': 'Предложение не прошло проверку.', 'buttons': []}
        if self.store is not None:
            try:
                self.store.save(proposal)
            except ValueError:
                return {'text': 'Это действие пока не поддерживается.', 'buttons': []}
        self.pending = proposal
        return {
            'text': 'Предложение:\n' + self._preview(proposal.command),
            'buttons': [[
                {'text': 'Подтвердить', 'callback_data': f'nl:confirm:{proposal.id}'},
                {'text': 'Отклонить', 'callback_data': f'nl:reject:{proposal.id}'},
            ]],
        }

    def handle_callback(self, chat_id: str, data: str) -> dict | None:
        if str(chat_id) != self.owner_chat_id:
            return None
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
            if self.store is not None:
                self.store.clear()
            return {'text': 'Предложение отклонено. Данные не изменены.', 'buttons': []}
        if action != 'confirm':
            return {'text': 'Кнопка не распознана.', 'buttons': []}
        self.proposals.approve(proposal, approved_by='telegram_owner')
        try:
            self._run(self.proposals.execute(proposal))
        except Exception:
            self.pending = None
            if self.store is not None:
                self.store.clear()
            return {'text': 'Не удалось безопасно выполнить команду.', 'buttons': []}
        self.pending = None
        if self.store is not None:
            self.store.clear()
        message = 'Подтверждённое действие выполнено.'
        if isinstance(proposal.command, CreateTaskCommand):
            message += ' Задача сохранена в /tasks. Время в Calendar ей ещё не назначено.'
        return {'text': message, 'buttons': []}

    def list_tasks(self, chat_id: str, page: int = 1) -> dict | None:
        if str(chat_id) != self.owner_chat_id:
            return None
        tasks = self.proposals.application_service.snapshot()['tasks']
        pages = max(1, (len(tasks) + 9) // 10)
        if not 1 <= page <= pages:
            return {'text': 'Такой страницы задач нет. Открой /tasks.', 'buttons': []}
        lines = [f'Сохранённые задачи · {page}/{pages}']
        lines += [f'• {task["title"]}' for task in tasks[(page - 1) * 10:page * 10]]
        if not tasks:
            lines.append('Пока пусто. Напиши: «Добавь задачу купить учебник».')
        lines.append('Это список задач; время в Calendar им ещё не назначено.')
        navigation = []
        for target, label in ((page - 1, '‹ Назад'), (page + 1, 'Дальше ›')):
            if 1 <= target <= pages:
                navigation.append({'text': label, 'callback_data': f'nt:tasks:{target}'})
        return {'text': '\n'.join(lines), 'buttons': [navigation] if navigation else []}

    @staticmethod
    def _preview(command: Any) -> str:
        if isinstance(command, CreateTaskCommand):
            return f'Создать задачу: {command.title}'
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
