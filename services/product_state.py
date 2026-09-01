"""Portable per-user configuration and schedule-source registry."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, Optional
from urllib.parse import urlparse
import json
import os


@dataclass(frozen=True)
class ScheduleSource:
    id: str
    kind: str  # ics_url, local_file or provider_api
    location: str
    display_name: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.kind not in {'ics_url', 'local_file', 'provider_api'}:
            raise ValueError('Unsupported schedule source kind')
        if self.kind in {'ics_url', 'provider_api'}:
            parsed = urlparse(self.location)
            if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
                raise ValueError('Remote schedule sources require an HTTP(S) URL')


@dataclass
class UserProductState:
    user_id: str
    timezone: str = 'Asia/Tomsk'
    sources: Dict[str, ScheduleSource] = field(default_factory=dict)
    active_source_id: Optional[str] = None
    secret_references: Dict[str, str] = field(default_factory=dict)

    def activate_source(self, source_id: str) -> None:
        if source_id not in self.sources:
            raise KeyError(f'Unknown schedule source: {source_id}')
        if not self.sources[source_id].enabled:
            raise ValueError('Cannot activate a disabled schedule source')
        self.active_source_id = source_id


class UserProductStateStore:
    """JSON persistence with no machine-specific paths or raw credentials."""

    def __init__(self, path: Path):
        self.path = path

    def load(self, user_id: str) -> UserProductState:
        if not self.path.exists():
            return UserProductState(user_id=user_id)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if data['user_id'] != user_id:
            raise ValueError('State belongs to a different user')
        return UserProductState(
            user_id=user_id,
            timezone=data.get('timezone', 'Asia/Tomsk'),
            sources={
                item['id']: ScheduleSource(**item)
                for item in data.get('sources', [])
            },
            active_source_id=data.get('active_source_id'),
            secret_references=data.get('secret_references', {}),
        )

    def save(self, state: UserProductState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'user_id': state.user_id,
            'timezone': state.timezone,
            'sources': [asdict(source) for source in state.sources.values()],
            'active_source_id': state.active_source_id,
            'secret_references': state.secret_references,
        }
        temporary_path = None
        try:
            with NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.path.parent,
                delete=False, prefix=f'.{self.path.name}.', suffix='.tmp',
            ) as output:
                json.dump(payload, output, ensure_ascii=False, indent=2)
                temporary_path = Path(output.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
