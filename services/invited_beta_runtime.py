"""Invite-gated transport reusing the isolated pilot domain application."""
from contextlib import contextmanager
import fcntl
from functools import partial
import json
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

import requests

from services.closed_beta_runtime import ClosedBetaApplication
from services.telegram_schedule_bot import TelegramScheduleBot
from services.user_registry import UserRegistryStore


def atomic_json(path, value):
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex)
    try:
        with temporary.open('x') as output:
            os.chmod(temporary, 0o600)
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
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

    def set_access(self, chat, enabled):
        # Same lock as execution: return from revoke means no writes remain in flight.
        with self.locked(chat) as directory:
            atomic_json(directory / 'access.json', {'enabled': enabled, 'generation': uuid4().hex})


class InvitedBot(TelegramScheduleBot):
    def __init__(self, token, root, adapter_factory, application_factory=None):
        super().__init__(token, 'invite-only', '', Path(root) / 'unused')
        self.invites = Invitations(root)
        self.adapter_factory = adapter_factory
        self.application_factory = application_factory or partial(ClosedBetaApplication, enable_plan_updates=True)
        self.applications = {}

    def process_update(self, update):
        callback = update.get('callback_query') or {}
        message = callback.get('message') if callback else update.get('message')
        if not isinstance(message, dict):
            return None
        chat = message.get('chat') or {}
        sender = callback.get('from') if callback else message.get('from')
        chat_id = str(chat.get('id'))
        if (chat.get('type') != 'private' or not isinstance(sender, dict)
                or str(sender.get('id')) != chat_id
                or not re.fullmatch(r'[1-9][0-9]{0,19}', chat_id)):
            return None
        # Unknown /start never creates even a directory.
        try:
            access = self.invites.read(chat_id)
            if not access or access.get('enabled') is not True:
                return None
            with self.invites.locked(chat_id) as directory:
                access = self.invites.read(chat_id)
                if not access or access.get('enabled') is not True:
                    return None
                update_id = update.get('update_id')
                if type(update_id) is not int or update_id < 0:
                    return None
                cursor = directory / 'last_update.json'
                if cursor.exists() and update_id <= json.loads(cursor.read_text()):
                    return None
                # At-most-once dispatch; after a crash use a NEW preview to resume journals.
                atomic_json(cursor, update_id)
                cached = self.applications.get(chat_id)
                if not cached or cached[0] != access['generation']:
                    app = self.application_factory(directory / 'state', chat_id,
                                                   self.adapter_factory(chat_id))
                    self.applications[chat_id] = (access['generation'], app)
                app = self.applications[chat_id][1]
                if callback:
                    return app.handle_callback(chat_id, callback.get('data'))
                return app.handle_text(chat_id, message.get('text'))
        except Exception:
            return {'text': 'Действие не завершено. Повтори /weekly_preview; часть записи могла сохраниться. '
                            'При повторной ошибке обратись к оператору.', 'buttons': []}

    def run_forever(self):
        # Consume even denied updates durably: an invitation must not replay a
        # previously rejected /start after a crash before Telegram's next poll.
        cursor = self.invites.root / 'polling_offset.json'
        self.invites.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        offset = json.loads(cursor.read_text()) if cursor.exists() else None
        while True:
            try:
                response = requests.get(f'{self.base_url}/getUpdates', params={
                    'timeout': 25, 'offset': offset, 'allowed_updates': ['message', 'callback_query'],
                }, timeout=35)
                response.raise_for_status()
                payload = response.json()
                if not payload.get('ok'):
                    raise ValueError('Polling unavailable')
                for update in payload['result']:
                    identifier = update.get('update_id')
                    if type(identifier) is not int or identifier < 0:
                        continue
                    if offset is not None and identifier < offset:
                        continue
                    offset = identifier + 1
                    atomic_json(cursor, offset)
                    reply = self.process_update(update)
                    if reply:
                        message = (update.get('callback_query') or {}).get('message') or update['message']
                        try:
                            self.send_message(message['chat']['id'], reply['text'], reply.get('buttons'))
                        except requests.RequestException:
                            pass  # Delivery failure must not repeat a domain operation.
            except (requests.RequestException, ValueError, KeyError):
                time.sleep(1)
