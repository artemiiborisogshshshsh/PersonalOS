"""Per-user durable state for confirmed natural-text commands."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
import json
import os

from services.application.command import BaseCommand, CreateTaskCommand, CreateUniversityEventCommand
from services.application.proposed_command import ProposalStatus, ProposedCommand


class CompleteIntakeTaskCommand(BaseCommand):
    """Complete exactly the owned task snapshot shown for confirmation."""

    def __init__(self, task, command_id=None):
        super().__init__(command_id)
        self.task = dict(task)

    def get_command_type(self):
        return 'complete_intake_task'

    def validate(self):
        return bool(self.task.get('id') and self.task.get('title')
                    and self.task.get('status') in {'todo', 'in_progress', 'inbox'})


class PerUserNaturalCommandExecutor:
    """Persist supported commands locally; Calendar remains a later projection."""

    VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    async def process_command(self, command: Any) -> dict:
        state = self._load()
        previous = state['executed'].get(command.command_id)
        if previous is not None:
            return previous
        if isinstance(command, CreateTaskCommand):
            record = {
                'id': self._id('task', command.command_id), 'title': command.title,
                'description': command.description, 'status': command.status,
                'priority': command.priority,
            }
            state['tasks'].append(record)
            result = {'kind': 'task', 'id': record['id']}
        elif isinstance(command, CompleteIntakeTaskCommand):
            matches = [task for task in state['tasks'] if task['id'] == command.task['id']]
            if len(matches) != 1 or matches[0] != command.task or not command.validate():
                raise ValueError('Task changed since confirmation was proposed')
            matches[0]['status'] = 'done'
            result = {'kind': 'completed_task', 'id': matches[0]['id']}
        elif isinstance(command, CreateUniversityEventCommand) and not command.is_group_event:
            record = {
                'id': self._id('event', command.command_id), 'summary': command.summary,
                'description': command.description, 'location': command.location,
                'start': command.dtstart.isoformat(), 'end': command.dtend.isoformat(),
            }
            state['personal_events'].append(record)
            result = {'kind': 'personal_event', 'id': record['id']}
        else:
            raise ValueError('Unsupported natural-text command')
        state['executed'][command.command_id] = result
        self._save(state)
        return result

    def snapshot(self) -> dict:
        return self._load()

    @staticmethod
    def _id(kind: str, command_id: str) -> str:
        return f'{kind}-' + sha256(command_id.encode()).hexdigest()[:20]

    def _load(self) -> dict:
        if not self.path.exists():
            return {'version': self.VERSION, 'tasks': [], 'personal_events': [], 'executed': {}}
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if payload.get('version') != self.VERSION:
            raise ValueError('Unsupported natural command state version')
        return payload

    def _save(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                    prefix='.natural-', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                json.dump(payload, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


class NaturalTextProposalStore:
    """Persist one validated proposal without retaining the raw chat message."""

    VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    def save(self, proposal: ProposedCommand) -> None:
        command = proposal.command
        if isinstance(command, CreateTaskCommand):
            command_data = {
                'type': 'create_task', 'command_id': command.command_id,
                'title': command.title, 'description': command.description,
                'priority': command.priority, 'status': command.status,
            }
        elif isinstance(command, CompleteIntakeTaskCommand):
            command_data = {'type': 'complete_intake_task', 'command_id': command.command_id,
                            'task': command.task}
        elif isinstance(command, CreateUniversityEventCommand) and not command.is_group_event:
            command_data = {
                'type': 'create_personal_event', 'command_id': command.command_id,
                'summary': command.summary, 'description': command.description,
                'location': command.location, 'event_type': command.event_type,
                'start': command.dtstart.isoformat(), 'end': command.dtend.isoformat(),
            }
        else:
            raise ValueError('Unsupported natural-text command')
        self._write({'version': self.VERSION, 'proposal': {
            'id': proposal.id, 'confidence': proposal.confidence,
            'interpretation': proposal.interpretation, 'command': command_data,
        }})

    def load(self) -> ProposedCommand | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if payload.get('version') != self.VERSION:
            raise ValueError('Unsupported natural proposal version')
        item = payload.get('proposal')
        if not item:
            return None
        data = item['command']
        if data['type'] == 'create_task':
            command = CreateTaskCommand(
                data['title'], data['description'], priority=data['priority'],
                status=data['status'], command_id=data['command_id'],
            )
        elif data['type'] == 'complete_intake_task':
            command = CompleteIntakeTaskCommand(data['task'], command_id=data['command_id'])
            if not command.validate():
                raise ValueError('Invalid task completion proposal')
        elif data['type'] == 'create_personal_event':
            command = CreateUniversityEventCommand(
                data['event_type'], data['summary'], data['description'], data['location'],
                datetime.fromisoformat(data['start']), datetime.fromisoformat(data['end']),
                is_group_event=False, command_id=data['command_id'],
            )
        else:
            raise ValueError('Unsupported stored natural command')
        proposal = ProposedCommand(
            command, '', item['interpretation'], item['confidence'], id=item['id'],
        )
        proposal.status = ProposalStatus.VALIDATED
        return proposal

    def clear(self) -> None:
        self._write({'version': self.VERSION, 'proposal': None})

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                    prefix='.proposal-', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                json.dump(payload, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
