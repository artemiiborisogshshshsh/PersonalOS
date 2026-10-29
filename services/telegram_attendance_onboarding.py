"""Telegram inline-button flow for configuring attendance preferences."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List
from zoneinfo import ZoneInfo

from models import EventType, UniversityEvent
from services.attendance_preferences import (
    AttendancePreferenceStore,
    course_name,
    lab_slot_key,
)
from services.telegram_attendance_editor import TelegramAttendanceEditor


@dataclass
class TelegramAttendanceOnboarding:
    store: AttendancePreferenceStore
    _subjects: Dict[str, List[UniversityEvent]] = field(default_factory=dict)
    timezone: str = "Asia/Tomsk"
    _lab_selection: Dict[str, set[str]] = field(default_factory=dict)
    _editor: TelegramAttendanceEditor | None = field(default=None, init=False)

    def start(self, events: Iterable[UniversityEvent], *, edit: bool = False) -> dict:
        if edit:
            self._editor = TelegramAttendanceEditor(self.store, timezone=self.timezone)
            return self._editor.start(events)
        self._editor = None
        self._subjects = {}
        self._lab_selection = {}
        zone = ZoneInfo(self.timezone)

        def local(value):
            return value.astimezone(zone) if value.tzinfo else value.replace(tzinfo=zone)

        for event in events:
            event = replace(event, dtstart=local(event.dtstart), dtend=local(event.dtend))
            if event.is_group_event and event.event_type in {
                EventType.LECTURE, EventType.PRACTICAL, EventType.LAB,
            }:
                self._subjects.setdefault(course_name(event), []).append(event)
        for subject, subject_events in self._subjects.items():
            preference = self.store.subjects.get(subject)
            if preference and preference.labs_enabled:
                valid_slots = {lab_slot_key(event) for event in subject_events
                               if event.event_type == EventType.LAB}
                self._lab_selection[subject] = preference.lab_slots & valid_slots
        return self._next_question()

    def handle_callback(self, data: str, events: Iterable[UniversityEvent] | None = None) -> dict:
        if data.startswith('ate:'):
            if self._editor is None:
                return TelegramAttendanceEditor._expired()
            return self._editor.handle_callback(data, events=events)
        if self._editor is not None:
            return TelegramAttendanceEditor._expired()
        if data == 'att:reset':
            self.store.subjects.clear()
            self._lab_selection.clear()
            self.store.save()
            return self._next_question()
        parts = data.split(':')
        _, subject_index, action, *value = parts
        subject = sorted(self._subjects)[int(subject_index)]
        if action in {'lecture', 'practical'}:
            self.store.set_choice(subject, action, value[0] == 'yes')
        elif action == 'lab':
            if value[0] == 'toggle':
                slot = ':'.join(value[1:]).replace('~', ':')
                selection = self._lab_selection.setdefault(subject, set())
                if slot in selection:
                    selection.remove(slot)
                else:
                    selection.add(slot)
                return self._next_question()
            if value[0] == 'save':
                self.store.set_lab_slots(subject, self._lab_selection.get(subject, set()))
            else:
                # Keep accepting old single-select callbacks while old Telegram
                # messages may still be on screen.
                choice = False if value[0] == 'none' else value[0].replace('~', ':')
                self.store.set_choice(subject, 'lab', choice)
        else:
            raise ValueError('Unknown attendance callback')
        self.store.save()
        return self._next_question()

    def _next_question(self) -> dict:
        for index, subject in enumerate(sorted(self._subjects)):
            events = self._subjects[subject]
            preference = self.store.subjects.get(subject)
            if preference and preference.lectures is False:
                continue
            for event_type, action, label in (
                (EventType.LECTURE, 'lecture', 'лекции'),
                (EventType.PRACTICAL, 'practical', 'практики'),
            ):
                if any(event.event_type == event_type for event in events):
                    current = getattr(preference, f'{action}s', None) if preference else None
                    if current is None:
                        selected = '✅ ' if current is True else ''
                        not_selected = '✅ ' if current is False else ''
                        return {
                            'text': f'{self._subject_label(subject)}: посещаешь {label}?',
                            'buttons': [[
                                {'text': selected + 'Посещаю', 'callback_data': f'att:{index}:{action}:yes'},
                                {
                                    'text': not_selected + (
                                        'Не посещаю предмет'
                                        if action == 'lecture' else 'Не посещаю'
                                    ),
                                    'callback_data': f'att:{index}:{action}:no',
                                },
                            ]],
                        }
            labs = [event for event in events if event.event_type == EventType.LAB]
            if labs and not (preference and preference.labs_enabled is not None):
                slots = {lab_slot_key(event): event for event in labs}
                selected_slots = self._lab_selection.setdefault(subject, set())
                return {
                    'text': (f'{self._subject_label(subject)}: выбери все посещаемые лабораторные. '
                             'Можно отметить несколько слотов:'),
                    'buttons': [[{
                        'text': ('✅ ' if key in selected_slots else '') + self._slot_label(event),
                        'callback_data': f'att:{index}:lab:toggle:{key.replace(':', '~')}',
                    }] for key, event in sorted(slots.items())] + [[{
                        'text': 'Сохранить выбор' if selected_slots else 'Сохранить: не посещаю ЛБ',
                        'callback_data': f'att:{index}:lab:save',
                    }]],
                }
        buttons = [[{
            'text': 'Изменить ответы',
            'callback_data': 'att:reset',
        }, {
            'text': 'Показать события для Calendar',
            'callback_data': 'cal:preview',
        }], [{
            'text': 'Отправить в Google Calendar',
            'callback_data': 'cal:apply',
        }, {
            'text': 'Отменить',
            'callback_data': 'cal:cancel',
        }]]
        return {
            'text': self._preview_text(),
            'attendance_complete': True,
            'buttons': buttons,
        }

    def _preview_text(self) -> str:
        lines = ['Настройка посещаемости завершена.', '', 'Твой план:']
        for subject in sorted(self._subjects):
            preference = self.store.subjects.get(subject)
            if preference is None:
                continue
            if preference.lectures is False:
                lines.append(f'• {self._subject_label(subject)}: не посещаю предмет')
                continue
            parts = []
            if preference.lectures is not None:
                parts.append('ЛК — да' if preference.lectures else 'ЛК — нет')
            if preference.practicals is not None:
                parts.append('ПР — да' if preference.practicals else 'ПР — нет')
            if preference.labs_enabled is False:
                parts.append('ЛБ — нет')
            elif preference.labs_enabled:
                labels = {
                    lab_slot_key(event): self._slot_label(event)
                    for event in self._subjects[subject]
                    if event.event_type == EventType.LAB
                }
                slots = ', '.join(
                    labels.get(slot, slot) for slot in sorted(preference.lab_slots)
                )
                parts.append(f'ЛБ — {slots}' if slots else 'ЛБ — не посещаю')
            lines.append(f'• {self._subject_label(subject)}: ' + '; '.join(parts))
        lines.extend([
            '',
            'После подтверждения этот план будет синхронизирован с Google Calendar.',
        ])
        return '\n'.join(lines)

    @staticmethod
    def _subject_label(subject: str) -> str:
        return subject.replace('\\"', '"')

    def _slot_label(self, event: UniversityEvent) -> str:
        weekdays = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')
        start = event.dtstart
        if start.tzinfo is not None:
            start = start.astimezone(ZoneInfo(self.timezone))
        return f'{weekdays[start.weekday()]} {start:%H:%M}'
