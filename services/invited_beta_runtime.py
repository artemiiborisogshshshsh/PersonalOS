"""Invite-gated transport reusing the isolated pilot domain application."""
from contextlib import contextmanager
import fcntl
from functools import partial
import json
import logging
import os
from pathlib import Path
import re
import signal
import threading
import time
from uuid import uuid4

import requests

from services.closed_beta_runtime import ClosedBetaApplication
from services.telegram_schedule_bot import TelegramScheduleBot
from services.user_registry import UserRegistryStore
from services.access_billing import AccessBillingStore
from services.image_schedule import download_telegram_image, ImageScheduleError
from services.telegram_natural_text_proposals import normalized_request, TEXT_PREVIEW_REQUESTS


logger = logging.getLogger(__name__)


def atomic_json(path, value):
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex)
    try:
        with temporary.open('x') as output:
            os.chmod(temporary, 0o600)
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


class Invitations:
    def __init__(self, root):
        self.root = Path(root)
        self.locks = {}
        self.guard = threading.Lock()

    def directory(self, chat):
        chat = str(chat)
        if not re.fullmatch(r'[1-9][0-9]{0,19}', chat):
            raise ValueError('Expected private Telegram user ID')
        return self.root / UserRegistryStore._user_id(chat)

    @contextmanager
    def locked(self, chat):
        directory = self.directory(chat)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.guard:
            lock = self.locks.setdefault(str(chat), threading.RLock())
        with lock:
            fd = os.open(directory / '.access.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                yield directory

    def read(self, chat):
        path = self.directory(chat) / 'access.json'
        return json.loads(path.read_text()) if path.exists() else None

    def set_access(self, chat, enabled, on_revoke=None):
        # Same lock as execution: return from revoke means no writes remain in flight.
        with self.locked(chat) as directory:
            atomic_json(directory / 'access.json', {'enabled': enabled, 'generation': uuid4().hex})
            if not enabled and on_revoke is not None:
                on_revoke(chat)


class InvitedBot(TelegramScheduleBot):
    def __init__(self, token, root, adapter_factory, application_factory=None, google_oauth=None,
                 *, open_registration=False, bot_identity_provider=None):
        super().__init__(token, 'invite-only', '', Path(root) / 'unused')
        self.invites = Invitations(root)
        self.open_registration = open_registration
        self._bot_identity_provider = bot_identity_provider
        self._bot_username = None
        self._identity_loaded = False
        self._identity_lock = threading.Lock()
        self.adapter_factory = adapter_factory
        self.application_factory = application_factory or partial(ClosedBetaApplication, enable_plan_updates=True)
        self.google_oauth = google_oauth
        self.applications = {}
        # Lazy construction preserves unknown/invitation-gated account isolation.
        self.access_store = None
        self.release_delivery = None
        self.release_delivery_stopped = False
        self._maintenance_stop = threading.Event()
        self._maintenance_thread = None

    def _fetch_bot_identity(self):
        response = requests.get(self.base_url + '/getMe', timeout=5)
        response.raise_for_status()
        payload = response.json()
        return payload.get('result') if payload.get('ok') else None

    def bot_username(self):
        # Production resolves getMe at startup; direct application use stays
        # offline unless an identity provider was explicitly injected.
        with self._identity_lock:
            if self._bot_identity_provider is None:
                return self._bot_username
            if not self._identity_loaded:
                try:
                    identity = self._bot_identity_provider()
                    name = identity.get('username') if isinstance(identity, dict) else None
                    if isinstance(name, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', name):
                        self._bot_username = name
                except (requests.RequestException, ValueError, TypeError):
                    pass
                self._identity_loaded = True
            return self._bot_username

    def _configure_access(self, app):
        if isinstance(app, ClosedBetaApplication):
            if self.access_store is None:
                self.access_store = AccessBillingStore(self.invites.root / 'access_billing.sqlite3')
            app.configure_access(self.access_store)
            app.access.bot_username_provider = self.bot_username
        return app

    @staticmethod
    def identity(update):
        if not isinstance(update, dict):
            return None
        callback = update.get('callback_query') or {}
        if not isinstance(callback, dict):
            return None
        message = callback.get('message') if callback else update.get('message')
        if not isinstance(message, dict):
            return None
        chat = message.get('chat') or {}
        if not isinstance(chat, dict):
            return None
        sender = callback.get('from') if callback else message.get('from')
        chat_id = str(chat.get('id'))
        if (chat.get('type') != 'private' or not isinstance(sender, dict)
                or str(sender.get('id')) != chat_id
                or not re.fullmatch(r'[1-9][0-9]{0,19}', chat_id)
                or type(update.get('update_id')) is not int or update['update_id'] < 0):
            return None
        return chat_id

    FAILURE = ('Действие не завершено. Повтори /weekly_preview; часть записи могла сохраниться. '
               'При повторной ошибке обратись к оператору.')
    RECOVERY = ('Предыдущий запрос был прерван или его ответ не доставлен. '
                'Часть записи могла сохраниться. Выполни новый /weekly_preview '
                'и проверь план перед новым подтверждением.')
    PREVIEW_COMMANDS = {'/weekly_preview', '/preparations', '/update_all', '/update_schedule'}
    PREVIEW_ACK = ('Проверяю расписание и Google Calendar. Это может занять несколько минут. '
                   'Запись начнётся только после отдельного подтверждения.')

    def _handle_locked(self, update, chat_id, directory, access):
        cursor = directory / 'last_update.json'
        if cursor.exists() and update['update_id'] <= json.loads(cursor.read_text()):
            return None
        # At-most-once dispatch; after a crash use a NEW preview to resume journals.
        atomic_json(cursor, update['update_id'])
        preview_requested = False
        if not update.get('callback_query'):
            text = update.get('message', {}).get('text', '')
            command = text.split()[0].split('@')[0] if isinstance(text, str) and text.split() else ''
            # Keep an interrupted-operation notice as the first reply; the
            # worker will attach it to the result of this fresh preview.
            preview_requested = ((command in self.PREVIEW_COMMANDS or normalized_request(text) in TEXT_PREVIEW_REQUESTS)
                                 and not (directory / 'interrupted_update.json').exists())
            if command == '/privacy':
                if self.google_oauth and self.google_oauth.redirect_uri:
                    base = self.google_oauth.redirect_uri.rsplit('/google/oauth/callback', 1)[0]
                    return {'text': 'Политика конфиденциальности MyAiCalendar: ' + base + '/privacy', 'buttons': []}
                return {'text': 'Политику конфиденциальности можно запросить: artemii.borisov22@gmail.com', 'buttons': []}
            if command == '/connect_google':
                if self.google_oauth is None:
                    return {'text': 'Подключение Google пока не настроено оператором.', 'buttons': []}
                return self.google_oauth.create_link(chat_id, access['generation'])
        cached = self.applications.get(chat_id)
        if not cached or cached[0] != access['generation']:
            app = self.application_factory(directory / 'state', chat_id,
                                           self.adapter_factory(chat_id))
            self._configure_access(app)
            self.applications[chat_id] = (access['generation'], app)
        app = self.applications[chat_id][1]
        callback = update.get('callback_query')
        can_preview = not isinstance(app, ClosedBetaApplication) or app.access.allowed()
        if preview_requested and can_preview:
            try:
                self.send_message(chat_id, self.PREVIEW_ACK)
            except requests.RequestException:
                pass
        if callback:
            if (callback.get('data') == 'nt:plan' and can_preview
                    and not (directory / 'interrupted_update.json').exists()):
                try:
                    self.send_message(chat_id, self.PREVIEW_ACK)
                except requests.RequestException:
                    pass
            return app.handle_callback(chat_id, callback.get('data'))
        message = update['message']
        if message.get('photo') or message.get('document'):
            if isinstance(app, ClosedBetaApplication):
                denied = app.access.guard()
                if denied:
                    return denied
            if hasattr(app, 'learning') and not app.learning.wants_schedule_image(message.get('caption', '')):
                return app.handle_attachment(chat_id, message)
            if isinstance(app, ClosedBetaApplication) and not app.enable_schedule_ocr:
                return app.schedule_ocr_deferred()
            app.pending = app.image_pending = None
            try:
                self.send_message(chat_id, 'Распознаю расписание… Проверю все занятия перед добавлением.')
            except requests.RequestException:
                pass
            try:
                content = download_telegram_image(self.base_url, message)
            except ImageScheduleError as error:
                return {'text': str(error), 'buttons': []}
            except Exception:
                return {'text': 'Не удалось загрузить изображение. Отправь его ещё раз.', 'buttons': []}
            return app.handle_image(chat_id, content, message.get('caption', ''))
        if isinstance(app, ClosedBetaApplication):
            return app.handle_text(chat_id, message.get('text'), message=message)
        return app.handle_text(chat_id, message.get('text'))

    def access_for_update(self, update, chat_id):
        """Admit only a new private /start; explicit revocations remain final.

        Call outside the polling condition: workers hold this account lock
        before acquiring that condition to persist their progress.
        """
        access = self.invites.read(chat_id)
        if access is not None or not self.open_registration:
            return access
        message = update.get('message')
        text = message.get('text') if isinstance(message, dict) else None
        if update.get('callback_query') or not isinstance(text, str):
            return None
        command = text.split()[0].split('@')[0] if text.split() else ''
        if command != '/start':
            return None
        with self.invites.locked(chat_id) as directory:
            access = self.invites.read(chat_id)
            if access is None:
                access = {'enabled': True, 'generation': uuid4().hex}
                atomic_json(directory / 'access.json', access)
            return access

    def process_update(self, update):
        chat_id = self.identity(update)
        if chat_id is None:
            return None
        try:
            access = self.access_for_update(update, chat_id)
            if not access or access.get('enabled') is not True:
                return None
            with self.invites.locked(chat_id) as directory:
                access = self.invites.read(chat_id)
                if not access or access.get('enabled') is not True:
                    return None
                return self._handle_locked(update, chat_id, directory, access)
        except Exception:
            return {'text': self.FAILURE, 'buttons': []}

    def run_learning_tick(self):
        # Discover persisted applications too: notifications survive a bot restart
        # before that user sends another message.
        for access_path in self.invites.root.glob('user-*/access.json'):
            directory = access_path.parent
            registry_path = directory / 'state' / 'users_registry.json'
            if not registry_path.exists():
                continue
            try:
                accounts = UserRegistryStore(registry_path).accounts()
                for account in accounts:
                    chat_id = account.telegram_chat_id
                    with self.invites.locked(chat_id):
                        access = self.invites.read(chat_id)
                        if not access or access.get('enabled') is not True:
                            continue
                        cached = self.applications.get(chat_id)
                        if not cached or cached[0] != access['generation']:
                            app = self.application_factory(directory / 'state', chat_id, self.adapter_factory(chat_id))
                            self._configure_access(app)
                            self.applications[chat_id] = (access['generation'], app)
                        app = self.applications[chat_id][1]
                        if hasattr(app, 'background_tick'):
                            app.background_tick(self.send_message, self.send_photo, self.send_document)
            except Exception:
                logger.warning('Learning maintenance deferred for one account.')

    def _learning_worker(self):
        while not self._maintenance_stop.is_set():
            self.run_learning_tick()
            self._maintenance_stop.wait(30)

    def run_forever(self, *, workers=4, max_pending=64):
        if self._bot_identity_provider is None:
            self._bot_identity_provider = self._fetch_bot_identity
        self.bot_username()
        polling = DurablePolling(self, workers=workers, max_pending=max_pending)
        try:
            polling.start()
            self._maintenance_stop.clear()
            self._maintenance_thread = threading.Thread(target=self._learning_worker, name="learning-maintenance")
            self._maintenance_thread.start()
            while True:
                try:
                    capacity = polling.wait_capacity()
                    response = requests.get(f'{self.base_url}/getUpdates', params={
                        'timeout': 25, 'offset': polling.offset, 'limit': min(100, capacity),
                        'allowed_updates': ['message', 'callback_query'],
                    }, timeout=35)
                    response.raise_for_status()
                    payload = response.json()
                    if (not isinstance(payload, dict) or payload.get('ok') is not True
                            or not isinstance(payload.get('result'), list)):
                        raise ValueError('Polling unavailable')
                    if not self.release_delivery_stopped:
                        try:
                            if self.release_delivery is None:
                                from services.release_delivery import delivery_from_environment
                                self.release_delivery = delivery_from_environment()
                            delivery_result = self.release_delivery.check_once()
                            status = delivery_result.get('status')
                            diagnostics = {
                                'sent': (logger.info, 'Release announcement sent.'),
                                'already_sent': (logger.info, 'Release announcement was already sent.'),
                                'rejected': (logger.warning, 'Release announcement stopped: rejected.'),
                                'uncertain': (logger.warning, 'Release announcement stopped: uncertain outcome.'),
                                'blocked': (logger.warning, 'Release announcement stopped: blocked.'),
                                'error': (logger.warning, 'Release announcement stopped: error.'),
                                'invalid': (logger.warning, 'Release announcement stopped: invalid configuration.'),
                                'disabled': (logger.info, 'Release announcement delivery is disabled.'),
                            }
                            if status in diagnostics:
                                log, message = diagnostics[status]
                                log(message)
                                self.release_delivery_stopped = True
                        except Exception:
                            # Release delivery is isolated from ordinary bot polling.
                            logger.warning('Release announcement stopped: error.')
                            self.release_delivery_stopped = True
                    for update in payload['result']:
                        polling.accept(update)
                except (requests.RequestException, ValueError, KeyError):
                    time.sleep(1)
        finally:
            # No timeout/cancellation claim: the enclosing instance lock remains
            # held until ALL workers and their external calls have returned.
            self._maintenance_stop.set()
            if self._maintenance_thread is not None:
                self._maintenance_thread.join()
            polling.close()


class DurablePolling:
    """Bounded local inbox. Single polling owner; fixed workers, FIFO per user.

    The snapshot commits acceptance and offset together. queued entries replay;
    started/completed entries recover with a notice, never by replaying domain work.
    A saturated inbox backpressures Telegram polling, including other users.
    """
    def __init__(self, bot, *, workers=4, max_pending=64):
        if type(workers) is not int or type(max_pending) is not int or not 1 <= workers <= max_pending:
            raise ValueError('Require 1 <= workers <= max_pending')
        self.bot = bot
        self.worker_count, self.max_pending = workers, max_pending
        self.condition = threading.Condition()
        self.active = set()
        self.threads = []
        self.finished = []
        self.stopping = False
        self.failure = None
        bot.invites.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = bot.invites.root / 'polling_inbox.json'
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            legacy = bot.invites.root / 'polling_offset.json'
            self.state = {'offset': json.loads(legacy.read_text()) if legacy.exists() else None,
                          'entries': []}
        for entry in self.state['entries']:
            if entry['status'] in {'started', 'completed'}:
                entry['status'] = 'interrupted'
                entry.pop('reply', None)
        self._save()

    @property
    def offset(self):
        with self.condition:
            return self.state['offset']

    def _save(self):
        atomic_json(self.path, self.state)

    def check_failure(self):
        if self.failure is not None:
            raise RuntimeError('Durable polling worker failed') from self.failure

    def wait_capacity(self):
        with self.condition:
            while len(self.state['entries']) >= self.max_pending:
                self.check_failure()
                self.condition.wait()
            self.check_failure()
            return self.max_pending - len(self.state['entries'])

    def start(self):
        for index in range(self.worker_count):
            done = threading.Event()
            thread = threading.Thread(target=self._run_worker, args=(done,),
                                      name=f'invited-beta-{index}')
            # This runtime already requires POSIX flock. Defer termination
            # signals across Thread.start's native-spawn/started-event gap so
            # a live worker can never be omitted from shutdown accounting.
            previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
            try:
                self.threads.append(thread)
                self.finished.append(done)
                try:
                    thread.start()
                except BaseException:
                    # A synchronous failure before native startup has no worker
                    # to await. After startup (including an injected interrupt),
                    # retain its completion event until the worker exits.
                    if thread.ident is None:
                        self.threads.remove(thread)
                        self.finished.remove(done)
                    raise
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous)

    def accept(self, update):
        if not isinstance(update, dict):
            return False
        identifier = update.get('update_id')
        if type(identifier) is not int or identifier < 0:
            return False
        # Reject acknowledged replays before a public /start can create state.
        # Do not take an account lock while holding the polling condition.
        with self.condition:
            self.check_failure()
            if self.stopping:
                raise RuntimeError('Polling is stopping')
            if self.state['offset'] is not None and identifier < self.state['offset']:
                return False
        chat = self.bot.identity(update)
        if chat is not None:
            self.bot.access_for_update(update, chat)
        with self.condition:
            while True:
                self.check_failure()
                if self.stopping:
                    raise RuntimeError('Polling is stopping')
                if self.state['offset'] is not None and identifier < self.state['offset']:
                    return False
                chat = self.bot.identity(update)
                access = self.bot.invites.read(chat) if chat else None
                allowed = access and access.get('enabled') is True
                if not allowed or len(self.state['entries']) < self.max_pending:
                    break
                # Only fixed workers execute. Queued work for an active user
                # never takes a worker slot away from an independent user.
                self.condition.wait()
            entries = list(self.state['entries'])
            if allowed:
                entries.append({'update': update, 'chat': chat,
                                'generation': access['generation'], 'status': 'queued'})
            accepted = {'offset': identifier + 1, 'entries': entries}
            atomic_json(self.path, accepted)
            self.state = accepted
            self.condition.notify_all()
            return bool(allowed)

    def _run_worker(self, done):
        try:
            self._worker()
        finally:
            done.set()

    def _worker(self):
        while True:
            with self.condition:
                while True:
                    if self.failure is not None:
                        return
                    entry = next((row for row in self.state['entries']
                                  if row['chat'] not in self.active), None)
                    if entry is not None:
                        self.active.add(entry['chat'])
                        break
                    if self.stopping and not self.state['entries']:
                        return
                    self.condition.wait()
            try:
                self._execute(entry)
                with self.condition:
                    self.state['entries'].remove(entry)
                    self._save()
            except BaseException as error:
                # Fail closed on persistence errors. Keep durable work for restart.
                with self.condition:
                    self.failure = error
            finally:
                with self.condition:
                    self.active.discard(entry['chat'])
                    self.condition.notify_all()

    def _execute(self, entry):
        bot, chat = self.bot, entry['chat']
        with bot.invites.locked(chat) as directory:
            notice = directory / 'interrupted_update.json'
            if entry['status'] == 'interrupted':
                atomic_json(notice, {'update_id': entry['update']['update_id']})
            access = bot.invites.read(chat)
            if not access or access.get('enabled') is not True:
                return
            same_generation = access['generation'] == entry['generation']
            if entry['status'] == 'queued' and not same_generation:
                return
            if entry['status'] == 'queued':
                with self.condition:
                    entry['status'] = 'started'
                    self._save()
                try:
                    reply = bot._handle_locked(entry['update'], chat, directory, access)
                except Exception:
                    reply = {'text': bot.FAILURE, 'buttons': []}
                with self.condition:
                    entry['status'], entry['reply'] = 'completed', reply
                    self._save()
            else:
                # Never send an old generation's interactive approval buttons.
                reply = None
                if not same_generation:
                    atomic_json(notice, {'update_id': entry['update']['update_id']})
            if notice.exists():
                reply = dict(reply or {'text': '', 'buttons': []})
                reply['text'] = bot.RECOVERY + ('\n\n' + reply['text'] if reply['text'] else '')
            if reply:
                try:
                    receipt = bot.send_message(chat, reply['text'], reply.get('buttons'))
                    lesson_id = reply.get('_learning_lesson_id')
                    cached = bot.applications.get(chat)
                    if lesson_id and cached and isinstance(receipt, dict):
                        cached[1].learning.bind_message(receipt.get('message_id'), lesson_id, reply.get('_learning_kind', 'material'))
                except Exception:
                    # No domain retry on transport errors. Retain visible recovery
                    # across restarts, failed deliveries and invitation generations.
                    atomic_json(notice, {'update_id': entry['update']['update_id']})
                else:
                    if notice.exists():
                        notice.unlink()
                        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)

    def close(self):
        while True:
            try:
                with self.condition:
                    self.stopping = True
                    self.condition.notify_all()
                # Explicit completion events remain reliable even when a second
                # interrupt disrupts Python's Thread.join bookkeeping.
                for done in self.finished:
                    done.wait()
                break
            except KeyboardInterrupt:
                continue
        self.check_failure()
