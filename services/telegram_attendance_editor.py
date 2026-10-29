"""Per-subject attendance drafts, saved only after explicit confirmation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from secrets import token_hex
from typing import Iterable
from zoneinfo import ZoneInfo

from models import EventType, UniversityEvent
from services.attendance_preferences import (
    AttendancePreferenceConflict,
    AttendancePreferenceStore,
    SubjectAttendancePreference,
    course_name,
    lab_slot_key,
)


@dataclass
class TelegramAttendanceEditor:
    store: AttendancePreferenceStore
    timezone: str = 'Asia/Tomsk'
    _subjects: dict[str, list[UniversityEvent]] = field(default_factory=dict, init=False)
    _source: tuple = field(default=(), init=False)
    _token: str = field(default='', init=False)
    _revision: int = field(default=0, init=False)
    _page: int = field(default=0, init=False)
    _active: str | None = field(default=None, init=False)
    _base: SubjectAttendancePreference | None = field(default=None, init=False)
    _draft: SubjectAttendancePreference | None = field(default=None, init=False)
    _PAGE_SIZE = 8

    def _eligible(self, events: Iterable[UniversityEvent]) -> list[UniversityEvent]:
        # Match planner inputs: weekly slot keys refer to the user's local day
        # and time, even when the source itself stores timestamps in UTC.
        zone = ZoneInfo(self.timezone)

        def local(value):
            return value.astimezone(zone) if value.tzinfo else value.replace(tzinfo=zone)

        return [replace(event, dtstart=local(event.dtstart), dtend=local(event.dtend))
                for event in events if event.is_group_event and event.event_type in {
                    EventType.LECTURE, EventType.PRACTICAL, EventType.LAB,
                }]

    @staticmethod
    def _snapshot(events: Iterable[UniversityEvent]) -> tuple:
        return tuple(sorted((
            event.uid, event.event_type.value, course_name(event), lab_slot_key(event),
            event.dtstart.isoformat(), event.dtend.isoformat(), event.location or '',
        ) for event in events))

    def start(self, events: Iterable[UniversityEvent]) -> dict:
        source = self._eligible(events)
        self._source = self._snapshot(source)
        self._subjects = {}
        for event in source:
            self._subjects.setdefault(course_name(event), []).append(event)
        self.store.subjects = self.store.load(self.store.path).subjects
        self._token = token_hex(6)
        self._revision = 0
        self._page = 0
        self._active = None
        self._draft = None
        self._base = None
        return self._menu()

    @staticmethod
    def _expired(text: str = 'Эти кнопки устарели. Открой посещаемость заново.') -> dict:
        return {'text': text, 'buttons': [[{
            'text': 'Открыть посещаемость', 'callback_data': 'manage:attendance',
        }]]}

    def handle_callback(self, data: str, events: Iterable[UniversityEvent] | None = None) -> dict:
        if not isinstance(data, str) or len(data.encode('utf-8')) > 64 or not self._token:
            return self._expired()
        parts = data.split(':')
        if (len(parts) not in {4, 5} or parts[0] != 'ate'
                or parts[1] != self._token or parts[2] != str(self._revision)):
            return self._expired()
        if events is not None and self._snapshot(self._eligible(events)) != self._source:
            self._token = ''
            self._active = None
            self._draft = None
            return self._expired('Расписание изменилось. Открой посещаемость заново, чтобы выбрать актуальные занятия.')
        action = parts[3]
        index = None
        if len(parts) == 5:
            if not parts[4].isascii() or not parts[4].isdigit():
                return self._expired()
            index = int(parts[4])
        if action == 'open' and index is not None and self._active is None:
            names = sorted(self._subjects)
            if index >= len(names):
                return self._expired()
            self.store.subjects = self.store.load(self.store.path).subjects
            self._active = names[index]
            self._base = deepcopy(self.store.subjects.get(self._active))
            self._draft = deepcopy(self._base) if self._base else SubjectAttendancePreference()
            types = {event.event_type for event in self._subjects[self._active]}
            for kind, attribute, defaults in (
                (EventType.LECTURE, 'lectures', 'defaulted_lecture_uids'),
                (EventType.PRACTICAL, 'practicals', 'defaulted_practical_uids'),
            ):
                if kind in types:
                    setattr(self._draft, attribute,
                            getattr(self._draft, attribute) is True and not self._draft.excluded)
                    getattr(self._draft, defaults).clear()
            if EventType.LAB in types:
                valid = {key for key, _ in self._slots(self._active)}
                self._draft.lab_slots = (self._draft.lab_slots & valid
                                         if self._draft.labs_enabled and not self._draft.excluded else set())
                self._draft.labs_enabled = bool(self._draft.lab_slots)
                self._draft.defaulted_lab_uids.clear()
            return self._subject()
        if action == 'page' and index is not None and self._active is None:
            if index * self._PAGE_SIZE >= len(self._subjects):
                return self._expired()
            self._page = index
            return self._menu()
        if self._active is None or self._draft is None:
            return self._expired()
        draft = self._draft
        types = {event.event_type for event in self._subjects[self._active]}
        if action in {'lecture', 'practical'} and index is None:
            event_type = EventType.LECTURE if action == 'lecture' else EventType.PRACTICAL
            if event_type not in types:
                return self._expired()
            attribute = 'lectures' if action == 'lecture' else 'practicals'
            value = not (getattr(draft, attribute) is True and not draft.excluded)
            setattr(draft, attribute, value)
            getattr(draft, f'defaulted_{action}_uids').clear()
            if value:
                draft.excluded = False
        elif action in {'lab', 'all', 'none'}:
            slots = self._slots(self._active)
            if not slots or (action == 'lab' and (index is None or index >= len(slots))):
                return self._expired()
            if action != 'lab' and index is not None:
                return self._expired()
            selection = set() if draft.excluded else draft.lab_slots.copy()
            if action == 'lab':
                key = slots[index][0]
                selection.symmetric_difference_update({key})
            elif action == 'all':
                selection = {key for key, _ in slots}
            else:
                selection.clear()
            draft.lab_slots = selection
            draft.labs_enabled = bool(selection)
            draft.defaulted_lab_uids.clear()
            if selection:
                draft.excluded = False
        elif action == 'back' and index is None:
            self._active = None
            self._draft = None
            return self._menu()
        elif action == 'save' and index is None:
            subject = self._active
            try:
                self.store.save_subject_choice(subject, draft, expected=self._base)
            except AttendancePreferenceConflict:
                self._active = None
                self._draft = None
                self.store.subjects = self.store.load(self.store.path).subjects
                return self._menu('Посещаемость этого предмета изменена в другом окне. Открой предмет и проверь актуальные ответы.')
            summary = self._summary(subject, self.store.subjects[subject])
            self._active = None
            self._draft = None
            reply = self._menu(
                f'Сохранено. {self._label(subject)}: {summary}.\n'
                'Для Calendar открой /weekly_preview и подтверди план.'
            )
            reply['attendance_saved'] = True
            return reply
        else:
            return self._expired()
        return self._subject()

    def _button(self, text: str, action: str) -> dict:
        return {'text': text, 'callback_data': f'ate:{self._token}:{self._revision}:{action}'}

    @staticmethod
    def _label(subject: str) -> str:
        return subject.replace('\\"', '"')

    def _summary(self, subject: str, preference: SubjectAttendancePreference | None) -> str:
        if preference is None:
            return 'не настроено'
        if preference.excluded:
            return 'предмет исключён'
        types = {event.event_type for event in self._subjects[subject]}
        parts = []
        for kind, attribute, label in (
            (EventType.LECTURE, 'lectures', 'ЛК'),
            (EventType.PRACTICAL, 'practicals', 'ПР'),
        ):
            if kind in types:
                value = getattr(preference, attribute)
                parts.append(f'{label} — ' + ('да' if value is True else 'нет' if value is False else '?'))
        if EventType.LAB in types:
            selected = len(preference.lab_slots & {key for key, _ in self._slots(subject)})
            parts.append('ЛБ — ' + ('?' if preference.labs_enabled is None else
                                   str(selected) if preference.labs_enabled else 'нет'))
        return '; '.join(parts)

    def _menu(self, notice: str = '') -> dict:
        self._revision += 1
        names = sorted(self._subjects)
        if not names:
            return {'text': 'В расписании пока нет предметов для настройки посещаемости. Сначала обнови расписание.',
                    'buttons': [[{'text': '← В центр управления', 'callback_data': 'manage:home'}]]}
        first = self._page * self._PAGE_SIZE
        buttons = [[self._button(
            f'{self._label(subject)} · {self._summary(subject, self.store.subjects.get(subject))}',
            f'open:{index}',
        )] for index, subject in enumerate(names[first:first + self._PAGE_SIZE], first)]
        pages = []
        if self._page:
            pages.append(self._button('←', f'page:{self._page - 1}'))
        if first + self._PAGE_SIZE < len(names):
            pages.append(self._button('→', f'page:{self._page + 1}'))
        if pages:
            buttons.append(pages)
        buttons.append([{'text': '← В центр управления', 'callback_data': 'manage:home'}])
        return {'text': (notice + '\n\n' if notice else '') + 'Посещаемость: выбери предмет. Изменения сохраняются кнопкой «Сохранить».',
                'buttons': buttons}

    def _slots(self, subject: str) -> list[tuple[str, str]]:
        labels: dict[str, set[str]] = {}
        order = {}
        weekdays = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')
        for event in self._subjects[subject]:
            if event.event_type != EventType.LAB:
                continue
            start = event.dtstart
            if start.tzinfo is not None:
                start = start.astimezone(ZoneInfo(self.timezone))
            key = lab_slot_key(event)
            label = f'{weekdays[start.weekday()]} {start:%H:%M}'
            if event.location:
                label += f' · {event.location}'
            labels.setdefault(key, set()).add(label)
            order[key] = min(order.get(key, (7, 24, 60)), (start.weekday(), start.hour, start.minute))
        return [(key, ' / '.join(sorted(labels[key]))) for key in sorted(labels, key=lambda key: (order[key], key))]

    def _subject(self) -> dict:
        self._revision += 1
        subject, draft = self._active, self._draft
        types = {event.event_type for event in self._subjects[subject]}
        buttons = []
        for kind, attribute, action, label in (
            (EventType.LECTURE, 'lectures', 'lecture', 'Лекции'),
            (EventType.PRACTICAL, 'practicals', 'practical', 'Практики'),
        ):
            if kind in types:
                selected = getattr(draft, attribute) is True and not draft.excluded
                buttons.append([self._button(('☑ ' if selected else '☐ ') + label, action)])
        slots = self._slots(subject)
        for index, (key, label) in enumerate(slots):
            selected = draft.labs_enabled and key in draft.lab_slots and not draft.excluded
            buttons.append([self._button(('☑ ' if selected else '☐ ') + label, f'lab:{index}')])
        if slots:
            buttons.append([self._button('Все', 'all'), self._button('Ни одной', 'none')])
        buttons.append([self._button('Сохранить', 'save'), self._button('Назад', 'back')])
        text = f'{self._label(subject)}\nОтметь посещаемые занятия. Выбор независим для лекций, практик и лабораторных.'
        if draft.excluded:
            text += '\nПредмет исключён. Отметь нужное занятие, чтобы снова включить его.'
        return {'text': text, 'buttons': buttons}
