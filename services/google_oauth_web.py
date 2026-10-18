"""Browser-based Google Calendar OAuth for the invite-only beta bot."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlsplit
import fcntl
import hmac
import json
import os
import re
import secrets
import threading
import time

from google_auth_oauthlib.flow import Flow
from adapters.google_calendar_adapter import GoogleCalendarAdapter
from services.user_registry import UserRegistryStore
from services.image_schedule import MAX_EVENTS


STATE_TTL_SECONDS = 600
CALLBACK_PATH = '/google/oauth/callback'
RUNTIME_FINGERPRINT_FILES = (
    'adapters/google_calendar_adapter.py',
    'scripts/invited_beta_bot.py',
    'services/closed_beta_runtime.py',
    'services/google_oauth_web.py',
    'services/image_schedule.py',
    'services/telegram_replacement_review.py',
    'services/tpu_schedule_source.py',
    'services/university_schedule_source.py',
)


def runtime_code_fingerprint(root) -> str:
    """Return a short SHA-256 fingerprint of fixed runtime code paths."""
    digest = sha256()
    digest.update(b'myaicalendar-runtime-fingerprint-v1\0')
    try:
        root_path = Path(root)
        for relative_path in sorted(RUNTIME_FINGERPRINT_FILES):
            path_bytes = relative_path.encode('utf-8')
            content = (root_path / relative_path).read_bytes()
            digest.update(len(path_bytes).to_bytes(4, 'big'))
            digest.update(path_bytes)
            digest.update(len(content).to_bytes(8, 'big'))
            digest.update(content)
    except (OSError, TypeError, ValueError):
        return 'unavailable'
    return digest.hexdigest()[:16]


class GoogleOAuthError(ValueError):
    """An expected OAuth setup, consent, or callback failure."""


class GoogleOAuthWeb:
    def __init__(self, state_root, google_root, invitations, client_id='', client_secret='',
                 redirect_uri=''):
        self.state_root = Path(state_root)
        self.google_root = Path(google_root)
        self.invitations = invitations
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.redirect_uri = redirect_uri.strip()
        self.pending_root = self.state_root / 'google_oauth_pending'
        self._validate_redirect()

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def _validate_redirect(self):
        if not self.redirect_uri:
            return
        parsed = urlsplit(self.redirect_uri)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != CALLBACK_PATH):
            raise GoogleOAuthError('Google OAuth redirect must be the public HTTPS callback URL.')

    def _client_config(self):
        if not self.configured:
            raise GoogleOAuthError('Google OAuth Web client is not configured.')
        return {'web': {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': [self.redirect_uri],
        }}

    def create_link(self, chat_id, generation):
        if not self.configured:
            return {'text': 'Подключение Google пока не настроено оператором. Повтори позже.', 'buttons': []}
        state = secrets.token_urlsafe(32)
        flow = Flow.from_client_config(self._client_config(), scopes=GoogleCalendarAdapter.SCOPES)
        flow.redirect_uri = self.redirect_uri
        url, state = flow.authorization_url(
            access_type='offline', include_granted_scopes='true', prompt='consent', state=state,
        )
        self.pending_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.pending_root.is_symlink() or self.pending_root.stat().st_mode & 0o077:
            raise GoogleOAuthError('Private OAuth state directory required.')
        now = int(time.time())
        attempt = secrets.token_urlsafe(24)
        payload = {'state': state, 'chat_id': str(chat_id), 'generation': generation,
                   'attempt': attempt, 'expires_at': now + STATE_TTL_SECONDS,
                   'code_verifier': flow.code_verifier}
        with self._pending_lock():
            for pending_path in self.pending_root.glob('*.json'):
                if pending_path.name.startswith('current-'):
                    continue
                if pending_path.is_symlink():
                    continue
                try:
                    old = json.loads(pending_path.read_text(encoding='utf-8'))
                    expired = int(old.get('expires_at', 0)) < now
                except (OSError, TypeError, ValueError):
                    old, expired = {}, True
                if old.get('chat_id') == str(chat_id) or expired:
                    pending_path.unlink(missing_ok=True)
            self._atomic_json(
                self.pending_root / (sha256(state.encode('ascii')).hexdigest() + '.json'), payload,
            )
            self._atomic_json(self._current_attempt_path(chat_id), {
                'chat_id': str(chat_id), 'attempt': attempt,
            })
        return {
            'text': 'Нажми кнопку и подтверди доступ к Google Calendar. Ссылка действует 10 минут.',
            'buttons': [[{'text': 'Подключить Google Calendar', 'url': url}]],
        }

    def complete(self, state, code='', error=''):
        if not isinstance(state, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', state):
            raise GoogleOAuthError('Ссылка подключения недействительна или устарела. Открой /connect_google заново.')
        pending = self._consume_state(state)
        chat_id = pending['chat_id']
        if error:
            return chat_id, 'Подключение Google отменено. Если передумаешь, открой /connect_google.'
        if not code:
            raise GoogleOAuthError('Google не вернул подтверждение. Открой /connect_google заново.')

        with self.invitations.locked(chat_id):
            access = self.invitations.read(chat_id)
            if (not access or access.get('enabled') is not True
                    or access.get('generation') != pending.get('generation')):
                raise GoogleOAuthError('Приглашение бота больше не активно.')
            try:
                current = json.loads(self._current_attempt_path(chat_id).read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise GoogleOAuthError('Ссылка подключения устарела. Открой /connect_google заново.') from exc
            if not hmac.compare_digest(str(current.get('attempt', '')), str(pending.get('attempt', ''))):
                raise GoogleOAuthError('Открыта более новая ссылка. Используй последнюю ссылку из бота.')
            verifier = pending.get('code_verifier')
            if not isinstance(verifier, str) or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier):
                raise GoogleOAuthError('Ссылка подключения устарела. Открой /connect_google заново.')
            flow = Flow.from_client_config(self._client_config(), scopes=GoogleCalendarAdapter.SCOPES,
                                           state=state, code_verifier=verifier,
                                           autogenerate_code_verifier=False)
            flow.redirect_uri = self.redirect_uri
            try:
                flow.fetch_token(code=code, timeout=20)
            except Exception as exc:
                raise GoogleOAuthError('Google не подтвердил подключение. Открой /connect_google заново.') from exc
            credentials = flow.credentials
            token_path = self.google_root / (UserRegistryStore._user_id(chat_id) + '.json')
            if token_path.is_symlink() or (token_path.exists() and
                    (not token_path.is_file() or token_path.stat().st_mode & 0o077)):
                raise GoogleOAuthError('Файл Google-подключения имеет небезопасные права.')
            if not credentials.refresh_token:
                raise GoogleOAuthError('Google не выдал offline-доступ. Старое подключение сохранено; '
                                       'отключи доступ PersonalOS в аккаунте Google и повтори /connect_google.')
            self.google_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.google_root.is_symlink() or self.google_root.stat().st_mode & 0o077:
                raise GoogleOAuthError('Папка токенов Google должна быть приватной.')
            self._atomic_json(token_path, json.loads(credentials.to_json()))
        return chat_id, 'Google Calendar подключён. Вернись в бот и открой /calendar_status.'

    def _consume_state(self, state):
        if not self.pending_root.exists() or self.pending_root.is_symlink():
            raise GoogleOAuthError('Ссылка подключения недействительна или устарела. Открой /connect_google заново.')
        digest = sha256(state.encode('ascii')).hexdigest()
        path = self.pending_root / (digest + '.json')
        with self._pending_lock():
            if path.is_symlink() or not path.is_file():
                raise GoogleOAuthError('Ссылка подключения недействительна или уже использована.')
            try:
                pending = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise GoogleOAuthError('Не удалось проверить ссылку. Открой /connect_google заново.') from exc
            if not hmac.compare_digest(str(pending.get('state', '')), state):
                raise GoogleOAuthError('Ссылка подключения недействительна.')
            path.unlink()
            if int(pending.get('expires_at', 0)) < int(time.time()):
                raise GoogleOAuthError('Ссылка подключения истекла. Открой /connect_google заново.')
            return pending

    def _current_attempt_path(self, chat_id):
        key = UserRegistryStore._user_id(chat_id)
        return self.pending_root / ('current-' + key + '.json')

    def forget_user(self, chat_id):
        """Delete local tokens and pending links after access is revoked."""
        self.pending_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.pending_root.is_symlink() or self.pending_root.stat().st_mode & 0o077:
            raise GoogleOAuthError('Private OAuth state directory required.')
        with self._pending_lock():
            if self.pending_root.exists():
                for pending_path in self.pending_root.glob('*.json'):
                    if pending_path.is_symlink():
                        continue
                    try:
                        pending = json.loads(pending_path.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        continue
                    if pending.get('chat_id') == str(chat_id):
                        pending_path.unlink(missing_ok=True)
            token_path = self.google_root / (UserRegistryStore._user_id(chat_id) + '.json')
            token_path.unlink(missing_ok=True)

    @contextmanager
    def _pending_lock(self):
        lock_path = self.pending_root / '.lock'
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _atomic_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
            raise GoogleOAuthError('OAuth storage must be private.')
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                    prefix='.' + path.name + '.', delete=False) as output:
                temporary = Path(output.name)
                os.chmod(temporary, 0o600)
                json.dump(value, output)
                output.flush()
                os.fsync(output.fileno())
            if path.is_symlink():
                raise GoogleOAuthError('OAuth storage path cannot be a symlink.')
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def make_server(self, bot, host='0.0.0.0', port=80):
        oauth = self
        runtime_fingerprint = runtime_code_fingerprint(
            Path(__file__).resolve().parent.parent,
        )

        class BoundedOAuthServer(ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 16

            def __init__(self, address, handler):
                import threading
                self._slots = threading.BoundedSemaphore(8)
                super().__init__(address, handler)

            def process_request(self, request, client_address):
                if not self._slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    self._slots.release()
                    raise

            def process_request_thread(self, request, client_address):
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    self._slots.release()

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, _format, *args):
                # The callback URL contains the authorization code and state.
                return

            def _respond(self, status, body, content_type='text/plain; charset=utf-8'):
                payload = body.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(payload)

            def _redirect(self, path):
                self.send_response(303)
                self.send_header('Location', path)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def do_GET(self):
                parsed = urlsplit(self.path)
                public_pages = {'/': ('index.html', 'text/html; charset=utf-8'),
                                '/privacy': ('privacy.html', 'text/html; charset=utf-8'),
                                '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
                if parsed.path in public_pages:
                    filename, content_type = public_pages[parsed.path]
                    page = Path(__file__).resolve().parent.parent / 'web' / 'public' / filename
                    self._respond(200, page.read_text(encoding='utf-8'), content_type)
                    return
                if parsed.path == '/healthz':
                    self._respond(200, f'PersonalOS closed beta is running. Image schedule limit: {MAX_EVENTS} events. '
                                       'Image request format: fixed-vision-v3. OAuth flow: pkce-v1. '
                                       f'Runtime fingerprint: {runtime_fingerprint}.')
                    return
                if parsed.path == '/google/oauth/complete':
                    self._respond(200, 'Готово. Вернись в Telegram к боту.')
                    return
                if parsed.path == '/google/oauth/error':
                    self._respond(400, 'Не удалось завершить подключение. Вернись в Telegram и повтори /connect_google.')
                    return
                if parsed.path != CALLBACK_PATH or len(self.path) > 8192 or not oauth.configured:
                    self._respond(404, 'Not found.')
                    return
                try:
                    query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8)
                except ValueError:
                    self._redirect('/google/oauth/error')
                    return
                states, codes, errors = query.get('state', []), query.get('code', []), query.get('error', [])
                if len(states) != 1 or (len(codes) != 1 and len(errors) != 1) or (codes and errors):
                    self._redirect('/google/oauth/error')
                    return
                state = states[0]
                code = codes[0] if codes else ''
                error = errors[0] if errors else ''
                try:
                    chat_id, notice = oauth.complete(state, code=code, error=error)
                except GoogleOAuthError:
                    self._redirect('/google/oauth/error')
                    return
                except Exception:
                    print('Google OAuth callback failed.', flush=True)
                    self._redirect('/google/oauth/error')
                    return
                try:
                    bot.send_message(chat_id, notice)
                except Exception:
                    print('Google OAuth Telegram notification failed.', flush=True)
                self._redirect('/google/oauth/complete')

            def log_request(self, code='-', size='-'):
                return

        server = BoundedOAuthServer((host, port), Handler)
        server.timeout = 1
        return server


def serve_oauth_callback(bot, oauth, *, host='0.0.0.0', port=80):
    server = oauth.make_server(bot, host=host, port=port)
    thread = threading.Thread(target=server.serve_forever, name='google-oauth-callback', daemon=True)
    thread.start()
    return server, thread
