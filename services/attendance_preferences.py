"""Persistent, user-controlled attendance choices for university events."""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, Iterable, Optional, Set
import json
import fcntl
import os
import re

from models import EventType, UniversityEvent


class AttendancePreferenceConflict(ValueError):
    """The subject was edited elsewhere after this draft was opened."""


def course_name(event: UniversityEvent | str) -> str:
    """Return the displayed course name without the trailing session marker."""
    return re.sub(r'\s*\([^)]*\)\s*$', '', event if isinstance(event, str) else event.summary).strip()


def lab_slot_key(event: UniversityEvent) -> str:
    """Stable weekly lab choice key, for example ``wed 10:15``."""
    return event.attendance_slot_key or f'{event.dtstart.weekday()}:{event.dtstart:%H:%M}'


@dataclass
class SubjectAttendancePreference:
    lectures: Optional[bool] = None
    practicals: Optional[bool] = None
    labs_enabled: Optional[bool] = None
    lab_slots: Set[str] = field(default_factory=set)
    excluded: bool = False
    # Auto-filled onboarding defaults apply only to source UIDs present when
    # the defaults were created. A new replacement UID still needs a decision.
    defaulted_lecture_uids: Set[str] = field(default_factory=set)
    defaulted_practical_uids: Set[str] = field(default_factory=set)
    defaulted_lab_uids: Set[str] = field(default_factory=set)

    def attends(self, event: UniversityEvent) -> Optional[bool]:
        if self.excluded:
            return False
        if event.event_type == EventType.LECTURE:
            if self.defaulted_lecture_uids and event.uid not in self.defaulted_lecture_uids:
                return None
            return self.lectures
        if event.event_type == EventType.PRACTICAL:
            if self.defaulted_practical_uids and event.uid not in self.defaulted_practical_uids:
                return None
            return self.practicals
        if event.event_type == EventType.LAB:
            if self.defaulted_lab_uids and event.uid not in self.defaulted_lab_uids:
                return None
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
                excluded=value.get('excluded') is True,
                defaulted_lecture_uids=set(value.get('defaulted_lecture_uids', [])),
                defaulted_practical_uids=set(value.get('defaulted_practical_uids', [])),
                defaulted_lab_uids=set(value.get('defaulted_lab_uids', [])),
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
                **({'excluded': True} if value.excluded else {}),
                **({'defaulted_lecture_uids': sorted(value.defaulted_lecture_uids)}
                   if value.defaulted_lecture_uids else {}),
                **({'defaulted_practical_uids': sorted(value.defaulted_practical_uids)}
                   if value.defaulted_practical_uids else {}),
                **({'defaulted_lab_uids': sorted(value.defaulted_lab_uids)}
                   if value.defaulted_lab_uids else {}),
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
                **({'excluded': True} if preference.excluded else {}),
                **({'defaulted_lecture_uids': sorted(preference.defaulted_lecture_uids)}
                   if preference.defaulted_lecture_uids else {}),
                **({'defaulted_practical_uids': sorted(preference.defaulted_practical_uids)}
                   if preference.defaulted_practical_uids else {}),
                **({'defaulted_lab_uids': sorted(preference.defaulted_lab_uids)}
                   if preference.defaulted_lab_uids else {}),
            }
            for subject, preference in self.subjects.items()
        }

    def set_choice(
        self, subject: str, session: str, value: bool | str,
    ) -> None:
        preference = self.subjects.setdefault(subject, SubjectAttendancePreference())
        if value is True or isinstance(value, str):
            preference.excluded = False
        if session == 'lecture':
            preference.lectures = bool(value)
            preference.defaulted_lecture_uids.clear()
            if value is False:
                # A lecture opt-out means the user does not attend this course
                # at all, so downstream preparation cannot be generated for
                # its practicals or laboratories either.
                preference.practicals = False
                preference.labs_enabled = False
                preference.lab_slots.clear()
                preference.defaulted_practical_uids.clear()
                preference.defaulted_lab_uids.clear()
        elif session == 'practical':
            preference.practicals = bool(value)
            preference.defaulted_practical_uids.clear()
        elif session == 'lab':
            preference.defaulted_lab_uids.clear()
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

    def set_lab_slots(self, subject: str, slots: Iterable[str]) -> None:
        """Replace the attended lab slots for a subject, including with none."""
        preference = self.subjects.setdefault(subject, SubjectAttendancePreference())
        preference.excluded = False
        preference.lab_slots = set(slots)
        preference.labs_enabled = bool(preference.lab_slots)
        preference.defaulted_lab_uids.clear()

    def save_subject_choice(
        self, subject: str, preference: SubjectAttendancePreference,
        *, expected: Optional[SubjectAttendancePreference],
    ) -> None:
        """Commit an independent subject draft without overwriting other courses.

        ``expected`` is the on-disk value captured when opening the subject.
        File replacement is atomic; a changed subject requires reopening its draft.
        The legacy ``set_choice`` lecture opt-out contract remains unchanged.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with (self.path.parent / f'.{self.path.name}.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            latest = self.load(self.path)
            if latest.subjects.get(subject) != expected:
                raise AttendancePreferenceConflict(subject)
            choice = deepcopy(preference)
            if not choice.lab_slots and choice.labs_enabled is not None:
                choice.labs_enabled = False
            latest.subjects[subject] = choice
            latest.save()
            self.subjects = latest.subjects

    def exclude_subject(self, subject: str) -> None:
        self.subjects.setdefault(subject, SubjectAttendancePreference()).excluded = True

    def subject_is_excluded(self, title: str) -> bool:
        preference = self.subjects.get(course_name(title))
        return bool(preference and preference.excluded)

    def undecided_subjects(
        self, events: Iterable[UniversityEvent],
    ) -> list[str]:
        return sorted({
            course_name(event) for event in events
            if self.preference_for(event) is None
        })
