#!/usr/bin/env python3
"""One explicitly invited private chat per isolated process. No .env loading."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def configuration(environ):
    required = ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'PERSONAL_OS_DATA_DIR',
                'GOOGLE_CALENDAR_TOKEN_PATH')
    if any(not environ.get(name) for name in required):
        raise ValueError('Required pilot environment is incomplete')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', environ['TELEGRAM_CHAT_ID']):
        raise ValueError('Pilot requires one private Telegram chat ID')
    root = Path(environ['PERSONAL_OS_DATA_DIR']).expanduser()
    token = Path(environ['GOOGLE_CALENDAR_TOKEN_PATH']).expanduser()
    if not root.is_absolute() or not token.is_absolute():
        raise ValueError('Pilot state and token paths must be absolute')
    root, token = root.resolve(), token.resolve()
    if root == ROOT or ROOT in root.parents or root == token or root in token.parents:
        raise ValueError('Use state outside repository and token outside state')
    if not token.is_file():
        raise ValueError('Operator must authorize Google before startup')
    if token.stat().st_mode & 0o077:
        raise ValueError('Google token file must be private (chmod 600)')
    if root.exists() and (not root.is_dir() or root.stat().st_mode & 0o077):
        raise ValueError('Pilot directory must be private (chmod 700)')
    return root, environ['TELEGRAM_CHAT_ID'], environ['TELEGRAM_BOT_TOKEN'], token


@contextmanager
def instance_lock(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / '.pilot.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another process owns this pilot directory') from None
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Local configuration only; no provider calls')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        root, chat_id, token, google_token = configuration(os.environ)
        if args.check:
            print('CONFIG OK: local checks only; Telegram/TPU/Google and restore are NOT verified.')
            return 0
        # Import after validation, so missing configuration cannot open state.
        from adapters.google_calendar_adapter import GoogleCalendarAdapter
        from services.closed_beta_runtime import ClosedBetaApplication, PilotBot
        with instance_lock(root):
            adapter = GoogleCalendarAdapter({
                'token_path': str(google_token), 'initialize_calendar': False,
                'allow_interactive_auth': False,
            })
            app = ClosedBetaApplication(root, chat_id, adapter)
            bot = PilotBot(token, chat_id, app)
            print('Closed pilot started: one invited private chat; automatic writes disabled.', flush=True)
            bot.run_forever()
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('PILOT STOPPED: check private configuration, directory ownership and instance lock. '
              'Provider readiness is not established.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
