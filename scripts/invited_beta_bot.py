#!/usr/bin/env python3
"""One invite-only bot. No dotenv, automatic migration or shared Google token."""
import argparse
import os
import signal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.closed_beta_bot import instance_lock
from services.google_oauth_web import GoogleOAuthWeb, serve_oauth_callback
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
            invitations = Invitations(root)
            if args.action == 'revoke':
                oauth = GoogleOAuthWeb(root, google, invitations)
                invitations.set_access(args.chat_id, False, on_revoke=oauth.forget_user)
            else:
                invitations.set_access(args.chat_id, True)
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
        def stop(signum, frame):
            raise KeyboardInterrupt()
        previous_term = signal.signal(signal.SIGTERM, stop)
        try:
            with instance_lock(root):
                invitations = Invitations(root)
                oauth = GoogleOAuthWeb(
                    root, google, invitations,
                    client_id=os.environ.get('GOOGLE_OAUTH_CLIENT_ID', ''),
                    client_secret=os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', ''),
                    redirect_uri=os.environ.get('GOOGLE_OAUTH_REDIRECT_URI', ''),
                )
                bot = InvitedBot(os.environ['TELEGRAM_BOT_TOKEN'], root, adapter,
                                 google_oauth=oauth)
                server = None
                if oauth.configured:
                    port = int(os.environ.get('PERSONAL_OS_HTTP_PORT', '80'))
                    server, _ = serve_oauth_callback(bot, oauth, port=port)
                else:
                    print('Google OAuth web flow is not configured; /connect_google is unavailable.',
                          file=sys.stderr, flush=True)
                try:
                    bot.run_forever()
                finally:
                    if server is not None:
                        server.shutdown()
                        server.server_close()
        finally:
            signal.signal(signal.SIGTERM, previous_term)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('BETA STOPPED: check private configuration and instance lock.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
