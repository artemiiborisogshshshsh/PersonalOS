"""Access policy at the real Telegram boundary; no payment simulation commands.

The billing ledger is shared by invited accounts. Runtime receipts are local to
one account and never delete or rewrite its saved plan when access expires.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from services.access_billing import AccessBillingStore
from services.user_data_lifecycle import UserDataLifecycleService


class TelegramAccessFlow:
    DENIED = ('Пробный период или оплаченный доступ завершён. Новые планы, изменения и напоминания приостановлены. '
              'Данные сохранены: /saved_schedule, /tasks, /materials и /export_data доступны. '
              'Оплата пока не настроена; для продления обратись к оператору. /access — статус.')

    def __init__(self, app, store=None):
        self.app = app
        self.store = store or AccessBillingStore(app.root / 'access_billing.sqlite3')
        self.path = app.directory / 'access_runtime.json'
        self.fresh_token = None

    def _state(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile('w', dir=self.path.parent, delete=False) as output:
                temporary = Path(output.name)
                os.chmod(temporary, 0o600)
                json.dump(state, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def _published(self):
        remote_classes = any(row.get('calendar_id') and row.get('event_id') and not row.get('pending')
                             for row in self.app.classes.projection_state.rows.values())
        return any(operation.status == 'confirmed' and not operation.projection_pending
                   and (operation.published_plan.get('calendar_id') and operation.published_plan.get('rows')
                        or operation.calendar_event_ids and operation.calendar_ids or remote_classes)
                   for operation in self.app.operations.load_all().values())

    def status(self):
        user = self.app.account.id
        now = self.app.now()
        self.store.ensure_user(user, now=now)
        status = self.store.status(user, now=now)
        # Existing beta users receive a whole trial on first observation. For
        # new users only the successfully committed projection starts the trial.
        if not status.get('trial_started_at') and self._published():
            self.store.start_trial(user, now=now)
            status = self.store.status(user, now=now)
        state = self._state()
        previous = state.get('observed_expires_at')
        expiry = status.get('access_expires_at')
        expired = bool(status.get('access_expires_at') and not status['access_allowed'])
        # Also detect a renewal after an offline gap without an expired tick.
        crossed = previous and now >= datetime.fromisoformat(previous)
        if expired or crossed:
            if not state.get('resume_required'):
                state['resume_required'] = True
                state['paused_at'] = now.isoformat()
                self.fresh_token = None
                self.app.pending = self.app.image_pending = None
                self.app.image_editing = False
        if expiry != previous:
            state['observed_expires_at'] = expiry
        if state != self._state():
            self._save(state)
        return status

    def allowed(self):
        status = self.status()
        return not status.get('access_expires_at') or status['access_allowed']

    def deny(self):
        return self.app.reply(self.DENIED)

    def guard(self):
        return None if self.allowed() else self.deny()

    def background_allowed(self, send_message):
        status = self.status()
        self.deliver_warning(status, send_message)
        # Preserve pending journals, but never replay them automatically after
        # expiry or renewal. A fresh manual preview + confirmation is required.
        return ((not status.get('access_expires_at') or status['access_allowed'])
                and not self._state().get('resume_required'))

    def deliver_warning(self, status, send_message):
        end = status.get('trial_ends_at')
        if not end or status.get('paid_active') or status.get('beta_active'):
            return
        remaining = datetime.fromisoformat(end) - self.app.now()
        kind = ('expired' if remaining <= timedelta(0) else
                '1day' if remaining <= timedelta(days=1) else
                '3days' if remaining <= timedelta(days=3) else None)
        if kind is None:
            return
        key = end + ':' + kind
        state = self._state()
        if key in state.get('warnings', []):
            return
        text = (self.DENIED if kind == 'expired' else
                f'Пробный период заканчивается {self._date(end)} '
                f'({"не больше суток" if kind == "1day" else "не больше трёх дней"}). '
                'После окончания данные сохранятся; новые планы и напоминания приостановятся. '
                'Оплата пока не настроена, для продления обратись к оператору. /access')
        try:
            send_message(self.app.chat_id, text, [])
        except Exception:
            return
        # Telegram offers no send idempotency: only a crash between send and
        # this durable receipt can duplicate a warning; failed sends retry.
        state.setdefault('warnings', []).append(key)
        self._save(state)

    def _date(self, value):
        zone = ZoneInfo(self.app.profile.load().profile.timezone)
        return datetime.fromisoformat(value).astimezone(zone).strftime('%d.%m.%Y %H:%M %Z') if value else '—'

    def text(self, text):
        if not isinstance(text, str) or not text.strip():
            return None
        parts = text.split()
        command = parts[0].split('@')[0]
        if command in {'/access', '/subscription'}:
            status = self.status()
            if status.get('trial_started_at'):
                lines = ['Доступ: ' + ('активен' if status['access_allowed'] else 'завершён'),
                         'Пробный период: ' + self._date(status['trial_started_at']) + ' — ' + self._date(status['trial_ends_at'])]
            else:
                lines = ['Пробный период: 21 день с первой успешной публикации плана в Google Calendar. Пока не начат.']
            if status.get('paid_through'):
                lines.append('Оплачено до: ' + self._date(status['paid_through']))
            if status.get('beta_through'):
                lines.append('Бесплатное продление беты до: ' + self._date(status['beta_through']))
            lines += [f'Накопленная бонусная скидка: {status["bonus_points"]}%.',
                      'За приглашённого друга: тебе 25%, другу 10% после его первой успешной оплаты.',
                      'Скидка на платёж — до 50%; остаток переносится. Бонусы не продлевают пробный период.',
                      'Оплата пока не настроена. Для продления обратись к оператору.',
                      'Данные и экспорт сохраняются после окончания доступа: /export_data. /referral — приглашения.']
            if self._state().get('resume_required') and status['access_allowed']:
                lines.append('Перед возобновлением обновлений нужен новый /weekly_preview и подтверждение.')
            return self.app.reply('\n'.join(lines))
        referral = (parts[1] if command == '/referral' and len(parts) == 2 else
                    parts[1][4:] if command == '/start' and len(parts) == 2 and parts[1].startswith('ref_') else None)
        if command == '/referral' or referral is not None:
            self.status()
            prefix = ''
            if referral:
                try:
                    self.store.bind_referral(self.app.account.id, referral, now=self.app.now())
                    prefix = 'Приглашение сохранено.\n'
                except ValueError:
                    prefix = ('Приглашение не принято: проверь код. Нельзя пригласить себя, менять уже привязанный код '
                              'или привязывать его после первой публикации.\n')
            code = self.store.referral_code(self.app.account.id)
            return self.app.reply(prefix + f'Твой код: {code}\nДруг может отправить /start ref_{code} или /referral {code} '
                                  'до первой публикации плана.\n'
                                  'Закрытая beta доступна только по приглашению оператора: этот код сам по себе доступ не открывает.\n'
                                  'После первой успешной оплаты друга: тебе 25%, ему 10% скидки. '
                                  'На один платёж — максимум 50%, остаток переносится. Бонусы не добавляют дни доступа.')
        if command in {'/export', '/export_data', '/export_access'}:
            self.status()
            payload = {'access': self.store.export_user(self.app.account.id, now=self.app.now())}
            if command != '/export_access':
                scoped = UserDataLifecycleService(self.app.root).export(self.app.account.id)
                # Operational backups keep callback signing secrets. A user
                # facing export strips those internals without changing backup.
                payload['files'] = {}
                for name, content in scoped['files'].items():
                    try:
                        value = json.loads(content)
                    except (ValueError, TypeError):
                        value = content
                    payload['files'][name] = self._export_safe(value)
                payload.update(saved_schedule=self.saved_schedule(), tasks=self.saved_tasks(),
                               materials=self.saved_materials(), access_runtime=self._state())
            return self.app.reply('Экспорт сохранённых данных (JSON):\n' + json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        if command in {'/saved_schedule', '/schedule'}:
            rows = self.saved_schedule()
            return self.app.reply('Сохранённое расписание:\n' + ('\n'.join(
                f'• {row["start"]} — {row["title"]}' for row in rows) or 'Пока пусто.'))
        if command == '/materials':
            rows = self.saved_materials()
            return self.app.reply('Сохранённые материалы:\n' + ('\n'.join(
                f'• {row["course"]}: {row.get("text") or "Файл в истории личного чата"}' for row in rows)
                or 'Пока пусто.') + '\nФайлы остаются в истории чата; экспорт содержит их Telegram file_id.')
        if command == '/tasks' or text.strip().casefold() in {'покажи мои задачи', 'покажи задачи', 'мои задачи'}:
            return self.app.reply('Сохранённые задачи:\n' + ('\n'.join(
                '• ' + row['title'] + (' (выполнено)' if row.get('status') == 'done' else '')
                for row in self.saved_tasks()) or 'Пока пусто.')) if not self.allowed() else None
        if command == '/image_status':
            self.app.image_editing = False
            return self.app._image_status()
        if not self.allowed():
            if command in {'/help', '/menu', '/start'}:
                return self.app.reply(self.DENIED + '\n/privacy — конфиденциальность; /connect_google — переподключение Google.')
            if command == '/privacy':
                return self.app.reply('Политика конфиденциальности: обратись к оператору. Данные доступны через /export_data.')
            if command == '/connect_google':
                return self.app.reply('Для переподключения Google используй /connect_google в приглашённой beta или обратись к оператору.')
            return self.deny()
        return None

    @classmethod
    def _export_safe(cls, value):
        if isinstance(value, dict):
            return {key: cls._export_safe(item) for key, item in value.items()
                    if key not in {'secret', 'access_token', 'refresh_token', 'client_secret', 'token', 'signature'}}
        if isinstance(value, list):
            return [cls._export_safe(item) for item in value]
        return value

    def saved_schedule(self):
        return [{'id': row.id, 'title': row.title, 'start': row.start_time.isoformat(), 'end': row.end_time.isoformat()}
                for row in self.app.snapshot.personal_events()]

    def saved_tasks(self):
        if not self.app.natural_text:
            return []
        return self.app.natural_text.proposals.application_service.snapshot()['tasks']

    def saved_materials(self):
        return [dict(material, course=lesson['course'], lesson_id=lesson['id'])
                for lesson in self.app.learning.store.lessons() for material in lesson.get('materials', [])]

    def previewed(self):
        if self._state().get('resume_required') and self.app.pending:
            self.fresh_token = self.app.pending[0]

    def applied(self, token, reply):
        success = reply and (reply['text'].startswith('План опубликован.') or
                             reply['text'].startswith('Сохранённое обновление материалов проверено.') or
                             reply['text'].startswith('План v') and 'обновлён и проверен' in reply['text'] or
                             'Этот план уже опубликован' in reply['text'])
        if success:
            self.store.start_trial(self.app.account.id, now=self.app.now())
        if success and token == self.fresh_token:
            state = self._state()
            state.pop('resume_required', None)
            state['resumed_at'] = self.app.now().isoformat()
            self.suppress_stale_reminders(self.app.now())
            self._save(state)
            self.fresh_token = None

    def suppress_stale_reminders(self, resumed_at):
        settings = self.app.profile.load().profile
        stale_ids = []
        for notification in self.app.learning.store.due_notifications(
                self.app.now(), timezone_name=settings.timezone,
                travel_minutes=settings.travel_minutes_each_way):
            if datetime.fromisoformat(notification['due_at']) < resumed_at:
                self.app.learning.store.ack_notification(notification['id'])
                stale_ids.append(notification['id'])
        state = self.app.learning._state()
        progress = state.get('delivery_progress', {})
        changed = False
        for identifier in stale_ids:
            if identifier in progress:
                progress.pop(identifier)
                changed = True
        if changed:
            self.app.learning._save(state)
