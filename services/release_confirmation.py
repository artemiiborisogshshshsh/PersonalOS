"""Read-only verification of a GitHub-observed Amvera release confirmation."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

import requests


GITHUB_REPOSITORY = 'artemiiborisogshshshsh/PersonalOS'
GITHUB_API = 'https://api.github.com'
GITHUB_CREATOR = 'github-actions[bot]'
DEPLOYMENT_ENVIRONMENT = 'amvera-production'
HEALTH_URL = 'https://personalos-borisov1artemii1.mia0.amvera.tech/healthz'
MIN_POLL_INTERVAL_SECONDS = 300
_SHA = re.compile(r'^[0-9a-f]{40}$')
_FINGERPRINT = re.compile(r'^[0-9a-f]{64}$')
_RELEASE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')


def valid_released_manifest(manifest: dict) -> bool:
    if (not isinstance(manifest, dict) or manifest.get('status') != 'released'
            or not isinstance(manifest.get('id'), str)
            or not _RELEASE_ID.fullmatch(manifest['id'])
            or not isinstance(manifest.get('text'), str)):
        return False
    if manifest.get('announcement_mode', 'automatic') not in ('automatic', 'manual_with_image'):
        return False
    revision = manifest.get('revision')
    if revision is not None and (not isinstance(revision, str) or not _SHA.fullmatch(revision)):
        return False
    try:
        return bool(manifest['text'].strip()) and len(manifest['text'].encode('utf-16-le')) // 2 <= 4096
    except UnicodeError:
        return False


def manifest_confirmation_digest(manifest: dict) -> str:
    """Bind confirmation to release content and its automatic/manual delivery mode."""
    if not valid_released_manifest(manifest):
        raise ValueError('Invalid release manifest')
    fields = {key: manifest[key] for key in ('id', 'text', 'status')}
    fields['announcement_mode'] = manifest.get('announcement_mode', 'automatic')
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


class GitHubReleaseConfirmation:
    """Rate-limited observer for GitHub deployment statuses; never creates them."""

    def __init__(self, state_dir, *, get=None, clock=None, min_interval=MIN_POLL_INTERVAL_SECONDS):
        self.state_dir = Path(state_dir)
        self.get = get or requests.get
        self.clock = clock or time.time
        self.min_interval = max(MIN_POLL_INTERVAL_SECONDS, int(min_interval))
        self._cache_path = self.state_dir / 'release_confirmation_poll.json'
        self._lock_path = self.state_dir / 'release_confirmation_poll.lock'

    def _claim_poll(self) -> bool:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            return False
        if self.state_dir.is_symlink() or not self.state_dir.is_dir():
            return False
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except OSError:
            return False
        with os.fdopen(fd, 'a+b') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
            except OSError:
                return False
            try:
                if self._cache_path.is_symlink():
                    return False
                try:
                    prior = json.loads(self._cache_path.read_text(encoding='utf-8'))
                    last_check = prior.get('last_check')
                    now = self.clock()
                    if not self._valid_time(last_check) or not self._valid_time(now):
                        return False
                    if now - last_check < self.min_interval:
                        return False
                except FileNotFoundError:
                    now = self.clock()
                    if not self._valid_time(now):
                        return False
                except (OSError, ValueError, AttributeError):
                    return False
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.state_dir,
                            prefix='.release-confirmation-', delete=False) as output:
                        temporary = Path(output.name)
                        os.fchmod(output.fileno(), 0o600)
                        json.dump({'last_check': now}, output)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, self._cache_path)
                    temporary = None
                    directory_fd = os.open(self.state_dir, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                    return True
                except OSError:
                    return False
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _valid_time(value):
        if type(value) not in (int, float) or abs(value) > 10**15:
            return False
        return math.isfinite(value)

    def _get_json(self, url, **kwargs):
        try:
            response = self.get(url, timeout=(3, 8), allow_redirects=False,
                headers={'Accept': 'application/vnd.github+json',
                         'X-GitHub-Api-Version': '2022-11-28'}, **kwargs)
            if response.status_code != 200:
                return None
            data = response.json()
            return data
        except Exception:
            return None

    def confirm(self, manifest: dict, artifact_fingerprint: str) -> dict:
        """Return confirmed SHA or pending; all observer/API errors stay retryable."""
        if (not valid_released_manifest(manifest)
                or not _FINGERPRINT.fullmatch(str(artifact_fingerprint))):
            return {'status': 'pending'}
        expected_revision = manifest.get('revision')
        if not self._claim_poll():
            return {'status': 'pending'}

        url = f'{GITHUB_API}/repos/{GITHUB_REPOSITORY}/deployments'
        deployments = self._get_json(url, params={'environment': DEPLOYMENT_ENVIRONMENT, 'per_page': 100})
        if not isinstance(deployments, list):
            return {'status': 'pending'}
        try:
            manifest_digest = manifest_confirmation_digest(manifest)
        except (KeyError, TypeError, ValueError, UnicodeError):
            return {'status': 'pending'}

        for deployment in deployments:
            if not isinstance(deployment, dict):
                continue
            creator = deployment.get('creator')
            payload = deployment.get('payload')
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue
            revision = deployment.get('sha')
            deployment_id = deployment.get('id')
            if (deployment.get('environment') != DEPLOYMENT_ENVIRONMENT
                    or not isinstance(creator, dict) or creator.get('login') != GITHUB_CREATOR
                    or not isinstance(revision, str) or not _SHA.fullmatch(revision)
                    or (expected_revision is not None and revision != expected_revision)
                    or not isinstance(deployment_id, int) or isinstance(deployment_id, bool)
                    or deployment_id <= 0 or not isinstance(payload, dict)
                    or payload.get('source_branch') != 'main'
                    or payload.get('release_id') != manifest['id']
                    or payload.get('artifact_fingerprint') != artifact_fingerprint
                    or payload.get('manifest_sha256') != manifest_digest):
                continue
            statuses = self._get_json(
                f'{GITHUB_API}/repos/{GITHUB_REPOSITORY}/deployments/{deployment_id}/statuses',
                params={'per_page': 1})
            if not isinstance(statuses, list) or not statuses:
                return {'status': 'pending'}
            latest = statuses[0]
            status_creator = latest.get('creator') if isinstance(latest, dict) else None
            if (not isinstance(latest, dict) or latest.get('state') != 'success'
                    or not isinstance(status_creator, dict)
                    or status_creator.get('login') != GITHUB_CREATOR):
                return {'status': 'pending'}
            return {'status': 'confirmed', 'revision': revision}
        return {'status': 'pending'}
