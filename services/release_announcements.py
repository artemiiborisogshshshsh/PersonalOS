"""Durable, fail-closed release announcement delivery."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path


CHANNEL = '@myaicalendar_updates'
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
_REVISION = re.compile(r'^[0-9a-f]{40}$')


def _fail(message: str) -> None:
    raise ValueError(message)


def _validate(release: dict, receipt: dict, channel: str) -> tuple[str, str, str, str]:
    if channel != CHANNEL:
        _fail('Invalid announcement channel')
    if not isinstance(release, dict):
        _fail('Invalid release')
    rid, revision, text = (release.get('id'), release.get('revision'), release.get('text'))
    if not isinstance(rid, str) or not _ID.fullmatch(rid):
        _fail('Invalid release id')
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        _fail('Invalid release revision')
    if not isinstance(text, str):
        _fail('Invalid announcement text')
    try:
        if not text.strip() or len(text.encode('utf-16-le')) // 2 > 4096:
            _fail('Invalid announcement text')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    except UnicodeError:
        _fail('Invalid announcement text')
    if not isinstance(receipt, dict) or receipt.get('status') != 'success' or \
            receipt.get('release_id') != rid or receipt.get('revision') != revision:
        _fail('Release receipt does not match')
    return rid, revision, text, digest


def _write(path: Path, ledger: dict) -> None:
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                prefix=f'.{path.name}.', suffix='.tmp', delete=False) as stream:
            tmp = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(ledger, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        tmp = None
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def _ensure_state_directory(path: Path) -> Path:
    """Create missing directory components and persist each parent entry."""
    target = Path(os.path.abspath(os.fspath(path)))
    chain = list(reversed((target, *target.parents)))
    current = Path(chain[0])
    for component in chain[1:]:
        current = current / component.name
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                # Recheck below; never accept a concurrently substituted symlink.
                pass
            info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('Invalid announcement state')
        # Re-sync even preexisting components: a prior attempt may have created
        # one successfully but failed before persisting its parent entry.
        parent_fd = os.open(current.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    return target


def _read(path: Path) -> dict:
    if path.is_symlink():
        raise RuntimeError('Invalid announcement state')
    if not path.exists():
        return {'version': 1, 'entries': {}}
    if not path.is_file():
        raise RuntimeError('Invalid announcement state')
    try:
        os.chmod(path, 0o600)
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != 1 or not isinstance(data.get('entries'), dict):
            raise ValueError
        for key, entry in data['entries'].items():
            if not isinstance(key, str) or not key.startswith(CHANNEL + ':') or \
                    not _ID.fullmatch(key[len(CHANNEL) + 1:]) or not isinstance(entry, dict):
                raise ValueError
            status = entry.get('status')
            if not isinstance(entry.get('revision'), str) or not _REVISION.fullmatch(entry['revision']) or \
                    not isinstance(entry.get('text_sha256'), str) or \
                    not re.fullmatch(r'[0-9a-f]{64}', entry['text_sha256']):
                raise ValueError
            if status in ('sending', 'uncertain'):
                if set(entry) != {'revision', 'text_sha256', 'status'}:
                    raise ValueError
            elif status == 'sent':
                mid = entry.get('message_id')
                if set(entry) != {'revision', 'text_sha256', 'status', 'message_id'} or \
                        not isinstance(mid, int) or isinstance(mid, bool) or mid <= 0:
                    raise ValueError
            elif status == 'rejected':
                code = entry.get('error_code')
                if set(entry) != {'revision', 'text_sha256', 'status', 'error_code'} or \
                        not isinstance(code, int) or isinstance(code, bool) or not 400 <= code <= 499:
                    raise ValueError
            else:
                raise ValueError
        return data
    except Exception:
        raise RuntimeError('Invalid announcement state') from None


def announce_release(state_dir: Path, release: dict, receipt: dict, transport,
                     channel: str = CHANNEL) -> dict:
    """Send once after reserving durably; ambiguous outcomes require review."""
    rid, revision, text, digest = _validate(release, receipt, channel)
    try:
        directory = _ensure_state_directory(Path(state_dir))
    except RuntimeError:
        raise
    except OSError:
        raise RuntimeError('Announcement state operation failed') from None
    os.chmod(directory, 0o700)
    path = directory / 'release_announcements.json'
    lock_path = directory / 'release_announcements.lock'
    if path.is_symlink() or lock_path.is_symlink():
        raise RuntimeError('Invalid announcement state')
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.fchmod(fd, 0o600)
    except OSError:
        raise RuntimeError('Invalid announcement state') from None
    try:
        with os.fdopen(fd, 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ledger = _read(path)
            key = CHANNEL + ':' + rid
            entry = ledger['entries'].get(key)
            if entry:
                if entry.get('revision') != revision or entry.get('text_sha256') != digest:
                    raise ValueError('Release announcement conflict')
                if entry.get('status') == 'sent':
                    return {'status': 'already_sent', 'message_id': entry['message_id']}
                if entry.get('status') == 'sending':
                    entry['status'] = 'uncertain'
                    _write(path, ledger)
                    return {'status': 'uncertain'}
                if entry.get('status') == 'uncertain':
                    return {'status': 'uncertain'}
                if entry.get('status') != 'rejected':
                    raise RuntimeError('Invalid announcement state')
            entry = {'revision': revision, 'text_sha256': digest, 'status': 'sending'}
            ledger['entries'][key] = entry
            _write(path, ledger)
            # BaseException deliberately leaves durable 'sending' for restart recovery.
            try:
                response = transport(CHANNEL, text)
            except Exception:
                entry['status'] = 'uncertain'
                _write(path, ledger)
                return {'status': 'uncertain'}
            except BaseException:
                raise
            if isinstance(response, dict) and response.get('ok') is True:
                result = response.get('result')
                mid = result.get('message_id') if isinstance(result, dict) else None
                chat = result.get('chat') if isinstance(result, dict) else None
                if isinstance(mid, int) and not isinstance(mid, bool) and mid > 0 and \
                        isinstance(chat, dict) and chat.get('id') is not None:
                    entry.update(status='sent', message_id=mid)
                    _write(path, ledger)
                    return {'status': 'sent', 'message_id': mid}
            if isinstance(response, dict) and response.get('ok') is False:
                code = response.get('error_code')
                if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 499:
                    entry.update(status='rejected', error_code=code)
                    _write(path, ledger)
                    return {'status': 'rejected', 'error_code': code}
            entry['status'] = 'uncertain'
            _write(path, ledger)
            return {'status': 'uncertain'}
    except (ValueError, RuntimeError):
        raise
    except Exception:
        raise RuntimeError('Announcement state operation failed') from None
