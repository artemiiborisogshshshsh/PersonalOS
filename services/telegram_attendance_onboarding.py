"""Telegram inline-button flow for configuring attendance preferences."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from copy import deepcopy
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
    _pending_bulk: bool = field(default=False, init=False)
    _allowed_callbacks: set[str] = field(default_factory=set, init=False)
    _revision: int = field(default=0, init=False)
    _reset_snapshot: dict | None = field(default=None, init=False)

    def start(self, events: Iterable[UniversityEvent], *, edit: bool = False) -> dict:
        if edit:
            self._editor = TelegramAttendanceEditor(self.store, timezone=self.timezone)
            return self._editor.start(events)
        self._editor = None
        self._pending_bulk = False
        self._allowed_callbacks.clear()
        self._revision = 0
        self._reset_snapshot = None
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
        if data == 'att:reset' or (data in self._allowed_callbacks and data.endswith(':preview')):
            self._revision += 1
            latest = AttendancePreferenceStore.load(self.store.path)
            self._reset_snapshot = deepcopy(latest.subjects)
            self._allowed_callbacks = {
                f'att:reset:r{self._revision}:save', f'att:reset:r{self._revision}:cancel',
            }
            return {
                'text': ('Это удалит все сохранённые ответы посещаемости для твоих предметов. '
                         'Продолжить? Изменения произойдут только после подтверждения.'),
                'buttons': [[
                    {'text': 'Да, сбросить ответы', 'callback_data': f'att:reset:r{self._revision}:save'},
                    {'text': 'Отмена', 'callback_data': f'att:reset:r{self._revision}:cancel'},
                ]],
            }
        if data in self._allowed_callbacks and data.startswith('att:reset:r'):
            if data.endswith(':cancel'):
                self._reset_snapshot = None
                self._allowed_callbacks.clear()
                return self._next_question()
            latest = AttendancePreferenceStore.load(self.store.path)
            if latest.subjects != self._reset_snapshot:
                self.store = latest
                self._reset_snapshot = None
                self._allowed_callbacks.clear()
                return self._stale()
            self.store.subjects.clear()
            self._lab_selection.clear()
            self.store.save()
            self._reset_snapshot = None
            self._allowed_callbacks.clear()
            return self._next_question()
        if data.startswith('att:bulk:') and data not in self._allowed_callbacks:
            return self._stale()
        if data.endswith(':confirm') and data in self._allowed_callbacks:
            self._pending_bulk = True
            self._allowed_callbacks = {
                f'att:bulk:r{self._revision}:save', f'att:bulk:r{self._revision}:cancel',
            }
            count = self._bulk_unanswered_count()
            return {
                'text': (f'Настроить посещение для {count} пока неотвеченных типов занятий? '
                         'Сохранённые ответы, лабораторные и исключённые предметы не изменятся.'),
                'buttons': [[
                    {'text': 'Да, настроить ненастроенные', 'callback_data': f'att:bulk:r{self._revision}:save'},
                    {'text': 'Отмена', 'callback_data': f'att:bulk:r{self._revision}:cancel'},
                ]],
            }
        if data in self._allowed_callbacks and self._pending_bulk:
            self._pending_bulk = False
            if data.endswith(':save'):
                from services.attendance_preferences import SubjectAttendancePreference
                for subject, events_for_subject in self._subjects.items():
                    preference = self.store.subjects.get(subject)
                    if preference is None:
                        preference = SubjectAttendancePreference()
                        self.store.subjects[subject] = preference
                    if preference.excluded or preference.lectures is False:
                        continue
                    types = {event.event_type for event in events_for_subject}
                    if EventType.LECTURE in types and preference.lectures is None:
                        preference.lectures = True
                    if EventType.PRACTICAL in types and preference.practicals is None:
                        preference.practicals = True
                self.store.save()
            self._allowed_callbacks.clear()
            return self._next_question()
        parts = data.split(':')
        if len(parts) < 3 or parts[0] != 'att' or not parts[1].isascii() or not parts[1].isdigit():
            return {'text': 'Эти кнопки устарели. Открой посещаемость заново.',
                    'buttons': [[{'text': 'Открыть посещаемость', 'callback_data': 'manage:attendance'}]]}
        subject_index = int(parts[1])
        if len(parts) >= 5 and parts[2].startswith('r') and parts[2][1:].isdigit():
            if int(parts[2][1:]) != self._revision:
                return self._stale()
            action, *value = parts[3:]
        else:
            action, *value = parts[2:]
        names = sorted(self._subjects)
        if subject_index >= len(names):
            return {'text': 'Эти кнопки устарели. Открой посещаемость заново.',
                    'buttons': [[{'text': 'Открыть посещаемость', 'callback_data': 'manage:attendance'}]]}
        subject = names[subject_index]
        if not value:
            return {'text': 'Эти кнопки устарели. Открой посещаемость заново.',
                    'buttons': [[{'text': 'Открыть посещаемость', 'callback_data': 'manage:attendance'}]]}
        legacy_none = (action == 'lab' and value[0] == 'none'
                       and any(item.endswith(':lab:none_draft')
                               and item.startswith(f'att:{subject_index}:r{self._revision}:')
                               for item in self._allowed_callbacks))
        if data not in self._allowed_callbacks and not legacy_none:
            return self._stale()
        if action in {'lecture', 'practical'}:
            self.store.set_choice(subject, action, value[0] == 'yes')
        elif action == 'lab':
            if value[0] == 'toggle':
                slot = value[1].replace('~', ':')
                selection = self._lab_selection.setdefault(subject, set())
                if slot in selection:
                    selection.remove(slot)
                else:
                    selection.add(slot)
                reply = self._next_question()
                reply['edit_message'] = True
                return reply
            if value[0] in {'all_draft', 'none_draft'}:
                slots = {lab_slot_key(event) for event in self._subjects[subject]
                         if event.event_type == EventType.LAB}
                self._lab_selection[subject] = slots if value[0] == 'all_draft' else set()
                reply = self._next_question()
                reply['edit_message'] = True
                return reply
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
        self._allowed_callbacks.clear()
        return self._next_question()

    @staticmethod
    def _stale() -> dict:
        return {'text': 'Эти кнопки устарели. Открой посещаемость заново.',
                'buttons': [[{'text': 'Открыть посещаемость', 'callback_data': 'manage:attendance'}]]}

    def _bulk_unanswered_count(self) -> int:
        count = 0
        for subject, events_for_subject in self._subjects.items():
            preference = self.store.subjects.get(subject)
            if preference and (preference.excluded or preference.lectures is False):
                continue
            types = {event.event_type for event in events_for_subject}
            count += sum(getattr(preference, attr, None) is None
                         for kind, attr in ((EventType.LECTURE, 'lectures'),
                                            (EventType.PRACTICAL, 'practicals'))
                         if kind in types)
        return count

    def _callback(self, index: int, action: str, value: str) -> str:
        return f'att:{index}:r{self._revision}:{action}:{value}'

    def _next_question(self) -> dict:
        self._revision += 1
        for index, subject in enumerate(sorted(self._subjects)):
            events = self._subjects[subject]
            preference = self.store.subjects.get(subject)
            if preference and (preference.excluded or preference.lectures is False):
                continue
            for event_type, action, label in (
                (EventType.LECTURE, 'lecture', 'лекции'),
                (EventType.PRACTICAL, 'practical', 'практики'),
            ):
                if any(event.event_type == event_type for event in events):
                    current = getattr(preference, f'{action}s', None) if preference else None
                    if current is None:
                        self._allowed_callbacks = {
                            self._callback(index, action, 'yes'), self._callback(index, action, 'no'),
                        }
                        buttons = [[
                            {'text': 'Посещаю', 'callback_data': self._callback(index, action, 'yes')},
                            {'text': 'Не посещаю предмет' if action == 'lecture' else 'Не посещаю',
                             'callback_data': self._callback(index, action, 'no')},
                        ]]
                        unanswered = self._bulk_unanswered_count()
                        if unanswered > 1:
                            self._allowed_callbacks.add(f'att:bulk:r{self._revision}:confirm')
                            buttons.append([{'text': f'Принять для всех ненастроенных ({unanswered})',
                                             'callback_data': f'att:bulk:r{self._revision}:confirm'}])
                        return {
                            'text': (f'{self._subject_label(subject)}: посещаешь {label}? '
                                     f'Осталось ответить: {unanswered} типов занятий.'),
                            'buttons': buttons,
                        }
            labs = [event for event in events if event.event_type == EventType.LAB]
            if labs and not (preference and preference.labs_enabled is not None):
                slots = {lab_slot_key(event): event for event in labs}
                selected_slots = self._lab_selection.setdefault(subject, set())
                all_callback = self._callback(index, 'lab', 'all_draft')
                none_callback = self._callback(index, 'lab', 'none_draft')
                save_callback = self._callback(index, 'lab', 'save')
                self._allowed_callbacks = {all_callback, none_callback, save_callback} | {
                    self._callback(index, 'lab', f'toggle:{key.replace(":", "~")}') for key in slots
                }
                slot_rows = []
                slot_buttons = []
                for slot_number, (key, event) in enumerate(sorted(slots.items()), 1):
                    time_label = self._slot_label(event)
                    full_label = time_label + (f' · {event.location}' if event.location else '')
                    slot_rows.append(f'{slot_number}. {self._clip(full_label, 450)}')
                    slot_buttons.append([{
                        'text': ('✅ ' if key in selected_slots else '') + f'ЛБ{slot_number} · {time_label}',
                        'callback_data': self._callback(index, 'lab', f'toggle:{key.replace(":", "~")}'),
                    }])
                return {
                    'text': self._clip(
                        f'{self._subject_label(subject)}: выбери посещаемые лабораторные. '
                        'Номера на кнопках соответствуют списку; можно выбрать несколько слотов.\n\n'
                        'Лабораторные слоты:\n' + '\n'.join(slot_rows), 4000,
                    ),
                    'buttons': slot_buttons + [[
                        {'text': 'Все ЛБ', 'callback_data': all_callback},
                    {'text': 'Ни одной ЛБ', 'callback_data': none_callback},
                    ], [{
                        'text': 'Сохранить выбор' if selected_slots else 'Сохранить: не посещаю ЛБ',
                        'callback_data': save_callback,
                    }]],
                }
        buttons = [[{
            'text': 'Изменить ответы',
            'callback_data': 'manage:attendance',
        }, {
            'text': 'Сбросить все ответы',
            'callback_data': f'att:reset:r{self._revision}:preview',
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
        self._allowed_callbacks = {f'att:reset:r{self._revision}:preview'}
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

    @staticmethod
    def _clip(value: str, limit: int) -> str:
        return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'
