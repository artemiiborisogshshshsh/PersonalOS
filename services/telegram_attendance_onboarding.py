"""Telegram inline-button flow for configuring attendance preferences."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from models import EventType, UniversityEvent
from services.attendance_preferences import (
    AttendancePreferenceStore,
    course_name,
    lab_slot_key,
)


@dataclass
class TelegramAttendanceOnboarding:
    store: AttendancePreferenceStore
    _subjects: Dict[str, List[UniversityEvent]] = field(default_factory=dict)

    def start(self, events: Iterable[UniversityEvent]) -> dict:
        self._subjects = {}
        for event in events:
            if event.is_group_event and event.event_type in {
                EventType.LECTURE, EventType.PRACTICAL, EventType.LAB,
            }:
                self._subjects.setdefault(course_name(event), []).append(event)
        return self._next_question()

    def handle_callback(self, data: str) -> dict:
        if data == 'att:reset':
            self.store.subjects.clear()
            self.store.save()
            return self._next_question()
        _, subject_index, action, *value = data.split(':')
        subject = sorted(self._subjects)[int(subject_index)]
        if action in {'lecture', 'practical'}:
            self.store.set_choice(subject, action, value[0] == 'yes')
        elif action == 'lab':
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
            for event_type, action, label in (
                (EventType.LECTURE, 'lecture', 'лекции'),
                (EventType.PRACTICAL, 'practical', 'практики'),
            ):
                if any(event.event_type == event_type for event in events):
                    current = getattr(preference, f'{action}s', None) if preference else None
                    if current is None:
                        return {
                            'text': f'{subject}: посещаешь {label}?',
                            'buttons': [[
                                {'text': 'Посещаю', 'callback_data': f'att:{index}:{action}:yes'},
                                {
                                    'text': (
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
                return {
                    'text': f'{subject}: выбери посещаемую лабораторную:',
                    'buttons': [[{
                        'text': self._slot_label(event),
                        'callback_data': f'att:{index}:lab:{key.replace(':', '~')}',
                    }] for key, event in sorted(slots.items())] + [[{
                        'text': 'Не посещаю ЛБ',
                        'callback_data': f'att:{index}:lab:none',
                    }]],
                }
        return {
            'text': self._preview_text(),
            'buttons': [[{
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
            }]],
        }

    def _preview_text(self) -> str:
        lines = ['Настройка посещаемости завершена.', '', 'Твой план:']
        for subject in sorted(self._subjects):
            preference = self.store.subjects.get(subject)
            if preference is None:
                continue
            if preference.lectures is False:
                lines.append(f'• {subject}: не посещаю предмет')
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
                parts.append(f'ЛБ — {slots}')
            lines.append(f'• {subject}: ' + '; '.join(parts))
        lines.extend([
            '',
            'После подтверждения этот план будет синхронизирован с Google Calendar.',
        ])
        return '\n'.join(lines)

    @staticmethod
    def _slot_label(event: UniversityEvent) -> str:
        weekdays = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')
        return f'{weekdays[event.dtstart.weekday()]} {event.dtstart:%H:%M}'
