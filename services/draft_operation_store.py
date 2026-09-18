"""Durable, portable storage for reversible preparation draft operations."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, Iterable
import json
import os

from services.adaptive_preparation_service import (
    CalendarRoute,
    DraftOperation,
    DraftPreparationBlock,
)


class DraftOperationStore:
    """Atomically persists system-owned draft snapshots, never user calendar data."""

    VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    def save(self, operation: DraftOperation) -> None:
        operations = self.load_all()
        operations[operation.id] = operation
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': self.VERSION,
            'operations': [self._serialize(item) for item in operations.values()],
        }
        temporary_path = None
        try:
            with NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.path.parent,
                delete=False, prefix=f'.{self.path.name}.', suffix='.tmp',
            ) as output:
                json.dump(payload, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
                temporary_path = Path(output.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def load(self, operation_id: str) -> DraftOperation:
        try:
            return self.load_all()[operation_id]
        except KeyError as error:
            raise KeyError(f'Unknown draft operation: {operation_id}') from error

    def load_all(self) -> Dict[str, DraftOperation]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if payload.get('version') != self.VERSION:
            raise ValueError('Unsupported draft operation storage version')
        return {
            item['id']: self._deserialize(item)
            for item in payload.get('operations', [])
        }

    @staticmethod
    def _serialize(operation: DraftOperation) -> dict:
        return {
            **asdict(operation),
            'blocks': [DraftOperationStore._serialize_block(item) for item in operation.blocks],
            'previous_blocks': [
                DraftOperationStore._serialize_block(item)
                for item in operation.previous_blocks
            ],
            'retained_blocks': [DraftOperationStore._serialize_block(item)
                                for item in operation.retained_blocks],
            'retired_blocks': [DraftOperationStore._serialize_block(item)
                               for item in operation.retired_blocks],
        }

    @staticmethod
    def _serialize_block(block: DraftPreparationBlock) -> dict:
        data = asdict(block)
        data['start'] = block.start.isoformat()
        data['end'] = block.end.isoformat()
        data['calendar'] = block.calendar.value
        return data

    @staticmethod
    def _deserialize(data: dict) -> DraftOperation:
        data = dict(data)
        data['blocks'] = [DraftOperationStore._deserialize_block(item) for item in data['blocks']]
        data['previous_blocks'] = [
            DraftOperationStore._deserialize_block(item)
            for item in data.get('previous_blocks', [])
        ]
        data['retained_blocks'] = [DraftOperationStore._deserialize_block(item)
                                   for item in data.get('retained_blocks', [])]
        data['retired_blocks'] = [DraftOperationStore._deserialize_block(item)
                                  for item in data.get('retired_blocks', [])]
        return DraftOperation(**data)

    @staticmethod
    def _deserialize_block(data: dict) -> DraftPreparationBlock:
        data = dict(data)
        data['start'] = datetime.fromisoformat(data['start'])
        data['end'] = datetime.fromisoformat(data['end'])
        data['calendar'] = CalendarRoute(data['calendar'])
        return DraftPreparationBlock(**data)
