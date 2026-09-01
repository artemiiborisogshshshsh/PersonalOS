"""Persistent, user-controlled attendance choices for university events."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, Iterable, Optional, Set
import json
import os
import re

from models import EventType, UniversityEvent


def course_name(event: UniversityEvent) -> str:
    """Return the displayed course name without the trailing session marker."""
    return re.sub(r'\s*\([^)]*\)\s*$', '', event.summary).strip()


def lab_slot_key(event: UniversityEvent) -> str:
    """Stable weekly lab choice key, for example ``wed 10:15``."""
    return f'{event.dtstart.weekday()}:{event.dtstart:%H:%M}'


@dataclass
class SubjectAttendancePreference:
    lectures: Optional[bool] = None
    practicals: Optional[bool] = None
    labs_enabled: Optional[bool] = None
    lab_slots: Set[str] = field(default_factory=set)

    def attends(self, event: UniversityEvent) -> Optional[bool]:
        if event.event_type == EventType.LECTURE:
            return self.lectures
        if event.event_type == EventType.PRACTICAL:
            return self.practicals
        if event.event_type == EventType.LAB:
            if self.labs_enabled is False:
                return False
            if self.labs_enabled is None:
                return None
            return lab_slot_key(event) in self.lab_slots
        return None


@dataclass
class AttendancePreferenceStore:
    path: Path
    subjects: Dict[str, SubjectAttendancePreference] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> 'AttendancePreferenceStore':
        if not path.exists():
            return cls(path)
        data = json.loads(path.read_text(encoding='utf-8'))
        subjects = {
            name: SubjectAttendancePreference(
                lectures=value.get('lectures'),
                practicals=value.get('practicals'),
                labs_enabled=value.get('labs_enabled'),
                lab_slots=set(value.get('lab_slots', [])),
            )
            for name, value in data.get('subjects', {}).items()
        }
        return cls(path, subjects)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'version': 1, 'subjects': {
            name: {
                'lectures': value.lectures,
                'practicals': value.practicals,
                'labs_enabled': value.labs_enabled,
                'lab_slots': sorted(value.lab_slots),
            }
            for name, value in sorted(self.subjects.items())
        }}
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
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()

    def preference_for(self, event: UniversityEvent) -> Optional[bool]:
        preference = self.subjects.get(course_name(event))
        return preference.attends(event) if preference else None

    def as_rule_metadata(self) -> Dict[str, dict]:
        return {
            subject: {
                'lectures': preference.lectures,
                'practicals': preference.practicals,
                'labs_enabled': preference.labs_enabled,
                'lab_slots': sorted(preference.lab_slots),
            }
            for subject, preference in self.subjects.items()
        }

    def set_choice(
        self, subject: str, session: str, value: bool | str,
    ) -> None:
        preference = self.subjects.setdefault(subject, SubjectAttendancePreference())
        if session == 'lecture':
            preference.lectures = bool(value)
            if value is False:
                # A lecture opt-out means the user does not attend this course
                # at all, so downstream preparation cannot be generated for
                # its practicals or laboratories either.
                preference.practicals = False
                preference.labs_enabled = False
                preference.lab_slots.clear()
        elif session == 'practical':
            preference.practicals = bool(value)
        elif session == 'lab':
            if value is False:
                preference.labs_enabled = False
                preference.lab_slots.clear()
            elif not isinstance(value, str):
                raise ValueError('Lab selection requires a weekday/time slot')
            else:
                preference.labs_enabled = True
                preference.lab_slots.add(value)
        else:
            raise ValueError(f'Unsupported session type: {session}')

    def undecided_subjects(
        self, events: Iterable[UniversityEvent],
    ) -> list[str]:
        return sorted({
            course_name(event) for event in events
            if self.preference_for(event) is None
        })
