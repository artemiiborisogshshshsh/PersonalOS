"""Portable, persisted wall-clock schedule for autonomous maintenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable, List
import json
import os


DEFAULT_CHECK_HOURS = (5, 12, 15, 18, 22)


@dataclass
class RuntimeScheduleState:
    completed_runs: dict[str, str] = field(default_factory=dict)


class RuntimeSchedule:
    """Returns each configured maintenance run once per local calendar day."""

    def __init__(self, path: Path, hours: Iterable[int] = DEFAULT_CHECK_HOURS):
        self.path = path
        self.set_hours(hours)
        self.state = self._load()

    def set_hours(self, hours: Iterable[int]) -> None:
        values = tuple(hours)
        if not all(type(hour) is int and 0 <= hour <= 23 for hour in values):
            raise ValueError('hours must contain whole hours from 0 through 23')
        self.hours = tuple(sorted(set(values)))

    def due_hours(self, now: datetime) -> List[int]:
        date_key = now.date().isoformat()
        # Never replay every missed job after a bot restart. A missed 05:00
        # report at noon must not block Telegram polling or send stale output.
        # The next configured wall-clock run safely resumes maintenance.
        if now.hour not in self.hours:
            return []
        return [] if self.state.completed_runs.get(str(now.hour)) == date_key else [now.hour]

    def mark_completed(self, hour: int, now: datetime) -> None:
        self.state.completed_runs[str(hour)] = now.date().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.path.parent,
                delete=False, prefix=f'.{self.path.name}.', suffix='.tmp',
            ) as output:
                json.dump({'completed_runs': self.state.completed_runs}, output)
                temporary_path = Path(output.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()

    def _load(self) -> RuntimeScheduleState:
        if not self.path.exists():
            return RuntimeScheduleState()
        return RuntimeScheduleState(**json.loads(self.path.read_text(encoding='utf-8')))
