"""Marker-scoped Obsidian projection that never rewrites user-owned content."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import re


class ObsidianProjectionService:
    def _value(self, item: Any, name: str, default: Any = '') -> Any:
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

    def _marker_key(self, course: str) -> str:
        key = re.sub(r'[^\w-]+', '-', course.casefold(), flags=re.UNICODE).strip('-')
        return key or 'course'

    def render_course_projection(
        self,
        course: str,
        events: Iterable[Any],
        tasks: Iterable[Any],
        preparation_blocks: Iterable[Any],
    ) -> str:
        key = self._marker_key(course)
        lines = [
            f"<!-- PERSONAL_OS:COURSE:{key}:START -->",
            "## Personal OS — автоматические связи",
            f"- **Course**: {course}",
            "- **Events**:",
        ]
        for event in events:
            event_id = self._value(
                event,
                'uid',
                self._value(event, 'university_event_uid', self._value(event, 'id')),
            )
            title = self._value(event, 'summary', self._value(event, 'title'))
            start = self._value(event, 'dtstart', self._value(event, 'start_time'))
            lines.append(f"  - `{event_id}` — {title} — {start}")
        lines.append("- **Tasks**:")
        for task in tasks:
            lines.append(
                f"  - `{self._value(task, 'id')}` — {self._value(task, 'title')}"
            )
        lines.append("- **PreparationBlocks**:")
        for block in preparation_blocks:
            block_id = self._value(block, 'uid', self._value(block, 'id'))
            source_id = self._value(
                block, 'source_event_uid', self._value(block, 'source_session_id')
            )
            title = self._value(block, 'summary', self._value(block, 'title'))
            lines.append(f"  - `{block_id}` → `{source_id}` — {title}")
        lines.append(f"<!-- PERSONAL_OS:COURSE:{key}:END -->")
        return "\n".join(lines)

    def project_course(
        self,
        note_path: Path,
        course: str,
        events: Iterable[Any],
        tasks: Iterable[Any],
        preparation_blocks: Iterable[Any],
    ) -> Path:
        note_path = Path(note_path)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        original = note_path.read_text(encoding='utf-8') if note_path.exists() else ''
        projection = self.render_course_projection(
            course, events, tasks, preparation_blocks
        )
        key = re.escape(self._marker_key(course))
        pattern = re.compile(
            rf'<!-- PERSONAL_OS:COURSE:{key}:START -->.*?'
            rf'<!-- PERSONAL_OS:COURSE:{key}:END -->',
            flags=re.DOTALL,
        )
        if pattern.search(original):
            updated = pattern.sub(lambda _: projection, original, count=1)
        else:
            separator = '' if not original else ('\n' if original.endswith('\n') else '\n\n')
            updated = f"{original}{separator}{projection}\n"

        temporary = note_path.with_suffix(note_path.suffix + '.personal-os.tmp')
        temporary.write_text(updated, encoding='utf-8')
        temporary.replace(note_path)
        return note_path
