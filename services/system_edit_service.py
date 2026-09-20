"""Safe user overrides for events projected by Personal OS.

The upstream schedules are read-only.  A Telegram "delete" therefore means
"hide this Personal OS projection" (and any related preparation), never
changing TPU, AlfaCRM, or a calendar event created by the user.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Optional
import json
import os


@dataclass
class SystemEditRecord:
    code: str
    kind: str
    source_id: str
    title: str
    start: str
    scope: str = ''
    hidden: bool = False


@dataclass
class SystemEditState:
    hidden_university_event_ids: list[str] = field(default_factory=list)
    hidden_work_lesson_ids: list[str] = field(default_factory=list)
    suppressed_university_preparation_sources: list[str] = field(default_factory=list)
    suppressed_work_preparation_sources: list[str] = field(default_factory=list)
    # Keys are ``normalised course|session_type``. ``all`` disables every
    # preparation for the course; a concrete type disables only ЛК/ПР/ЛБ.
    disabled_preparation_rules: list[str] = field(default_factory=list)
    inventory: dict[str, dict[str, Any]] = field(default_factory=dict)


class SystemEditStore:
    """Portable per-user storage for intentional scheduling overrides."""

    VERSION = 1

    def __init__(self, path: Path):
        self.path = path
        self.state = self._load()

    def _load(self) -> SystemEditState:
        if not self.path.exists():
            return SystemEditState()
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if payload.get('version') != self.VERSION:
            raise ValueError('Unsupported system edit storage version')
        return SystemEditState(**payload.get('state', {}))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Optional[Path] = None
        try:
            with NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.path.parent, delete=False,
                prefix=f'.{self.path.name}.', suffix='.tmp',
            ) as output:
                json.dump({'version': self.VERSION, 'state': asdict(self.state)}, output,
                          ensure_ascii=False, indent=2)
                temporary = Path(output.name)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def university_hidden(self, event_id: str) -> bool:
        return event_id in self.state.hidden_university_event_ids

    def work_hidden(self, lesson_id: str) -> bool:
        return lesson_id in self.state.hidden_work_lesson_ids

    def preparation_suppressed(self, scope: str, source_id: str) -> bool:
        targets = (self.state.suppressed_work_preparation_sources
                   if scope == 'work' else self.state.suppressed_university_preparation_sources)
        return source_id in targets

    @staticmethod
    def normalise_course(course: str) -> str:
        return ' '.join(course.casefold().split())

    @staticmethod
    def normalise_session_type(session_type: str) -> str:
        value = session_type.casefold().strip()
        return {
            'лк': 'lecture', 'lecture': 'lecture',
            'пр': 'practical', 'practical': 'practical',
            'лб': 'lab', 'lab': 'lab',
            'все': 'all', 'all': 'all',
        }.get(value, value)

    def preparation_disabled(self, course: str, session_type: str) -> bool:
        course_key = self.normalise_course(course)
        type_key = self.normalise_session_type(session_type)
        return (
            f'{course_key}|all' in self.state.disabled_preparation_rules
            or f'{course_key}|{type_key}' in self.state.disabled_preparation_rules
        )

    def set_preparation_rule(self, course: str, session_type: str = 'all', disabled: bool = True) -> None:
        course_key = self.normalise_course(course)
        type_key = self.normalise_session_type(session_type)
        if not course_key or type_key not in {'all', 'lecture', 'practical', 'lab'}:
            raise ValueError('Укажи предмет и тип: ЛК, ПР, ЛБ или все.')
        key = f'{course_key}|{type_key}'
        if disabled and key not in self.state.disabled_preparation_rules:
            self.state.disabled_preparation_rules.append(key)
        if not disabled and key in self.state.disabled_preparation_rules:
            self.state.disabled_preparation_rules.remove(key)
        self.save()

    def preparation_rules(self) -> list[tuple[str, str]]:
        return [tuple(value.rsplit('|', 1)) for value in self.state.disabled_preparation_rules]

    def set_hidden(self, kind: str, source_id: str, hidden: bool) -> None:
        if kind == 'university':
            target = self.state.hidden_university_event_ids
        elif kind == 'work':
            target = self.state.hidden_work_lesson_ids
        elif kind == 'university-preparation':
            target = self.state.suppressed_university_preparation_sources
        elif kind == 'work-preparation':
            target = self.state.suppressed_work_preparation_sources
        else:
            raise ValueError('Unknown system event type')
        if hidden and source_id not in target:
            target.append(source_id)
        if not hidden and source_id in target:
            target.remove(source_id)
        for payload in self.state.inventory.values():
            if payload.get('kind') == kind and str(payload.get('source_id')) == source_id:
                payload['hidden'] = hidden
        self.save()

    def refresh_inventory(
        self,
        university_events: Iterable[Any],
        work_lessons: Iterable[Any],
        preparation_blocks: Iterable[tuple[str, Any]],
    ) -> list[SystemEditRecord]:
        """Create short, Telegram-friendly identifiers for the next events."""
        previous_inventory = dict(self.state.inventory)
        records: list[SystemEditRecord] = []
        for prefix, kind, entries in (
            ('u', 'university', university_events),
            ('w', 'work', work_lessons),
        ):
            for index, event in enumerate(sorted(entries, key=lambda item: item.start_time if kind == 'university' else item.start), 1):
                start = event.start_time if kind == 'university' else event.start
                source_id = str(event.id)
                title = str(event.title if kind == 'university' else event.display_name)
                hidden = self.university_hidden(source_id) if kind == 'university' else self.work_hidden(source_id)
                records.append(SystemEditRecord(f'{prefix}{index}', kind, source_id, title,
                                                 start.isoformat(), hidden=hidden))
        for index, (scope, block) in enumerate(
            sorted(preparation_blocks, key=lambda item: item[1].start), 1,
        ):
            kind = f'{scope}-preparation'
            records.append(SystemEditRecord(
                f'p{index}', kind, str(block.source_event_id), str(block.title),
                block.start.isoformat(), scope=scope,
                hidden=self.preparation_suppressed(scope, str(block.source_event_id)),
            ))
        # Once a preparation is suppressed it naturally disappears from the
        # scheduler output. Keep it in the edit list nevertheless, otherwise
        # a user who opens the list again would have no way to restore it.
        represented = {(record.kind, record.source_id) for record in records}
        hidden_preparations = [
            SystemEditRecord(**payload)
            for payload in previous_inventory.values()
            if str(payload.get('kind', '')).endswith('-preparation')
            and bool(payload.get('hidden'))
            and (str(payload.get('kind')), str(payload.get('source_id'))) not in represented
        ]
        for old in hidden_preparations:
            scope = old.scope or old.kind.removesuffix('-preparation')
            if self.preparation_suppressed(scope, old.source_id):
                records.append(SystemEditRecord(
                    f'p{len([item for item in records if item.code.startswith("p")]) + 1}',
                    old.kind, old.source_id, old.title, old.start,
                    scope=scope, hidden=True,
                ))
        self.state.inventory = {record.code: asdict(record) for record in records}
        self.save()
        return records

    def record(self, code: str) -> Optional[SystemEditRecord]:
        payload = self.state.inventory.get(code.casefold())
        return SystemEditRecord(**payload) if payload else None

    @staticmethod
    def render(records: Iterable[SystemEditRecord]) -> str:
        records = list(records)
        if not records:
            return 'На ближайшие 30 дней системных событий нет.'
        labels = {
            'university': 'ВУЗ', 'work': 'Работа',
            'university-preparation': 'Подготовка к ВУЗу',
            'work-preparation': 'Подготовка к работе',
        }
        lines = ['Системные события (ближайшие 30 дней):']
        for item in records:
            status = ' — скрыто' if item.hidden else ''
            start = datetime.fromisoformat(item.start)
            lines.append(f'• {item.code}: [{labels[item.kind]}] {start:%d.%m %H:%M} — {item.title}{status}')
        lines.extend([
            '', 'Удалить/скрыть: /system_delete КОД',
            'Вернуть: /system_restore КОД',
            'Например: /system_delete u1',
            'Источник TPU/AlfaCRM и личные Google-события никогда не меняются.',
        ])
        return '\n'.join(lines)

    def delete_owned_preparations(self, calendar_adapter: Any, source_id: str) -> int:
        """Delete only app-marked preparations attached to a selected source."""
        from services.calendar.projection_state import (
            CalendarProjectionError, delete_owned_verified,
        )
        if not hasattr(calendar_adapter, 'list_visible_calendars'):
            return 0
        now = datetime.now().astimezone()
        removed = 0
        for calendar in calendar_adapter.list_visible_calendars():
            calendar_id = calendar.get('id')
            if not calendar_id:
                continue
            for event in calendar_adapter.list_events_in_calendar(
                calendar_id, now - timedelta(days=180), now + timedelta(days=365),
            ):
                description = str(event.get('description') or '')
                private = event.get('extendedProperties', {}).get('private', {})
                event_source = str(private.get('personal_os_source_event_id') or '')
                if not event_source:
                    marker = 'AI Calendar Source: '
                    if marker in description:
                        event_source = description.split(marker, 1)[1].split('\n', 1)[0].strip()
                block_id = str(private.get('personal_os_block_id') or '')
                operation_id = str(private.get('personal_os_operation_id') or '')
                if (event_source == source_id and block_id and operation_id
                        and f'AI Calendar Block: {block_id}\n' in description
                        and event.get('id')):
                    try:
                        deleted = delete_owned_verified(
                            calendar_adapter, calendar_id, str(event['id']),
                            lambda value: value.get('extendedProperties', {})
                            .get('private', {}).get('personal_os_block_id') == block_id
                            and value.get('extendedProperties', {}).get('private', {})
                            .get('personal_os_operation_id') == operation_id,
                        )
                    except CalendarProjectionError:
                        deleted = False
                    if deleted:
                        removed += 1
        return removed
