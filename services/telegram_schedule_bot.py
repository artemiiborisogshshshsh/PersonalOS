"""Telegram command endpoint for a manual university-schedule refresh."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any, Callable, Dict, Optional
import os
import time
import threading

import requests

from services.university_schedule_source import (
    UniversityScheduleFetchError,
    UniversityScheduleFetchResult,
    fetch_university_schedule,
)


@dataclass
class TelegramScheduleBot:
    """Minimal, allow-listed long-polling bot for schedule refresh requests."""

    token: str
    allowed_chat_id: str
    source_url: str
    output_path: Path
    fetcher: Callable[[str, Path], UniversityScheduleFetchResult] = (
        fetch_university_schedule
    )
    attendance_onboarding: Any = None
    onboarding_start: Optional[Callable[[], dict]] = None
    onboarding_action: Optional[Callable[[str], dict]] = None
    onboarding_timezone_apply: Optional[Callable[[str], None]] = None
    onboarding_source_connect: Optional[Callable[[str], dict]] = None
    onboarding_attendance_complete: Optional[Callable[[], None]] = None
    onboarding_profile_complete: Optional[Callable[[], None]] = None
    onboarding_calendar_complete: Optional[Callable[[], None]] = None
    analytics_first_plan: Optional[Callable[[], None]] = None
    analytics_weekly_active: Optional[Callable[[], None]] = None
    events_loader: Optional[Callable[[], list[Any]]] = None
    calendar_preview: Optional[Callable[[], str]] = None
    calendar_apply: Optional[Callable[[], str]] = None
    work_schedule_preview: Optional[Callable[[], str]] = None
    calendar_health: Optional[Callable[[], str]] = None
    system_events_preview: Optional[Callable[[], str]] = None
    system_delete: Optional[Callable[[str], str]] = None
    system_restore: Optional[Callable[[str], str]] = None
    preparation_rules_preview: Optional[Callable[[], str]] = None
    preparation_rule_disable: Optional[Callable[[str], str]] = None
    preparation_rule_enable: Optional[Callable[[str], str]] = None
    management_home: Optional[Callable[[], dict]] = None
    management_action: Optional[Callable[[str], dict]] = None
    work_mode_apply: Optional[Callable[[str, str], str]] = None
    work_route_apply: Optional[Callable[[str, str], str]] = None
    work_feedback_complete: Optional[Callable[[str, str, str, str, str], str]] = None
    work_preparation_confirm: Optional[Callable[[], str]] = None
    work_preparation_rollback: Optional[Callable[[], str]] = None
    work_preparation_feedback_preview: Optional[Callable[[], dict]] = None
    work_preparation_feedback_detail: Optional[Callable[[str, str, int, int, str], str]] = None
    preparation_preview: Optional[Callable[[], str]] = None
    preparation_stage: Optional[Callable[[], str]] = None
    preparation_confirm: Optional[Callable[[], str]] = None
    preparation_rollback: Optional[Callable[[], str]] = None
    preparation_replan: Optional[Callable[[], str]] = None
    preparation_cleanup_preview: Optional[Callable[[], str]] = None
    preparation_cleanup_confirm: Optional[Callable[[], str]] = None
    preparation_reset_preview: Optional[Callable[[], str]] = None
    preparation_reset_confirm: Optional[Callable[[], str]] = None
    profile_preview: Optional[Callable[[], str]] = None
    profile_update: Optional[Callable[[str, str], str]] = None
    feedback_preview: Optional[Callable[[], dict]] = None
    feedback_submit: Optional[Callable[[str, str], str]] = None
    feedback_detail: Optional[Callable[[str], str]] = None
    feedback_proposal_apply: Optional[Callable[[], str]] = None
    feedback_proposal_reject: Optional[Callable[[], str]] = None
    schedule_change_replan: Optional[Callable[[], str]] = None
    schedule_refresh_reconcile: Optional[Callable[[], None]] = None
    reconciliation_review: Optional[Callable[[], dict]] = None
    reconciliation_apply: Optional[Callable[[str], dict]] = None
    scheduled_tick: Optional[Callable[[], None]] = None
    update_all: Optional[Callable[..., Any]] = None
    update_all_action: Optional[Callable[[str], Any]] = None
    natural_text_proposals: Any = None
    operation_lock: Any = field(default_factory=threading.RLock, repr=False)
    maintenance_worker: Any = field(default=None, repr=False)
    last_schedule_hash: Optional[str] = None
    request_timeout: int = 35
    pending_feedback: Dict[str, tuple[str, str]] = field(default_factory=dict)
    pending_work_feedback: Dict[str, Dict[str, str]] = field(default_factory=dict)
    pending_work_preparation_feedback: Dict[str, tuple[str, str]] = field(default_factory=dict)
    private_owner_only: bool = False
    TELEGRAM_MESSAGE_LIMIT = 4096

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError('TELEGRAM_BOT_TOKEN is required')
        if not self.allowed_chat_id:
            raise ValueError('TELEGRAM_CHAT_ID is required')
        self.base_url = f'https://api.telegram.org/bot{self.token}'

    @classmethod
    def from_environment(
        cls,
        source_url: str,
        output_path: Path,
    ) -> 'TelegramScheduleBot':
        return cls(
            token=os.environ.get('TELEGRAM_BOT_TOKEN', ''),
            allowed_chat_id=os.environ.get('TELEGRAM_CHAT_ID', ''),
            source_url=source_url,
            output_path=output_path,
        )

    def handle_text(self, chat_id: Any, text: str) -> Optional[Any]:
        if str(chat_id) != str(self.allowed_chat_id):
            return None
        if text.strip().split('@')[0] in {'/help', '/start'}:
            return self._handle_text(chat_id, text)
        if not self.operation_lock.acquire(blocking=False):
            return 'Обновление уже выполняется'
        try:
            return self._handle_text(chat_id, text)
        finally:
            self.operation_lock.release()

    def _handle_text(self, chat_id: Any, text: str) -> Optional[Any]:
        """Handle a single incoming message and return an optional response."""
        if str(chat_id) != str(self.allowed_chat_id):
            return None

        work_prep_pending = self.pending_work_preparation_feedback.get(str(chat_id))
        if work_prep_pending and not text.strip().startswith('/'):
            values = text.strip().split(maxsplit=2)
            if len(values) < 2:
                return 'Укажи: фактические минуты, сложность 1–5 и необязательный комментарий.'
            try:
                minutes, difficulty = int(values[0]), int(values[1])
            except ValueError:
                return 'Минуты и сложность должны быть числами.'
            self.pending_work_preparation_feedback.pop(str(chat_id), None)
            if self.work_preparation_feedback_detail is None:
                return 'Feedback рабочих подготовок пока не подключён.'
            block_id, outcome = work_prep_pending
            return self.work_preparation_feedback_detail(
                block_id, outcome, minutes, difficulty, values[2] if len(values) == 3 else '',
            )

        pending = self.pending_feedback.get(str(chat_id))
        if pending and not text.strip().startswith('/'):
            values = text.strip().split(maxsplit=2)
            if len(values) < 2:
                return 'Укажи: фактические минуты, сложность 1–5 и необязательный комментарий.'
            try:
                minutes, difficulty = int(values[0]), int(values[1])
            except ValueError:
                return 'Минуты и сложность должны быть числами.'
            if self.feedback_detail is None:
                return 'Feedback пока не подключён.'
            index, outcome = pending
            self.pending_feedback.pop(str(chat_id), None)
            comment = values[2] if len(values) == 3 else ''
            return self.feedback_detail(
                f'{int(index) + 1} {outcome} {minutes} {difficulty} {comment}'.strip(),
            )

        work_pending = self.pending_work_feedback.get(str(chat_id))
        if work_pending and not text.strip().startswith('/'):
            stage = work_pending['stage']
            if stage == 'homework':
                work_pending['homework'] = text.strip() or 'не задано'
                work_pending['stage'] = 'deadline'
                return 'Какой дедлайн? Напиши дату/время или «нет». '
            if stage == 'deadline':
                work_pending['deadline'] = text.strip() or 'нет'
                work_pending['stage'] = 'comment'
                return 'Добавь комментарий к паре или отправь «нет».'
            self.pending_work_feedback.pop(str(chat_id), None)
            if self.work_feedback_complete is None:
                return 'Рабочий feedback пока не подключён.'
            comment = '' if text.strip().casefold() == 'нет' else text.strip()
            return self.work_feedback_complete(
                work_pending['lesson_id'], work_pending['outcome'],
                work_pending.get('homework', ''), work_pending.get('deadline', ''), comment,
            )

        command = text.strip().split(maxsplit=1)[0].split('@', maxsplit=1)[0]
        if command == '/update_all':
            if self.update_all is None:
                return 'Общее обновление пока не подключено.'
            result = self.update_all()
            if (self.analytics_weekly_active is not None and isinstance(result, dict)
                    and 'Не удалось завершить' not in result.get('text', '')):
                self.analytics_weekly_active()
            return result
        if command == '/schedule_review':
            if self.reconciliation_review is None:
                return 'Проверка изменений расписания пока не подключена.'
            return self.reconciliation_review()
        if command == '/start' and self.onboarding_start is not None:
            return self.onboarding_start()
        if command in {'/start', '/help'}:
            return (
                '/update_all — обновить TPU, работу и подготовки, проверить Calendar. '
                'В отчёте только проблемы; черновики публикуются автоматически.\n\n'
                'Как обновить всё: TPU → пары в Calendar → работа → подготовки.\n'
                'Отправляй команды по одной и дожидайся результата каждой.\n\n'
                '1. /update_schedule — загрузить актуальное расписание TPU. '
                'Это не отдельная команда публикации пар в Calendar.\n'
                'Если TPU сообщил неоднозначную замену, /schedule_review покажет '
                'варианты и даст подтвердить нужный без создания дубля.\n'
                '2. /attendance — проверить выбранные занятия. Ответь на новые '
                'вопросы, если они появились, затем нажми «Отправить в Google Calendar». '
                'Пары попадут в Personal University Schedule. '
                'Сохранённые ответы заново вводить не нужно.\n'
                '3. /work_schedule — обновить занятия AlfaCRM и календарь «Работа». '
                'Ответь на вопросы о формате/маршруте, если бот их задаст. '
                'В самой AlfaCRM ничего не изменяется.\n'
                '4. /preparations — построить общий план подготовок к учёбе и работе '
                '(если AlfaCRM подключён). Черновики могут сразу появиться в Calendar; '
                'это не только просмотр. Для пересчёта нажми «Перепланировать», '
                'затем используй кнопки подтверждения или отката.\n\n'
                'Настройки:\n'
                '/manage — управление занятиями и правилами через кнопки.\n'
                '/planning_profile — сон, дорога и параметры подготовки.\n'
                'Быстрые настройки профиля: /sleep, /sleep_weekend, /travel, '
                '/recovery, /deep_work, /prep_durations, /prep_window, '
                '/prep_deadline, /prep_urgency.\n'
                '/preparation_rules — действующие исключения.\n'
                '/disable_preparation и /enable_preparation — выключить/включить '
                'подготовки по предмету; отправь команду без аргументов для примера.\n\n'
                'Обратная связь:\n'
                '/feedback — результат учебной подготовки.\n'
                '/feedback_apply и /feedback_reject — применить или отклонить '
                'предложенную адаптацию.\n'
                '/work_preparation_feedback — результат рабочей подготовки.\n\n'
                'Если в календаре ошибка:\n'
                '/calendar_health — проверка с исправлением: может удалить ошибочные '
                'системные подготовки и перепланировать их. Это не просто просмотр!\n'
                '/cleanup_preparation_drafts — показать дубли перед удалением по кнопке.\n'
                '/reset_preparation_drafts — показать все системные черновики перед '
                'их удалением по кнопке.\n'
                '/system_events — список событий с кодами для редактирования.\n'
                '/system_delete КОД и /system_restore КОД — убрать/восстановить '
                'системное событие.\n\n'
                'Если команда завершилась ошибкой, не запускай очистку наугад: '
                'сохрани текст ошибки. /help — снова показать эту памятку.'
            )
        if command == '/attendance':
            if self.attendance_onboarding is None or self.events_loader is None:
                return 'Настройка посещаемости пока не подключена.'
            return self.attendance_onboarding.start(self.events_loader())
        if command == '/connect_tpu':
            value = text.strip()[len(command):].strip()
            if not value:
                return 'Формат: /connect_tpu https://ro-rasp.tpu.ru/gruppa_.../2026/1/view.html'
            if self.onboarding_source_connect is None:
                return 'Подключение TPU-источника пока не настроено.'
            return self.onboarding_source_connect(value)
        if command == '/work_schedule':
            if self.work_schedule_preview is None:
                return 'AlfaCRM ещё не подключён.'
            return self.work_schedule_preview()
        if command == '/calendar_health':
            if self.calendar_health is None:
                return 'Проверка Calendar пока не подключена.'
            return self.calendar_health()
        if command == '/system_events':
            if self.system_events_preview is None:
                return 'Системное редактирование пока не подключено.'
            return self.system_events_preview()
        if command in {'/system_delete', '/system_restore'}:
            code = text.strip()[len(command):].strip().casefold()
            if not code:
                return f'Сначала открой /system_events, затем отправь: {command} КОД.'
            handler = self.system_delete if command == '/system_delete' else self.system_restore
            if handler is None:
                return 'Системное редактирование пока не подключено.'
            return handler(code)
        if command == '/preparation_rules':
            if self.preparation_rules_preview is None:
                return 'Правила preparation пока не подключены.'
            return self.preparation_rules_preview()
        if command in {'/disable_preparation', '/enable_preparation'}:
            raw = text.strip()[len(command):].strip()
            if not raw:
                return (
                    f'Формат: {command} НАЗВАНИЕ_ПРЕДМЕТА [ЛК|ПР|ЛБ|все].\n'
                    f'Например: {command} Архитектура ИС ЛБ'
                )
            handler = (self.preparation_rule_disable if command == '/disable_preparation'
                       else self.preparation_rule_enable)
            if handler is None:
                return 'Правила preparation пока не подключены.'
            return handler(raw)
        if command == '/manage':
            if self.management_home is None:
                return 'Центр управления пока не подключён.'
            return self.management_home()
        if command == '/work_preparation_feedback':
            if self.work_preparation_feedback_preview is None:
                return 'Рабочие подготовки пока не подключены.'
            return self.work_preparation_feedback_preview()
        if command == '/preparations':
            if self.preparation_preview is None:
                return 'Планировщик подготовок пока не подключён.'
            preview = self.preparation_preview()
            if self.analytics_first_plan is not None:
                self.analytics_first_plan()
            return {
                'text': preview,
                'buttons': [[
                    {'text': 'Создать черновики', 'callback_data': 'prep:stage'},
                    {'text': 'Перепланировать', 'callback_data': 'prep:replan'},
                ]],
            }
        if command == '/cleanup_preparation_drafts':
            if self.preparation_cleanup_preview is None:
                return 'Очистка preparation-черновиков пока не подключена.'
            return {
                'text': self.preparation_cleanup_preview(),
                'buttons': [[
                    {'text': 'Удалить найденные дубли', 'callback_data': 'prep:cleanup-confirm'},
                ]],
            }
        if command == '/reset_preparation_drafts':
            if self.preparation_reset_preview is None:
                return 'Сброс preparation-черновиков пока не подключён.'
            return {
                'text': self.preparation_reset_preview(),
                'buttons': [[
                    {'text': 'Удалить все системные черновики', 'callback_data': 'prep:reset-confirm'},
                ]],
            }
        if command == '/planning_profile':
            if self.profile_preview is None:
                return 'Профиль планирования пока не подключён.'
            return self.profile_preview()
        if command == '/feedback':
            raw_feedback = text.strip()[len(command):].strip()
            if raw_feedback and self.feedback_detail:
                return self.feedback_detail(raw_feedback)
            if self.feedback_preview is None:
                return 'Пока нет блока подготовки, для которого можно оставить feedback.'
            return self.feedback_preview()
        if command in {'/feedback_apply', '/feedback_reject'}:
            handler = (self.feedback_proposal_apply if command == '/feedback_apply'
                       else self.feedback_proposal_reject)
            if handler is None:
                return 'Адаптация feedback пока не подключена.'
            return handler()
        if command in {
            '/sleep', '/travel', '/prep_durations', '/prep_window', '/prep_deadline',
            '/prep_urgency', '/sleep_weekend', '/recovery', '/deep_work',
        }:
            if self.profile_update is None:
                return 'Профиль планирования пока не подключён.'
            reply = self.profile_update(command, text.strip()[len(command):].strip())
            if self.onboarding_profile_complete is not None:
                self.onboarding_profile_complete()
            return reply
        if not text.strip().startswith('/') and self.natural_text_proposals is not None:
            return self.natural_text_proposals.propose(str(chat_id), text)
        if command != '/update_schedule':
            return 'Неизвестная команда. Используйте /update_schedule.'

        try:
            result = self.fetcher(self.source_url, self.output_path)
        except (ValueError, UniversityScheduleFetchError):
            return 'Не удалось обновить расписание. Проверь источник и повтори попытку.'
        if self.schedule_refresh_reconcile is not None:
            try:
                self.schedule_refresh_reconcile()
            except (ValueError, OSError):
                # The source file was downloaded, but it is unsafe to pretend
                # that its personal projection was reconciled when local state
                # could not be validated or persisted.
                return (
                    'Расписание загружено, но не удалось безопасно применить '
                    'изменения. Повтори /update_all после проверки настроек.'
                )
        reply = (
            f'Расписание обновлено: {result.event_count} событий.\n'
            f'Файл: {result.output_path.name}\n'
            f'Хеш: {result.content_hash[:12]}'
        )
        previous_hash = self.last_schedule_hash
        self.last_schedule_hash = result.content_hash
        if (
            previous_hash is not None
            and previous_hash != result.content_hash
            and self.schedule_change_replan is not None
        ):
            try:
                reply += '\n\nРасписание изменилось — ' + self.schedule_change_replan()
            except Exception:
                # Refresh itself succeeded; an optional draft replan must not
                # take down long polling or hide the new schedule.
                reply += (
                    '\n\nРасписание изменилось, но черновики не были '
                    'перепланированы. Повтори /preparations после проверки Calendar.'
                )
        return reply

    def handle_callback(self, chat_id: Any, data: str) -> Optional[dict]:
        if str(chat_id) != str(self.allowed_chat_id):
            return None
        if not self.operation_lock.acquire(blocking=False):
            return {'text': 'Обновление уже выполняется', 'buttons': []}
        try:
            return self._handle_callback(chat_id, data)
        finally:
            self.operation_lock.release()

    def _handle_callback(self, chat_id: Any, data: str) -> Optional[dict]:
        if str(chat_id) != str(self.allowed_chat_id):
            return None
        if data.startswith('nl:') and self.natural_text_proposals is not None:
            return self.natural_text_proposals.handle_callback(str(chat_id), data)
        if data.startswith('ua:') and self.update_all_action:
            return self.update_all_action(data)
        if data.startswith('ob:') and self.onboarding_action:
            parts = data.split(':', maxsplit=2)
            if parts[:2] == ['ob', 'timezone'] and len(parts) == 3 and self.onboarding_timezone_apply:
                self.onboarding_timezone_apply(parts[2])
            return self.onboarding_action(data)
        if data.startswith('rec:') and self.reconciliation_apply:
            return self.reconciliation_apply(data)
        if data == 'cal:preview' and self.calendar_preview:
            return {'text': self.calendar_preview(), 'buttons': []}
        if data == 'cal:apply' and self.calendar_apply:
            reply = self.calendar_apply()
            if self.onboarding_calendar_complete is not None and reply.startswith('Готово:'):
                self.onboarding_calendar_complete()
            return {'text': reply, 'buttons': []}
        if data == 'cal:cancel':
            return {'text': 'Синхронизация отменена. Google Calendar не изменён.', 'buttons': []}
        if data == 'prep:stage' and self.preparation_stage:
            return {
                'text': self.preparation_stage(),
                'buttons': [[
                    {'text': 'Подтвердить', 'callback_data': 'prep:confirm'},
                    {'text': 'Откатить', 'callback_data': 'prep:rollback'},
                ], [{
                    'text': 'Перепланировать', 'callback_data': 'prep:replan'},
                ]],
            }
        if data == 'prep:confirm' and self.preparation_confirm:
            return {'text': self.preparation_confirm(), 'buttons': []}
        if data == 'prep:rollback' and self.preparation_rollback:
            return {'text': self.preparation_rollback(), 'buttons': []}
        if data == 'prep:replan' and self.preparation_replan:
            return {
                'text': self.preparation_replan(),
                'buttons': [[
                    {'text': 'Создать черновики', 'callback_data': 'prep:stage'},
                ]],
            }
        if data == 'prep:cleanup-confirm' and self.preparation_cleanup_confirm:
            return {'text': self.preparation_cleanup_confirm(), 'buttons': []}
        if data == 'prep:reset-confirm' and self.preparation_reset_confirm:
            return {'text': self.preparation_reset_confirm(), 'buttons': []}
        if data.startswith('fb:') and self.feedback_submit:
            _, block_index, outcome = data.split(':', maxsplit=2)
            self.pending_feedback[str(chat_id)] = (block_index, outcome)
            return {
                'text': (
                    'Сколько минут получилось? Отправь одним сообщением: '
                    'МИНУТЫ СЛОЖНОСТЬ(1–5) комментарий.\n'
                    'Например: 45 4 тема оказалась сложной'
                ),
                'buttons': [],
            }
        if data.startswith('work:mode:') and self.work_mode_apply:
            lesson_id, mode = data.removeprefix('work:mode:').rsplit(':', maxsplit=1)
            return {'text': self.work_mode_apply(lesson_id, mode), 'buttons': []}
        if data.startswith('work:route:') and self.work_route_apply:
            lesson_id, route = data.removeprefix('work:route:').rsplit(':', maxsplit=1)
            return {'text': self.work_route_apply(lesson_id, route), 'buttons': []}
        if data.startswith('work:feedback:'):
            lesson_id, outcome = data.removeprefix('work:feedback:').rsplit(':', maxsplit=1)
            self.pending_work_feedback[str(chat_id)] = {
                'lesson_id': lesson_id, 'outcome': outcome, 'stage': 'homework',
            }
            return {'text': 'Что задал домой? Если ничего — напиши «не задано».', 'buttons': []}
        if data == 'work:prep:confirm' and self.work_preparation_confirm:
            return {'text': self.work_preparation_confirm(), 'buttons': []}
        if data == 'work:prep:rollback' and self.work_preparation_rollback:
            return {'text': self.work_preparation_rollback(), 'buttons': []}
        if data.startswith('wpfb:') and self.work_preparation_feedback_detail:
            block_id, outcome = data.removeprefix('wpfb:').rsplit(':', maxsplit=1)
            self.pending_work_preparation_feedback[str(chat_id)] = (block_id, outcome)
            return {'text': 'Сколько минут получилось? Отправь: МИНУТЫ СЛОЖНОСТЬ(1–5) комментарий.', 'buttons': []}
        if data.startswith('manage:') and self.management_action:
            return self.management_action(data)
        if not data.startswith('att:'):
            return None
        if self.attendance_onboarding is None:
            return None
        reply = self.attendance_onboarding.handle_callback(data)
        if reply.get('attendance_complete') and self.onboarding_attendance_complete is not None:
            self.onboarding_attendance_complete()
        return reply

    @classmethod
    def _message_chunks(cls, text: str) -> list[str]:
        """Split Telegram output without cutting a word unless unavoidable."""
        if len(text) <= cls.TELEGRAM_MESSAGE_LIMIT:
            return [text]
        chunks: list[str] = []
        remaining = text
        while len(remaining) > cls.TELEGRAM_MESSAGE_LIMIT:
            boundary = max(
                remaining.rfind('\n', 0, cls.TELEGRAM_MESSAGE_LIMIT),
                remaining.rfind(' ', 0, cls.TELEGRAM_MESSAGE_LIMIT),
            )
            if boundary <= 0:
                boundary = cls.TELEGRAM_MESSAGE_LIMIT
            chunks.append(remaining[:boundary].rstrip())
            remaining = remaining[boundary:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def send_message(self, chat_id: Any, text: str, buttons: Optional[list] = None) -> None:
        chunks = self._message_chunks(text)
        for index, chunk in enumerate(chunks):
            payload: Dict[str, Any] = {'chat_id': chat_id, 'text': chunk}
            # A keyboard belongs to the final part, whose text explains the
            # action. Long status reports intentionally have no keyboard.
            if buttons and index == len(chunks) - 1:
                payload['reply_markup'] = {'inline_keyboard': buttons}
            response = requests.post(
                f'{self.base_url}/sendMessage',
                json=payload,
                timeout=10,
            )
            response.raise_for_status()

    @staticmethod
    def _telegram_error_message(operation: str, error: requests.RequestException) -> str:
        """Describe a Telegram transport failure without ever printing the token URL."""
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        if status == 409:
            return (
                f'Telegram {operation} blocked (HTTP 409): another polling bot '
                'or a webhook is active for this token.'
            )
        if status == 401:
            return f'Telegram {operation} blocked (HTTP 401): check TELEGRAM_BOT_TOKEN.'
        if status == 403:
            return (
                f'Telegram {operation} blocked (HTTP 403): check that the bot '
                'can message this chat.'
            )
        return f'Telegram {operation} failed' + (f' (HTTP {status})' if status else '')

    def start_update_all(self, chat_id, callback_data=None):
        """Keep polling responsive; all Calendar access shares the same lock."""
        if str(chat_id) != str(self.allowed_chat_id):
            return

        def work():
            if not self.operation_lock.acquire(blocking=False):
                self.send_message(chat_id, 'Обновление уже выполняется')
                return
            message_id = None
            try:
                response = requests.post(f'{self.base_url}/sendMessage', json={
                    'chat_id': chat_id, 'text': 'Обновляю расписания и проверяю подготовки…',
                }, timeout=10)
                response.raise_for_status()
                message_id = response.json().get('result', {}).get('message_id')
                result = (self.handle_callback(chat_id, callback_data) if callback_data
                          else self.handle_text(chat_id, '/update_all'))
                reply = result if isinstance(result, dict) else {'text': str(result), 'buttons': []}
                if message_id:
                    payload = dict(chat_id=chat_id, message_id=message_id, text=reply['text'],
                                   reply_markup={'inline_keyboard': reply.get('buttons', [])})
                    response = requests.post(f'{self.base_url}/editMessageText', json=payload, timeout=10)
                    response.raise_for_status()
                else:
                    self.send_message(chat_id, reply['text'], reply.get('buttons'))
            except Exception:
                # Never include raw transport URLs in Telegram or logs.
                try:
                    self.send_message(chat_id, 'Обновление не завершено. Повтори /update_all; часть изменений могла сохраниться.')
                except requests.RequestException:
                    pass
            finally:
                self.operation_lock.release()

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        return worker

    def run_scheduled_tick(self):
        if not self.operation_lock.acquire(blocking=False):
            return
        try:
            if self.scheduled_tick:
                self.scheduled_tick()
        except Exception:
            # The next tick retries; never leak provider URLs or stop polling.
            pass
        finally:
            self.operation_lock.release()

    def run_forever(self) -> None:
        """Poll Telegram and process only the small allow-listed command set."""
        offset: Optional[int] = None
        last_poll_error: Optional[str] = None
        while True:
            if self.scheduled_tick is not None and (
                self.maintenance_worker is None or not self.maintenance_worker.is_alive()
            ):
                self.maintenance_worker = threading.Thread(target=self.run_scheduled_tick, daemon=True)
                self.maintenance_worker.start()
            params: Dict[str, Any] = {
                'timeout': 25,
                'allowed_updates': ['message', 'callback_query'],
            }
            if offset is not None:
                params['offset'] = offset
            try:
                response = requests.get(
                    f'{self.base_url}/getUpdates',
                    params=params,
                    timeout=self.request_timeout,
                )
                response.raise_for_status()
                updates = response.json().get('result', [])
            except requests.RequestException as error:
                message = self._telegram_error_message('polling', error)
                if message != last_poll_error:
                    print(message, flush=True)
                    last_poll_error = message
                time.sleep(1)
                continue

            last_poll_error = None

            for update in updates:
                offset = max(offset or 0, update.get('update_id', 0) + 1)
                if self.private_owner_only:
                    callback = update.get('callback_query') or {}
                    envelope = callback.get('message') or update.get('message') or {}
                    sender = callback.get('from') if callback else envelope.get('from')
                    private_chat = envelope.get('chat') or {}
                    if (private_chat.get('type') != 'private'
                            or str(private_chat.get('id')) != str(self.allowed_chat_id)
                            or not isinstance(sender, dict)
                            or str(sender.get('id')) != str(self.allowed_chat_id)):
                        continue
                message = update.get('message') or {}
                chat = message.get('chat') or {}
                text = message.get('text')
                if text is None or 'id' not in chat:
                    callback = update.get('callback_query') or {}
                    callback_message = callback.get('message') or {}
                    callback_chat = callback_message.get('chat') or {}
                    if callback.get('data') is None or 'id' not in callback_chat:
                        continue
                    if callback['data'].startswith('ua:') and ':retry:' in callback['data']:
                        self.start_update_all(callback_chat['id'], callback['data'])
                        continue
                    try:
                        reply = self.handle_callback(callback_chat['id'], callback['data'])
                    except Exception:
                        reply = {
                            'text': 'Не удалось выполнить действие. Повтори проверку; '
                                    'часть изменений могла сохраниться.',
                            'buttons': [],
                        }
                    chat = callback_chat
                else:
                    if not isinstance(text, str) or not text.strip():
                        continue
                    command = text.strip().split(maxsplit=1)[0].split('@', maxsplit=1)[0]
                    if command == '/update_all':
                        self.start_update_all(chat['id'])
                        continue
                    if (
                        command == '/update_schedule'
                        and str(chat['id']) == str(self.allowed_chat_id)
                    ):
                        # Fetching and validating an ICS source can take tens of
                        # seconds. Acknowledge it before the synchronous work so
                        # Telegram never appears to ignore the command.
                        try:
                            self.send_message(
                                chat['id'],
                                'Обновляю расписание TPU; это обычно занимает до 30 секунд…',
                            )
                        except requests.RequestException as error:
                            print(
                                self._telegram_error_message('sendMessage', error),
                                flush=True,
                            )
                    try:
                        reply = self.handle_text(chat['id'], text)
                    except Exception:
                        reply = ('Не удалось обработать команду. Повтори попытку; '
                                 'если ошибка повторяется, открой /help.')
                if reply is None:
                    if str(chat.get('id')) != str(self.allowed_chat_id):
                        print('Ignored Telegram update from an unapproved chat.', flush=True)
                    continue
                try:
                    if isinstance(reply, dict):
                        self.send_message(
                            chat['id'], reply['text'], reply.get('buttons'),
                        )
                    else:
                        self.send_message(chat['id'], reply)
                except requests.RequestException as error:
                    print(self._telegram_error_message('sendMessage', error), flush=True)
                    continue
