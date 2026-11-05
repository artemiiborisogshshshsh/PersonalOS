"""Telegram learning dialogue with durable lesson context and private file references."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import os
from uuid import uuid4
from zoneinfo import ZoneInfo

from services.learning_lifecycle import LearningLifecycleStore


class TelegramLearningFlow:
    def __init__(self, app):
        self.app = app
        self.store = LearningLifecycleStore(app.directory / 'learning_lifecycle.json', app.account.id)
        self.path = app.directory / 'telegram_learning_context.json'

    def _state(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile('w', dir=self.path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def refresh(self):
        events = self.app.snapshot.personal_events()
        if events:
            self.store.reconcile_lessons(events)
        blocks = [block for operation in self.app.operations.load_all().values() for block in operation.blocks]
        self.store.register_preparation_blocks(blocks)

    def enrich_events(self, events):
        from dataclasses import replace
        lessons = {row.get('source_personal_id'): row for row in self.store.lessons()}
        result = []
        for event in events:
            lesson = lessons.get(event.id)
            if lesson and lesson['materials']:
                description = event.description.split('\n\nУчебные материалы:\n')[0]
                event = replace(event, description=description + '\n\nУчебные материалы:\n' + learning_description(lesson))
            result.append(event)
        return result

    def enrich_blocks(self, blocks):
        from dataclasses import replace
        lessons = {row.get('source_personal_id'): row for row in self.store.lessons()}
        result = []
        for block in blocks:
            lesson = lessons.get(block.source_event_id)
            if lesson and lesson['materials']:
                block = replace(block, reason=block.reason.split('\n\nУчебные материалы:\n')[0]
                                + '\n\nУчебные материалы:\n' + learning_description(lesson, preparation=True))
            result.append(block)
        return result

    def _reply(self, text, buttons=None):
        style = getattr(self.app.profile.load(), 'communication_style', 'friendly')
        if style == 'casual' and text.startswith('Сохранено'):
            text = text.replace('Сохранено', 'Готово', 1)
        reply = self.app.reply(text, buttons)
        context = self.store.get_context()
        if context and context.get('lesson_id'):
            reply['_learning_lesson_id'] = context['lesson_id']
            reply['_learning_kind'] = context.get('kind', 'material')
        return reply

    def bind_message(self, message_id, lesson_id, kind='material'):
        if type(message_id) is int:
            lesson = self.store.get_lesson(lesson_id)
            state = self._state()
            state.setdefault('prompts', {})[str(message_id)] = {'lesson_id': lesson_id, 'kind': kind, 'revision': lesson['revision']}
            self._save(state)

    def reply_context(self, message):
        reply = message.get('reply_to_message')
        if not isinstance(reply, dict):
            return None, False
        saved = self._state().get('prompts', {}).get(str(reply.get('message_id')))
        if isinstance(saved, str):
            saved = {'lesson_id': saved, 'kind': 'material'}
        if saved:
            saved = {**saved, 'message_id': reply.get('message_id')}
        return saved, True

    def lesson_for_reply(self, message):
        saved, is_reply = self.reply_context(message)
        return saved.get('lesson_id') if saved else None, is_reply

    def _changed(self, text, *, homework_lesson_id=None):
        state = self._state()
        state['sync_pending'] = True
        self._save(state)
        try:
            message = self.app.sync_learning()
        except ValueError as error:
            from services.published_plan_updates import PlanConflict
            detail = str(error)
            actionable = ('не хватает свободного времени', 'срок ДЗ уже прошёл',
                          'не найден непрерывный', 'Максимальный блок подготовки',
                          'В допустимом окне нет времени')
            message = detail if isinstance(error, PlanConflict) or any(part in detail for part in actionable) else 'Данные сохранены. Обновление Google пока не завершено; повторю автоматически.'
        except Exception:
            message = 'Данные сохранены. Обновление Google пока не завершено; повторю автоматически.'
        buttons = None
        if homework_lesson_id:
            receipt, buttons = self._homework_schedule_receipt(homework_lesson_id, message)
            text += '\n' + receipt
        return self._reply(text + ('\n' + message if message else ''), buttons)

    def _homework_estimate(self, lesson):
        zone = ZoneInfo(self.app.profile.load().profile.timezone)
        due = datetime.fromisoformat(lesson['due_at']).astimezone(zone) if lesson.get('due_at') else None
        if due:
            due_text = ('Срок сдачи: ' if lesson.get('due_explicit') else 'Предполагаемый срок сдачи: ')
            due_text += due.strftime('%d.%m.%Y %H:%M')
        else:
            due_text = 'Срок сдачи пока не задан.'
        duration = int(lesson.get('duration_minutes') or 0)
        duration_text = ('Время на ДЗ: ' if lesson.get('duration_override') else 'Оценка времени на ДЗ: ')
        duration_text += f'{duration} мин.' if duration else 'пока не задано.'
        return due_text + '\n' + duration_text

    def _homework_controls(self, lesson):
        state = self._state()
        actions = state.setdefault('estimate_actions', {})
        for old_token, saved in list(actions.items()):
            if saved.get('lesson_id') == lesson['id']:
                del actions[old_token]
        tokens = {}
        for field in ('deadline', 'duration'):
            token = uuid4().hex[:12]
            tokens[field] = token
            actions[token] = {'lesson_id': lesson['id'], 'revision': lesson['revision']}
        self._save(state)
        return [[
            {'text': 'Изменить срок', 'callback_data': f'learn:estimate:{tokens["deadline"]}:deadline'},
            {'text': 'Изменить время', 'callback_data': f'learn:estimate:{tokens["duration"]}:duration'},
        ]]

    def cancel_estimate_input(self):
        """Cancel the active estimate editor and invalidate its reply prompts."""
        state = self._state()
        active = state.pop('estimate_input', None)
        state['prompts'] = {
            message_id: prompt for message_id, prompt in state.get('prompts', {}).items()
            if prompt.get('kind') not in {'estimate_deadline', 'estimate_duration'}
        }
        self._save(state)
        context = self.store.get_context()
        if context and context.get('kind') in {'estimate_deadline', 'estimate_duration'}:
            self.store.set_context((active or context).get('lesson_id'))
        return bool(active)

    def _estimate_cancel_button(self):
        active = self._state().get('estimate_input') or {}
        token = active.get('token')
        return [[{'text': 'Отмена', 'callback_data': f'learn:estimate_cancel:{token}'}]] if token else None

    def _homework_schedule_receipt(self, lesson_id, sync_message):
        """Report only future lesson-owned blocks present in the durable plan."""
        state = self._state()
        verified_no_change = (sync_message == ''
                              and not self.store.has_pending_calendar_sync())
        if state.get('sync_pending') or (not verified_no_change
                                        and 'Google Calendar обновлён.' not in (sync_message or '')):
            return ('Подготовка пока не подтверждена в Google Calendar. '
                    'Сохранённое ДЗ останется в боте; повторю синхронизацию автоматически.\n'
                    + self._homework_estimate(self.store.get_lesson(lesson_id)),
                    self._homework_controls(self.store.get_lesson(lesson_id)))
        lesson = self.store.get_lesson(lesson_id)
        if not lesson:
            return 'Подготовка пока не подтверждена: не удалось сверить занятие.', None
        if 'Подготовка не перепланирована' in (sync_message or ''):
            return ('Google Calendar обновлён, но план подготовки остался прежним. '
                    'Новый интервал для этого ДЗ не подтверждён.\n' + self._homework_estimate(lesson),
                    self._homework_controls(lesson))
        settings = self.app.profile.load()
        zone = ZoneInfo(settings.profile.timezone)
        now = self.app.now().astimezone(zone)
        source_ids = set(lesson.get('source_aliases', [])) | {lesson.get('source_personal_id'), lesson_id}
        slots = []
        for operation in self.app.operations.load_all().values():
            if operation.projection_pending:
                continue
            plan_rows = (operation.published_plan or {}).get('rows', {})
            for block in operation.blocks:
                row = plan_rows.get(block.id)
                if (row and row.get('kind') == 'preparation'
                        and block.id.startswith('learning-prep:')
                        and not block.manual_conflict
                        and block.source_event_id in source_ids
                        and block.end.astimezone(zone) > now):
                    slots.append((block.start.astimezone(zone), block.end.astimezone(zone)))
        slots.sort()
        lesson_start = datetime.fromisoformat(lesson['start']).astimezone(zone)
        lesson_when = lesson_start.strftime('%d.%m.%Y %H:%M')
        if not slots:
            return (f'ДЗ сохранено по занятию «{lesson["course"]}» ({lesson_when}). '
                    'Время подготовки пока не назначено.\n' + self._homework_estimate(lesson),
                    self._homework_controls(lesson))
        shown = slots[:3]
        lines = [f'ДЗ сохранено по занятию «{lesson["course"]}» ({lesson_when}). '
                 'Интервалы из подтверждённого плана:']
        for start, end in shown:
            end_format = '%d.%m.%Y %H:%M' if end.date() != start.date() else '%H:%M'
            lines.append(f'• {start:%d.%m.%Y %H:%M}–{end:{end_format}}')
        if len(slots) > len(shown):
            lines.append(f'И ещё интервалов: {len(slots) - len(shown)}.')
        lines.append(self._homework_estimate(lesson))
        return '\n'.join(lines), self._homework_controls(lesson)

    def _lesson(self, text, *, use_context=True):
        self.refresh()
        lessons = [row for row in self.store.lessons() if row.get('active', True)]
        # Russian inflections usually share the first five letters.
        words = re.findall(r'[\w]+', text.casefold())
        matches = []
        for row in lessons:
            tokens = re.findall(r'[\w]+', row['course'].casefold())
            if tokens and all(any(word.startswith(token[:5]) if len(token) > 3 else word == token
                                 for word in words) for token in tokens):
                matches.append(row)
        if matches:
            now = self.app.now()
            past = [row for row in matches if datetime.fromisoformat(row['start']) <= now]
            pool = past or matches
            # Different lesson types on the same day require explicit selection.
            latest_day = max(datetime.fromisoformat(row['start']).date() for row in pool) if past else min(datetime.fromisoformat(row['start']).date() for row in pool)
            pool = [row for row in pool if datetime.fromisoformat(row['start']).date() == latest_day]
            return pool[0] if len(pool) == 1 else None
        context = self.store.get_context() if use_context else None
        # An explicit unknown subject must never inherit another lesson's context.
        if re.search(r'(?i)\bпо\s+[а-яёa-z]{1,}', text):
            return None
        return self.store.get_lesson(context['lesson_id']) if context and context.get('lesson_id') else None

    def _choose(self, pending):
        state = self._state()
        token = uuid4().hex[:12]
        lessons = [row for row in self.store.lessons() if row.get('active', True)]
        lessons.sort(key=lambda row: row['start'], reverse=True)
        state['selection'] = {'token': token, 'pending': pending, 'lessons': [row['id'] for row in lessons[:20]]}
        self._save(state)
        buttons = [[{'text': f"{row['course']} · {datetime.fromisoformat(row['start']):%d.%m %H:%M}",
                     'callback_data': f'learn:select:{token}:{index}'}]
                   for index, row in enumerate(lessons[:20])]
        return self.app.reply('Выбери занятие по предмету, дате и времени.', buttons)

    def material_entry(self):
        """Start an explicit homework/material intake without saving a blank item."""
        self.refresh()
        return self._choose({'entry': 'material'})

    @staticmethod
    def is_schedule_image_caption(caption=''):
        return bool(re.search(r'(?i)(?:расписани|/import_schedule)', caption or ''))

    def wants_schedule_image(self, caption=''):
        explicit = self.is_schedule_image_caption(caption)
        if not self.app.enable_schedule_ocr:
            # An old /import_schedule flag must not consume an unlabelled note.
            return explicit
        state = self._state()
        if state.pop('schedule_image', False):
            self._save(state)
            return True
        self.refresh()
        return explicit

    def handle_attachment(self, message):
        if not self.app.enable_schedule_ocr and self.is_schedule_image_caption(message.get('caption', '')):
            return self.app.schedule_ocr_deferred()
        self.refresh()
        photo = (message.get('photo') or [{}])[-1]
        file = photo if photo.get('file_id') else message.get('document', {})
        if not file.get('file_id'):
            return self._reply('Пришли фото или файл материала ещё раз.')
        pending = {'attachment': {'file_id': file['file_id'], 'file_unique_id': file.get('file_unique_id'),
                                  'source_message_id': message.get('message_id'), 'file_type': 'photo' if photo.get('file_id') else 'document'},
                   'text': message.get('caption', '')}
        reply_lesson, is_reply = self.lesson_for_reply(message)
        # A bare attachment has no reliable subject signal. Do not inherit the
        # last text-entry context; that can silently put a photo on another class.
        lesson = (self.store.get_lesson(reply_lesson) if reply_lesson else
                  None if is_reply or not pending['text'].strip() else self._lesson(pending['text'], use_context=False))
        if lesson is None:
            return self._choose(pending)
        return self._save_material(lesson, pending)

    def _save_material(self, lesson, pending):
        text = pending.get('text', '')
        kind = 'note' if re.search(r'(?i)(запомн|заметк|конспект|тема)', text) else 'homework'
        metadata = dict(pending.get('attachment', {}))
        if pending.get('source_message_id') is not None:
            metadata['source_message_id'] = pending['source_message_id']
        self.store.add_material(lesson['id'], text=text, kind=kind, **metadata)
        self.store.set_context(lesson['id'])
        return self._changed(
            f"Сохранено к занятию «{lesson['course']}». "
            + ('Заметка и материалы.' if kind == 'note' else 'ДЗ и материалы.')
            + ' Чтобы добавить ещё, ответь на вопрос бота о нужной паре или укажи предмет в сообщении. '
            + 'Фото и файлы, сохранённые к занятию, придут в начале подготовки.',
            homework_lesson_id=lesson['id'] if kind == 'homework' else None,
        )

    def handle_text(self, text, *, lesson_id=None, source_message_id=None, unknown_reply=False, prompt_kind=None, prompt_revision=None):
        if text.strip().split('@')[0] == '/import_schedule':
            if not self.app.enable_schedule_ocr:
                return self.app.schedule_ocr_deferred()
            state = self._state()
            state['schedule_image'] = True
            self._save(state)
            return self._reply('Пришли фото расписания. Проверю распознанные пары перед добавлением.')
        if text.startswith('/'):
            return None
        state = self._state()
        if prompt_kind is None:
            active = state.get('estimate_input')
            context = self.store.get_context()
            if (not unknown_reply and active and context and context.get('lesson_id') == active.get('lesson_id')
                    and context.get('kind') == active.get('kind')):
                prompt_kind, prompt_revision = active['kind'], active['revision']
                lesson_id = lesson_id or active['lesson_id']
        pending = state.get('selection')
        learning_text = bool(re.search(r'(?i)(задал|задани|домаш|\bдз\b|запомн|заметк|конспект|номер|реши|прочита|сделат|подготовк|остал|к пятниц|к понедельник|к сред|к четверг|к вторник|к суббот|к воскрес)', text))
        context = self.store.get_context()
        if lesson_id is None and not unknown_reply and not learning_text and not pending and not (context and context.get('kind') == 'remaining') and text.casefold().strip() not in {'ничего не задали', 'ничего', 'дз нет', 'не задали', 'узнаю позже', 'позже'}:
            return None
        lesson = self.store.get_lesson(lesson_id) if lesson_id else (None if unknown_reply else self._lesson(text))
        if lesson is None:
            return self._choose({'text': text, 'source_message_id': source_message_id}) if learning_text or pending or unknown_reply else None
        if pending:
            self._save({key: value for key, value in state.items() if key != 'selection'})
            original = pending['pending']
            if original.get('attachment'):
                return self._save_material(lesson, original)
            text = original.get('text') or text
            source_message_id = original.get('source_message_id', source_message_id)
        lowered = text.casefold().strip().strip('.!?')
        if prompt_kind in {'estimate_deadline', 'estimate_duration'}:
            active = self._state().get('estimate_input') or {}
            if (active.get('lesson_id') != lesson['id'] or active.get('kind') != prompt_kind
                    or prompt_revision != lesson['revision'] or active.get('revision') != prompt_revision):
                return self._reply('Этот запрос уже изменился. Используй последние кнопки у сохранённого ДЗ.')
            if prompt_kind == 'estimate_deadline':
                try:
                    due_at = self._deadline(text)
                except ValueError:
                    due_at = None
                if due_at is None or due_at <= self.app.now():
                    return self._reply('Не распознал будущий срок. Напиши будущую дату в формате ДД.ММ или «к пятнице».',
                                       self._estimate_cancel_button())
                self.store.set_deadline(lesson['id'], due_at)
                confirmation = f'Срок сдачи сохранён: {due_at:%d.%m.%Y %H:%M}.'
            else:
                minutes = self._minutes(text)
                if minutes is None:
                    return self._reply('Не распознал время. Укажи, например, «45 минут» или «2 часа».',
                                       self._estimate_cancel_button())
                try:
                    self.store.set_homework_duration(lesson['id'], minutes)
                except ValueError:
                    return self._reply('Укажи от 1 минуты до 7 дней, например, «45 минут».',
                                       self._estimate_cancel_button())
                confirmation = f'Время на ДЗ сохранено: {minutes} мин.'
            state = self._state()
            state.pop('estimate_input', None)
            self._save(state)
            self.store.set_context(lesson['id'])
            return self._changed(confirmation + ' Пересчитаю подготовку.', homework_lesson_id=lesson['id'])
        if prompt_kind == 'preparation_end':
            if prompt_revision is not None and prompt_revision != lesson['revision']:
                return self._reply('Ответ по этой подготовке уже изменился. Используй последний вопрос.')
            action = ('done' if lowered in {'да', 'сделал', 'готово', 'выполнено'} else
                      'incomplete' if lowered in {'нет', 'не сделал', 'не успел'} else None)
            if action:
                return self.handle_callback(self.store.callback_data(lesson['id'], action))
        if prompt_kind == 'attendance' and lesson.get('attendance') is not None and lowered in {'да', 'нет', 'был', 'была', 'не был', 'не была', 'пропустил', 'пропустила'}:
            return self._reply('Посещение этой пары уже сохранено.')
        if prompt_kind in {'attendance', 'evening'} and lesson.get('attendance') is None:
            action = ('attend' if lowered in {'да', 'был', 'была'} else
                      'miss' if lowered in {'нет', 'не был', 'не была', 'пропустил', 'пропустила'} else None)
            if action:
                return self.handle_callback(self.store.callback_data(lesson['id'], action))
        if prompt_kind == 'evening' and lesson.get('attendance') is not None:
            if lowered in {'нет', 'не узнал', 'не узнала', 'пока нет'}:
                self.store.apply_callback(self.store.callback_data(lesson['id'], 'later'))
                return self._reply('Когда узнаешь, пришли материал и ДЗ к этой паре.')
            if lowered in {'да', 'узнал', 'узнала'}:
                self.store.set_context(lesson['id'])
                return self._reply('Пришли ДЗ и материалы текстом или фото либо напиши «ничего не задали».')
        if prompt_kind == 'remaining':
            self.store.set_context(lesson['id'], kind='remaining')
            context = self.store.get_context()
        if lowered in {'ничего не задали', 'ничего', 'дз нет', 'не задали'}:
            self.store.apply_callback(self.store.callback_data(lesson['id'], 'nothing'))
            return self._changed(f"{lesson['course']}: ничего не задали.")
        if lowered in {'узнаю позже', 'позже'}:
            self.store.apply_callback(self.store.callback_data(lesson['id'], 'later'))
            return self._reply('Хорошо. Вечером спрошу только недостающую информацию.')
        minutes = self._minutes(text)
        if context and context.get('kind') == 'remaining' and context.get('lesson_id') == lesson['id']:
            if minutes is None:
                return self._reply('Напиши, что осталось и сколько ещё времени нужно, например: «номера 18–20, 40 минут».')
            self.store.record_preparation_result(lesson['id'], False, remaining_text=text, remaining_minutes=minutes)
            self.store.set_context(lesson['id'])
            return self._changed('Сохранено, что осталось. Подберу время для продолжения.')
        if minutes is not None and re.search(r'(?i)(подготов|врем|надо|нуж|займ)', text):
            if re.search(r'(?i)(обычно|всегда|по предмету)', text):
                self.store.set_course_duration(lesson['course'], minutes)
            else:
                self.store.set_homework_duration(lesson['id'], minutes)
            return self._changed(f'Сохранено: на подготовку {minutes} мин.')
        try:
            deadline = self._deadline(text)
        except ValueError:
            return self._reply('Дата не распознана. Укажи существующую дату: например, «к 30.10».')
        if deadline:
            self.store.set_deadline(lesson['id'], deadline)
            if not re.search(r'(?i)(задал|номер|упражнен|прочита|реши|сделат)', text):
                return self._changed(f'Сохранено: срок {deadline:%d.%m %H:%M}.')
        if not learning_text and len(text.strip()) < 3:
            return None
        return self._save_material(lesson, {'text': text, 'source_message_id': source_message_id})

    @staticmethod
    def _minutes(text):
        match = re.search(r'(\d+)\s*(мин|час)', text, re.I)
        if match:
            return int(match[1]) * (60 if match[2].casefold() == 'час' else 1)
        for phrase, value in (('два часа', 120), ('две минуты', 2), ('полтора часа', 90), ('час', 60)):
            if phrase in text.casefold():
                return value
        return None

    def _deadline(self, text):
        now = self.app.now()
        date_match = re.search(r'(?:к|до)\s+(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?', text, re.I)
        if date_match:
            return now.replace(year=int(date_match[3] or now.year), month=int(date_match[2]), day=int(date_match[1]), hour=9, minute=0, second=0, microsecond=0)
        weekdays = ('понедельник', 'вторник', 'сред', 'четверг', 'пятниц', 'суббот', 'воскрес')
        for index, name in enumerate(weekdays):
            if re.search(r'(?:к|до)\s+' + name, text, re.I):
                days = (index - now.weekday()) % 7 or 7
                return (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
        if re.search(r'(?:к|до)\s+завтра', text, re.I):
            return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return None

    def handle_callback(self, data):
        if data.startswith('learn:estimate:'):
            try:
                _, _, token, field = data.split(':')
            except ValueError:
                return self._reply('Эта кнопка устарела. Используй последние кнопки у сохранённого ДЗ.')
            state = self._state()
            saved = state.get('estimate_actions', {}).pop(token, None)
            self._save(state)
            if not saved or field not in {'deadline', 'duration'}:
                return self._reply('Эта кнопка уже использована или устарела. Сохранённое ДЗ не изменено.')
            try:
                lesson = self.store.get_lesson(saved['lesson_id'])
            except KeyError:
                lesson = None
            if not lesson or not lesson.get('active') or lesson['revision'] != saved.get('revision'):
                self._save(state)
                return self._reply('Занятие изменилось. Используй последние кнопки у сохранённого ДЗ.')
            self.cancel_estimate_input()
            kind = 'estimate_deadline' if field == 'deadline' else 'estimate_duration'
            state = self._state()
            state['estimate_input'] = {'lesson_id': lesson['id'], 'revision': lesson['revision'],
                                       'kind': kind, 'token': token}
            self._save(state)
            self.store.set_context(lesson['id'], kind=kind, revision=lesson['revision'])
            prompt = ('Напиши новый срок сдачи в формате ДД.ММ или «к пятнице».'
                      if field == 'deadline' else 'Напиши, сколько времени нужно, например, «45 минут» или «2 часа».')
            return self._reply(prompt, [[{'text': 'Отмена', 'callback_data': f'learn:estimate_cancel:{token}'}]])
        if data.startswith('learn:estimate_cancel:'):
            try:
                _, _, token = data.split(':')
            except ValueError:
                return self._reply('Эта кнопка устарела. Сохранённое ДЗ не изменено.')
            active = self._state().get('estimate_input') or {}
            if active.get('token') != token:
                return self._reply('Этот редактор уже закрыт или устарел. Сохранённое ДЗ не изменено.')
            try:
                lesson = self.store.get_lesson(active['lesson_id'])
            except KeyError:
                lesson = None
            if not lesson or lesson['revision'] != active.get('revision'):
                self.cancel_estimate_input()
                return self._reply('Занятие изменилось. Сохранённое ДЗ не изменено.')
            self.cancel_estimate_input()
            return self._reply('Изменение срока или времени отменено. Сохранённое ДЗ не изменено.')
        if data.startswith('learn:select:'):
            parts = data.split(':')
            selection = self._state().get('selection', {})
            try:
                if len(parts) != 4 or parts[2] != selection.get('token') or int(parts[3]) < 0:
                    raise ValueError()
                lesson_id = selection['lessons'][int(parts[3])]
            except (ValueError, IndexError, KeyError):
                return self._reply('Эта кнопка устарела. Пришли сообщение ещё раз.')
            state = self._state()
            state.pop('selection', None)
            self._save(state)
            self.store.set_context(lesson_id)
            pending = selection['pending']
            if pending.get('entry') == 'material':
                return self._reply('Теперь отправь ДЗ или заметку текстом либо фото/файл ответом на это сообщение.')
            return self._save_material(self.store.get_lesson(lesson_id), pending) if pending.get('attachment') else self.handle_text(pending['text'], lesson_id=lesson_id, source_message_id=pending.get('source_message_id'))
        if not data.startswith('learn:'):
            return None
        result = self.store.apply_callback(data)
        if result is None:
            return self._reply('Эта кнопка уже использована или устарела. Сохранённые ответы не изменены.')
        action = data.split(':')[3]
        lesson_id = result.get('lesson_id') or result.get('id')
        lesson = self.store.get_lesson(lesson_id) if lesson_id else result.get('lesson', {})
        if action in {'attend', 'miss'}:
            prefix = '' if action == 'attend' else 'Нужно узнать материал и ДЗ у одногруппников. Не буду придумывать задания. '
            return self._reply(prefix + 'Пришли ДЗ и материалы текстом или фото ответом на это сообщение. '
                               'Если отправляешь отдельно, укажи предмет и пару в подписи.', [[
                {'text': 'Ничего не задали', 'callback_data': self.store.callback_data(lesson['id'], 'nothing')},
                {'text': 'Узнаю позже', 'callback_data': self.store.callback_data(lesson['id'], 'later')} ]])
        if action == 'incomplete':
            self.store.set_context(lesson['id'], kind='remaining')
            return self._reply('Что осталось и сколько ещё времени нужно? Материалы уже сохранены.')
        if action == 'later':
            return self._reply('Хорошо. Напомню вечером только о недостающей информации.')
        return self._changed('ДЗ выполнено. Будущие подготовки обновлены.' if action == 'done' else 'Сохранено: ничего не задали.')

    def deliver_due(self, send_message, send_photo, send_document=None):
        access = getattr(self.app, 'access', None)
        if access and (not access.allowed() or access._state().get('resume_required')):
            return []
        self.refresh()
        settings = self.app.profile.load()
        now = self.app.now()
        sent = []
        for notification in self.store.due_notifications(now, timezone_name=settings.profile.timezone,
                                                         travel_minutes=settings.profile.travel_minutes_each_way):
            identifier = notification['id']
            resumed_at = access._state().get('resumed_at') if access else None
            if resumed_at and datetime.fromisoformat(notification['due_at']) < datetime.fromisoformat(resumed_at):
                self.store.ack_notification(identifier)
                state = self._state()
                if identifier in state.get('delivery_progress', {}):
                    state['delivery_progress'].pop(identifier)
                    self._save(state)
                continue
            if not self.store.claim_notification(identifier, now):
                continue
            lesson = self.store.get_lesson(notification['lesson_id'])
            kind = notification['kind']
            buttons = []
            if kind == 'attendance':
                text = f"{lesson['course']} · {datetime.fromisoformat(lesson['start']):%d.%m %H:%M}: был на паре?"
                buttons = [[{'text': 'Был', 'callback_data': self.store.callback_data(lesson['id'], 'attend')},
                            {'text': 'Пропустил', 'callback_data': self.store.callback_data(lesson['id'], 'miss')}]]
            elif kind == 'evening':
                text = (f"{lesson['course']}: был на паре?" if lesson.get('attendance') is None else
                        f"{lesson['course']}: узнал материал и ДЗ? Пришли только недостающую информацию.")
                actions = ('attend', 'miss') if lesson.get('attendance') is None else ('nothing', 'later')
                labels = ('Был', 'Пропустил') if lesson.get('attendance') is None else ('Ничего не задали', 'Узнаю позже')
                buttons = [[{'text': label, 'callback_data': self.store.callback_data(lesson['id'], action)} for label, action in zip(labels, actions)]]
            elif kind == 'preparation_end':
                text = f"{lesson['course']}: сделал?"
                buttons = [[{'text': 'Да', 'callback_data': self.store.callback_data(lesson['id'], 'done')},
                            {'text': 'Нет', 'callback_data': self.store.callback_data(lesson['id'], 'incomplete')}]]
            else:
                context = self.store.preparation_context(notification['block_id'])
                block = context['block']
                minutes = int((datetime.fromisoformat(block['end']) - datetime.fromisoformat(block['start'])).total_seconds() / 60)
                text = f"Подготовка: {lesson['course']}. Доступно {minutes} мин.\n" + learning_description(lesson, preparation=True)
            style = getattr(settings, 'communication_style', 'friendly')
            if style == 'friendly':
                text = ('Начинаем подготовку. ' if kind == 'preparation_start' else 'Подскажи, ') + text
            elif style == 'casual':
                text = ('Пора за дело. ' if kind == 'preparation_start' else 'Давай сверимся. ') + text
            try:
                state = self._state()
                progress = state.setdefault('delivery_progress', {}).setdefault(identifier, {'materials': []})
                if not progress.get('message_sent'):
                    if access and not access.allowed():
                        raise PermissionError('Access expired before notification delivery')
                    receipt = send_message(self.app.chat_id, text, buttons)
                    message_id = receipt.get('message_id') if isinstance(receipt, dict) else None
                    progress.update(message_sent=True, message_id=message_id)
                    if type(message_id) is int:
                        state.setdefault('prompts', {})[str(message_id)] = {'lesson_id': lesson['id'], 'kind': kind, 'revision': lesson['revision']}
                    self._save(state)
                if kind == 'preparation_start':
                    for material in lesson.get('materials', []):
                        if material.get('file_id') and material['id'] not in progress['materials']:
                            sender = send_document if material.get('file_type') == 'document' and send_document else send_photo
                            if access and not access.allowed():
                                raise PermissionError('Access expired before material delivery')
                            sender(self.app.chat_id, material['file_id'])
                            progress['materials'].append(material['id'])
                            self._save(state)
                self.store.ack_notification(identifier, message_id=progress.get('message_id'))
                state['delivery_progress'].pop(identifier, None)
                self._save(state)
                sent.append(identifier)
            except Exception:
                self.store.release_notification(identifier)
        return sent


def learning_description(lesson, *, preparation=False):
    lines = []
    for material in lesson.get('materials', []):
        if material.get('text'):
            lines.append(('ДЗ: ' if material.get('kind') == 'homework' else 'Заметка: ') + material['text'])
    if preparation and lesson.get('remaining_text'):
        lines.append('Осталось: ' + lesson['remaining_text'])
    if preparation and lesson.get('remaining_minutes'):
        lines.append(f"Оставшееся время: {lesson['remaining_minutes']} мин.")
    if lesson.get('homework_state') == 'assigned' and lesson.get('due_at'):
        due = datetime.fromisoformat(lesson['due_at'])
        lines.append(f'Срок сдачи ДЗ: {due:%d.%m.%Y %H:%M}.')
    if any(material.get('file_id') for material in lesson.get('materials', [])):
        lines.append('Фото и файлы доступны в личном чате с ботом и придут в начале подготовки.')
    return '\n'.join(lines) or 'Материалы пока не добавлены.'
