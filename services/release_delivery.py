"""Opt-in, fail-closed validation and transport for release announcements."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests

from services.release_announcements import CHANNEL, announce_release


_ANNOUNCEMENT_CONFIG_ENV = ('PERSONAL_OS_RELEASE_MANIFEST', 'PERSONAL_OS_RELEASE_RECEIPT',
                            'PERSONAL_OS_ANNOUNCEMENT_DIR', 'PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL')
_TERMINAL = {'sent', 'already_sent', 'rejected', 'uncertain', 'blocked', 'error'}


def release_artifact_fingerprint(root) -> str:
    """Hash the complete expected deployed Python/runtime artifact set."""
    root = Path(root)
    files = set()
    for directory in ('adapters', 'services', 'scripts', 'persistence', 'cli'):
        base = root / directory
        if base.is_symlink():
            return 'unavailable'
        if not base.exists():
            continue
        if base.is_symlink() or not base.is_dir():
            return 'unavailable'
        try:
            walked = os.walk(base, followlinks=False, onerror=lambda error: (_ for _ in ()).throw(error))
            for current, directories, names in walked:
                current_path = Path(current)
                for name in directories:
                    if (current_path / name).is_symlink():
                        return 'unavailable'
                for name in names:
                    path = current_path / name
                    if path.suffix == '.py':
                        if path.is_symlink() or not path.is_file():
                            return 'unavailable'
                        files.add(path.relative_to(root).as_posix())
        except OSError:
            return 'unavailable'
    files.update(('models.py', 'planning_engine.py', 'ids.py', 'results.py',
                  'requirements-closed-beta.txt'))
    digest = hashlib.sha256(b'myaicalendar-release-artifact-v1\0')
    try:
        for relative in sorted(files):
            path = root / relative
            if path.is_symlink() or not path.is_file():
                return 'unavailable'
            name, content = relative.encode(), path.read_bytes()
            digest.update(len(name).to_bytes(4, 'big') + name)
            digest.update(len(content).to_bytes(8, 'big') + content)
    except (OSError, ValueError, TypeError):
        return 'unavailable'
    return digest.hexdigest()


class ReleaseAnnouncementDelivery:
    def __init__(self, root=None, environ=None, get=None, post=None, announce=None):
        self.root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
        self.env = os.environ if environ is None else environ
        self.get = get or requests.get
        self.post = post or requests.post
        self.announce = announce or announce_release
        self.stopped = False

    def _config(self):
        values = [self.env.get(key, '') for key in _ANNOUNCEMENT_CONFIG_ENV]
        use_main_bot = self.env.get('PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT', '')
        dedicated_token = self.env.get('TELEGRAM_ANNOUNCEMENT_BOT_TOKEN', '')
        if use_main_bot not in ('', '1'):
            return 'invalid', None
        if use_main_bot == '1':
            if dedicated_token:
                return 'invalid', None
            token = self.env.get('TELEGRAM_BOT_TOKEN', '')
            if not token:
                return 'invalid', None
        else:
            token = dedicated_token
        if not any(values) and not dedicated_token and use_main_bot == '':
            return 'disabled', None
        if not all(values) or not token:
            return 'invalid', None
        manifest, receipt, state_dir, health_url = values
        paths = []
        for value in (manifest, receipt, state_dir):
            path = Path(value)
            if not path.is_absolute():
                return 'invalid', None
            paths.append(path)
        try:
            parsed = urlsplit(health_url)
            valid_health_url = (parsed.scheme == 'https' and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None and not parsed.query
                and not parsed.fragment and parsed.path == '/healthz' and parsed.port in (None, 443))
        except ValueError:
            valid_health_url = False
        if not valid_health_url:
            return 'invalid', None
        return 'configured', (paths[0], paths[1], paths[2], health_url, token)

    @staticmethod
    def _read(path):
        if path.is_symlink() or not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, UnicodeError):
            return None

    def check_once(self):
        state, config = self._config()
        if state == 'disabled':
            return {'status': 'disabled'}
        if state == 'invalid':
            return {'status': 'invalid'}
        if self.stopped:
            return {'status': 'stopped'}
        manifest_path, receipt_path, state_dir, health_url, token = config
        manifest = self._read(manifest_path)
        if manifest is None:
            # Missing manifest is pending, malformed is blocked.
            result = {'status': 'pending' if not manifest_path.exists() and not manifest_path.is_symlink() else 'blocked'}
        elif manifest.get('status') != 'released':
            result = {'status': 'pending' if manifest.get('status') == 'draft' else 'blocked'}
        else:
            receipt = self._read(receipt_path)
            if receipt is None:
                result = {'status': 'pending' if not receipt_path.exists() and not receipt_path.is_symlink() else 'blocked'}
            else:
                fingerprint = release_artifact_fingerprint(self.root)
                receipt_well_formed = (
                    receipt.get('status') == 'success'
                    and isinstance(receipt.get('release_id'), str)
                    and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', receipt['release_id'])
                    and isinstance(receipt.get('revision'), str)
                    and re.fullmatch(r'[0-9a-f]{40}', receipt['revision'])
                    and isinstance(receipt.get('artifact_fingerprint'), str)
                    and re.fullmatch(r'[0-9a-f]{64}', receipt['artifact_fingerprint'])
                )
                if fingerprint == 'unavailable' or not receipt_well_formed:
                    result = {'status': 'blocked'}
                elif (receipt.get('release_id') != manifest.get('id')
                        or receipt.get('revision') != manifest.get('revision')):
                    # A previous release's valid receipt can remain at the shared path
                    # until deployment of this release succeeds and atomically replaces it.
                    result = {'status': 'pending'}
                elif receipt.get('artifact_fingerprint') != fingerprint:
                    result = {'status': 'blocked'}
                else:
                    try:
                        health = self.get(health_url, timeout=(3, 5), allow_redirects=False)
                        if health.status_code != 200 or 'Release fingerprint: ' + fingerprint + '.' not in health.text:
                            result = {'status': 'blocked'}
                        else:
                            def transport(channel, text):
                                response = self.post(
                                    'https://api.telegram.org/bot' + token + '/sendMessage',
                                    json={'chat_id': channel, 'text': text},
                                    timeout=(3, 10), allow_redirects=False)
                                try:
                                    data = response.json()
                                except Exception:
                                    return None
                                if response.status_code >= 500 or 300 <= response.status_code < 400:
                                    return None
                                if 400 <= response.status_code < 500:
                                    return data if isinstance(data, dict) and data.get('ok') is False and data.get('error_code') == response.status_code else None
                                return data if response.status_code == 200 else None
                            result = self.announce(state_dir, manifest, receipt, transport)
                    except Exception:
                        result = {'status': 'error'}
        if result.get('status') in _TERMINAL:
            self.stopped = True
        return result


def delivery_from_environment(root=None):
    return ReleaseAnnouncementDelivery(root=root)
