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
        return {'text': 'Подтверждённое действие выполнено.', 'buttons': []}

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
    account: UserAccount, state_directory,
) -> TelegramNaturalTextProposalFlow:
    """Compose the flow only from the router-owned account and directory."""
    executor = PerUserNaturalCommandExecutor(state_directory / 'natural_commands.json')
    return TelegramNaturalTextProposalFlow(
        account.telegram_chat_id,
        IntentInterpreter(),
        ProposedCommandService(executor),
        NaturalTextProposalStore(state_directory / 'natural_text_proposal.json'),
    )
