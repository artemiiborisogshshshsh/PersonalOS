"""Offline, operator-only snapshots of the entire invited-bot state root.

This deliberately does not use the per-user privacy-export allowlist. No domain
objects are instantiated during restore, so recovery and access barriers survive.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
from tempfile import NamedTemporaryFile, TemporaryDirectory
import time
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


class BetaStateBackupService:
    MANIFEST = 'manifest.json'
    VERSION = 1
    MAX_FILES = 10000
    MAX_FILE_BYTES = 256 * 1024 * 1024
    MAX_TOTAL_BYTES = 1024 * 1024 * 1024
    MAX_MANIFEST_BYTES = 4 * 1024 * 1024
    EXCLUDED_DIRECTORIES = frozenset({'google_oauth_pending', '__pycache__', '.pytest_cache', 'cache', 'caches'})

    @staticmethod
    def _private_path(path):
        path = Path(path).absolute()
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ValueError('Symbolic links are not supported')
        return path

    @staticmethod
    def _safe_name(name):
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                or any(ord(char) < 32 for char in name)
                or PurePosixPath(name).is_absolute()
                or any(part in {'', '.', '..'} for part in name.split('/'))):
            raise ValueError('Invalid state path')
        return name

    @classmethod
    def _excluded(cls, name):
        parts = PurePosixPath(cls._safe_name(name)).parts
        for part in parts:
            lower = part.lower()
            # Reject misplaced credentials even if they look like temporary files.
            if (lower == '.env' or lower.startswith('.env.')
                    or any(word in lower for word in ('credential', 'secret', 'keyfile', 'access_token', 'refresh_token'))
                    or lower in {'.ssh', '.aws', '.netrc', 'id_rsa', 'id_ed25519'}
                    or re.search(r'(^|[_.-])(tokens?|keys?)([_.-]|$)', lower)
                    or lower.endswith(('.pem', '.key', '.p12', '.pfx'))
                    or (('google' in lower or 'oauth' in lower) and lower != 'google_oauth_pending')):
                raise ValueError('Credentials must be outside the state root')
        if any(part in cls.EXCLUDED_DIRECTORIES for part in parts):
            return True
        leaf = parts[-1]
        return (leaf.endswith(('.lock', '.tmp', '.pyc', '-wal', '-shm', '-journal'))
                or leaf.startswith('.') or bool(re.fullmatch(r'tmp[a-z0-9_]{8}', leaf)))

    @staticmethod
    def _fsync_directory(path):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _offline_lock(self, root):
        # Same top-level process lock as scripts.closed_beta_bot.instance_lock.
        descriptor = os.open(root / '.pilot.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'r+') as handle:
            os.fchmod(handle.fileno(), 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Stop the bot before taking a state backup') from None
            yield

    def _inventory(self, root):
        files = {}
        for directory, directories, names in os.walk(root, followlinks=False):
            parent = Path(directory)
            for child in list(directories):
                path = parent / child
                if path.is_symlink():
                    raise ValueError('Symbolic links are not supported')
                if self._excluded(path.relative_to(root).as_posix()):
                    directories.remove(child)
            for child in names:
                path = parent / child
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError('Only regular state files are supported')
                name = path.relative_to(root).as_posix()
                if self._excluded(name):
                    continue
                if metadata.st_size > self.MAX_FILE_BYTES:
                    raise ValueError('State file exceeds backup limit')
                files[name] = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        if len(files) > self.MAX_FILES or sum(value[2] for value in files.values()) > self.MAX_TOTAL_BYTES:
            raise ValueError('State exceeds backup limits')
        return files

    @staticmethod
    def _sqlite_snapshot(source, destination):
        deadline = time.monotonic() + 10
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise ValueError('Database remains busy; stop all writers')
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=1)) as original:
            with closing(sqlite3.connect(destination)) as snapshot:
                original.backup(snapshot, pages=128, progress=progress, sleep=0.01)
                if snapshot.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Database integrity check failed')

    def backup(self, state_dir: Path, archive_path: Path) -> dict:
        root = self._private_path(state_dir)
        archive_path = self._private_path(archive_path)
        if not root.is_dir():
            raise ValueError('Existing state root required')
        if archive_path == root or root in archive_path.parents:
            raise ValueError('Archive must be outside the state root')
        if archive_path.exists():
            raise FileExistsError('Archive already exists')
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with self._offline_lock(root), TemporaryDirectory(prefix='beta-snapshot-') as staging:
                before = self._inventory(root)
                manifest = {'version': self.VERSION, 'kind': 'invited-beta-state', 'files': {}}
                with NamedTemporaryFile(dir=archive_path.parent, prefix='.beta-backup-', delete=False) as output:
                    temporary = Path(output.name)
                    os.fchmod(output.fileno(), 0o600)
                total = 0
                with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
                    for index, name in enumerate(sorted(before)):
                        path = root / name
                        with path.open('rb') as source:
                            header = source.read(16)
                        is_database = header == b'SQLite format 3\x00' or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3'}
                        if is_database:
                            snapshot = Path(staging) / str(index)
                            self._sqlite_snapshot(path, snapshot)
                            data = snapshot.read_bytes()
                        else:
                            data = path.read_bytes()
                        total += len(data)
                        if len(data) > self.MAX_FILE_BYTES or total > self.MAX_TOTAL_BYTES:
                            raise ValueError('Snapshot exceeds backup limits')
                        manifest['files'][name] = {'sha256': sha256(data).hexdigest(), 'size': len(data)}
                        archive.writestr('state/' + name, data)
                    archive.writestr(self.MANIFEST, json.dumps(manifest, sort_keys=True))
                if before != self._inventory(root):
                    raise ValueError('State changed during backup; stop all writers')
                with temporary.open('rb') as completed:
                    os.fsync(completed.fileno())
                # Atomic publication without ever replacing an existing archive.
                os.link(temporary, archive_path)
                temporary.unlink()
                temporary = None
                self._fsync_directory(archive_path.parent)
                return manifest
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate manifest key')
            result[key] = value
        return result

    def _validate(self, archive, staging):
        entries = archive.infolist()
        if len(entries) > self.MAX_FILES + 1:
            raise ValueError('Too many archive entries')
        names = [entry.filename for entry in entries]
        if len(set(names)) != len(names) or self.MANIFEST not in names:
            raise ValueError('Duplicate entries or missing manifest')
        total = 0
        for entry in entries:
            self._safe_name(entry.filename)
            mode = entry.external_attr >> 16
            if (entry.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                    or entry.flag_bits & 1 or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}):
                raise ValueError('Unsupported archive entry')
            limit = self.MAX_MANIFEST_BYTES if entry.filename == self.MANIFEST else self.MAX_FILE_BYTES
            total += entry.file_size
            if entry.file_size > limit or total > self.MAX_TOTAL_BYTES:
                raise ValueError('Archive exceeds size limits')
        manifest = json.loads(archive.read(self.MANIFEST), object_pairs_hook=self._unique_object)
        if (not isinstance(manifest, dict) or type(manifest.get('version')) is not int
                or manifest.get('version') != self.VERSION
                or manifest.get('kind') != 'invited-beta-state' or not isinstance(manifest.get('files'), dict)):
            raise ValueError('Unsupported backup manifest')
        files = manifest['files']
        for name in files:
            self._safe_name(name)
            if any(parent.as_posix() in files for parent in PurePosixPath(name).parents
                   if parent != PurePosixPath('.')):
                raise ValueError('Conflicting state paths')
        if set(names) != {self.MANIFEST, *('state/' + name for name in files)}:
            raise ValueError('Archive and manifest inventory differ')
        for index, (name, record) in enumerate(files.items()):
            self._safe_name(name)
            if self._excluded(name) or not isinstance(record, dict):
                raise ValueError('Unsupported state entry')
            if (type(record.get('size')) is not int or not 0 <= record['size'] <= self.MAX_FILE_BYTES
                    or not isinstance(record.get('sha256'), str)
                    or not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
                raise ValueError('Invalid integrity record')
            entry = archive.getinfo('state/' + name)
            if entry.file_size != record['size']:
                raise ValueError('State size mismatch')
            digest = sha256()
            with archive.open(entry) as source, (staging / str(index)).open('xb') as output:
                os.fchmod(output.fileno(), 0o600)
                count = 0
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > record['size']:
                        raise ValueError('State size mismatch')
                    digest.update(chunk)
                    output.write(chunk)
            if count != record['size'] or digest.hexdigest() != record['sha256']:
                raise ValueError('State integrity check failed')
        return manifest

    def restore(self, archive_path: Path, destination: Path) -> dict:
        archive_path = self._private_path(archive_path)
        destination = self._private_path(destination)
        if destination.exists():
            raise FileExistsError('Restore requires a new destination')
        if not destination.parent.is_dir():
            raise ValueError('Restore destination parent must already exist')
        if archive_path.stat().st_size > self.MAX_TOTAL_BYTES + self.MAX_MANIFEST_BYTES + self.MAX_FILES * 1024:
            raise ValueError('Archive exceeds size limits')
        # Validate every byte in private staging before creating the destination.
        with TemporaryDirectory(prefix='.beta-restore-', dir=destination.parent) as temporary:
            staging = Path(temporary)
            with ZipFile(archive_path) as archive:
                manifest = self._validate(archive, staging)
            destination.mkdir(mode=0o700)
            try:
                for index, name in enumerate(manifest['files']):
                    target = destination / name
                    parent = destination
                    for part in PurePosixPath(name).parts[:-1]:
                        parent = parent / part
                        parent.mkdir(mode=0o700, exist_ok=True)
                    with (staging / str(index)).open('rb') as source, target.open('xb') as output:
                        os.fchmod(output.fileno(), 0o600)
                        shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                for directory, _, _ in os.walk(destination, topdown=False):
                    self._fsync_directory(directory)
                self._fsync_directory(destination.parent)
            except BaseException:
                shutil.rmtree(destination)
                raise
        return {'restored_files': sorted(manifest['files'])}
