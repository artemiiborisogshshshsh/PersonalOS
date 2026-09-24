#!/usr/bin/env python3
"""One invite-only bot. No dotenv, automatic migration or shared Google token."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.closed_beta_bot import instance_lock
from services.invited_beta_runtime import Invitations, InvitedBot
from services.user_registry import UserRegistryStore


def configuration(environ):
    paths = []
    for name in ('PERSONAL_OS_BETA_DIR', 'PERSONAL_OS_GOOGLE_DIR'):
        raw = environ.get(name, '')
        path = Path(raw).expanduser()
        if not raw or not path.is_absolute():
            raise ValueError('Explicit absolute private directories required')
        path = path.resolve()
        if path == ROOT or ROOT in path.parents:
            raise ValueError('Use directories outside repository')
        if path.exists() and (not path.is_dir() or path.stat().st_mode & 0o077):
            raise ValueError('Directories must be private')
        paths.append(path)
    state, google = paths
    if state == google or state in google.parents or google in state.parents:
        raise ValueError('Separate state and Google directories required')
    return state, google


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'invite', 'revoke', 'check'))
    parser.add_argument('chat_id', nargs='?')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        root, google = configuration(os.environ)
        if args.action in {'invite', 'revoke'}:
            Invitations(root).set_access(args.chat_id, args.action == 'invite')
            print('Access updated. In-flight operations finished before this change.')
            return 0
        if not os.environ.get('TELEGRAM_BOT_TOKEN'):
            raise ValueError('Bot token required')
        if args.action == 'check':
            print('CONFIG OK: local checks only; providers NOT verified.')
            return 0
        from adapters.google_calendar_adapter import GoogleCalendarAdapter
        def adapter(chat):
            token = google / (UserRegistryStore._user_id(chat) + '.json')
            if token.is_symlink() or (token.exists() and (not token.is_file() or token.stat().st_mode & 0o077)):
                raise ValueError('Private per-user Google token required')
            return GoogleCalendarAdapter({'token_path': str(token), 'initialize_calendar': False,
                                          'allow_interactive_auth': False})
        with instance_lock(root):
            InvitedBot(os.environ['TELEGRAM_BOT_TOKEN'], root, adapter).run_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('BETA STOPPED: check private configuration and instance lock.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
