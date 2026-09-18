"""Small privacy-minimised B2C lifecycle analytics store.

No schedule titles, messages, URLs, calendar IDs, feedback comments or raw
Telegram IDs are accepted as event properties. This is intentionally a funnel
counter, not behavioural surveillance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
import json
import os


LIFECYCLE_EVENTS = frozenset({
    'registered', 'source_connected', 'attendance_completed', 'first_plan',
    'weekly_active', 'referral_sent', 'payment_stage_reached',
})


@dataclass(frozen=True)
class LifecycleEvent:
    user_id: str
    name: str
    occurred_at: str


class ProductAnalyticsStore:
    VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    def record_once(self, user_id: str, name: str) -> bool:
        if name not in LIFECYCLE_EVENTS:
            raise ValueError('Unsupported lifecycle event')
        events = self._load()
        if any(item.user_id == user_id and item.name == name for item in events):
            return False
        self._save([*events, LifecycleEvent(user_id, name, datetime.now(timezone.utc).isoformat())])
        return True

    def funnel_counts(self) -> dict[str, int]:
        events = self._load()
        return {name: sum(item.name == name for item in events) for name in sorted(LIFECYCLE_EVENTS)}

    def _load(self) -> list[LifecycleEvent]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('version') != self.VERSION:
            raise ValueError('Unsupported analytics storage version')
        return [LifecycleEvent(**item) for item in data.get('events', [])]

    def _save(self, events: list[LifecycleEvent]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                    prefix='.analytics-', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                json.dump({'version': self.VERSION,
                           'events': [item.__dict__ for item in events]}, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
