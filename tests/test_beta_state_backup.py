"""Whole-root restore rehearsals use only synthetic local state and ledger data."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import fcntl
import json
from pathlib import Path
import sqlite3
import stat
from zipfile import ZipFile, ZipInfo

import pytest

from scripts.beta_state_backup import main
from services.access_billing import AccessBillingStore
from services.beta_state_backup import BetaStateBackupService


NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def synthetic_state(root):
    store = AccessBillingStore(root / 'access_billing.sqlite3')
    store.start_trial('inviter', NOW)
    store.bind_referral('friend', store.referral_code('inviter'), NOW)
    quote = store.quote('friend', 10000, 'RUB', 30, NOW)
    payment = dict(provider='synthetic', payment_id='payment-one', quote_id=quote['quote_id'],
                   amount_minor=quote['amount_minor'], currency=quote['currency'], now=NOW)
    store.record_verified_payment('friend', **payment)
    store.grant_beta_extension('inviter', 30, 'beta-extension-once', 'synthetic rehearsal', NOW)
    # Generic SQLite snapshots must preserve tables unknown to this service.
    with sqlite3.connect(store.path) as db:
        db.execute('CREATE TABLE future_domain (operation_id TEXT PRIMARY KEY, value TEXT)')
        db.execute('INSERT INTO future_domain VALUES (?, ?)', ('once', 'preserved'))
    payload = {
        'polling_inbox.json': b'{"offset":123,"pending":[{"update_id":122}]}\n',
        'polling_offset.json': b'123\n',
        'user-example/access.json': b'{"enabled":true,"generation":"same"}',
        'user-example/last_update.json': b'122',
        'user-example/interrupted_update.json': b'{"update_id":122}',
        'user-example/state/users_registry.json': b'{"users":{}}',
        'user-example/state/users/user-friend/access_runtime.json':
            b'{"resume_required":true,"warnings":["paid:soon"],"opaque":"\\u041f"}\n',
        'user-example/state/users/user-friend/update_all.json': b'{"phase":"pending","event_id":"same"}',
        'user-example/state/users/user-friend/class-calendar-recovery.json': b'{"phase":"ambiguous"}',
        'release_delivery.json': b'{"sent":["same"]}',
    }
    for name, data in payload.items():
        write(root, name, data)
    return store, payment, payload


def test_full_restore_preserves_ledger_idempotency_recovery_and_private_permissions(tmp_path):
    root, archive, restored = tmp_path / 'state', tmp_path / 'state.zip', tmp_path / 'restored'
    store, payment, payload = synthetic_state(root)
    before = {user: store.export_user(user, NOW) for user in ('inviter', 'friend')}
    service = BetaStateBackupService()
    manifest = service.backup(root, archive)
    result = service.restore(archive, restored)
    assert set(manifest['files']) == {'access_billing.sqlite3', *payload}
    assert set(result['restored_files']) == set(manifest['files'])
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    for name, data in payload.items():
        assert (restored / name).read_bytes() == data
    for path in [restored, *restored.rglob('*')]:
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    recovered = AccessBillingStore(restored / 'access_billing.sqlite3')
    assert {user: recovered.export_user(user, NOW) for user in before} == before
    assert recovered.record_verified_payment('friend', **payment)['duplicate'] is True
    assert recovered.grant_beta_extension('inviter', 30, 'beta-extension-once', 'synthetic rehearsal', NOW)['duplicate'] is True
    assert recovered.export_user('inviter', NOW) == before['inviter']
    assert recovered.start_trial('inviter', NOW + timedelta(days=400))['trial_ends_at'] == before['inviter']['status']['trial_ends_at']
    assert recovered.status('inviter', NOW)['bonus_points'] == 25
    assert recovered.status('friend', NOW)['bonus_points'] == 10
    with sqlite3.connect(recovered.path) as db:
        assert db.execute('SELECT * FROM future_domain').fetchall() == [('once', 'preserved')]


def test_sqlite_snapshot_includes_committed_wal_without_archiving_sidecars(tmp_path):
    root = tmp_path / 'state'
    root.mkdir()
    with sqlite3.connect(root / 'future.db') as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE events (value TEXT)')
        db.execute("INSERT INTO events VALUES ('committed')")
        db.commit()
        service = BetaStateBackupService()
        manifest = service.backup(root, tmp_path / 'wal.zip')
        assert set(manifest['files']) == {'future.db'}
        service.restore(tmp_path / 'wal.zip', tmp_path / 'restored')
    with sqlite3.connect(tmp_path / 'restored/future.db') as restored:
        assert restored.execute('SELECT * FROM events').fetchall() == [('committed',)]


def test_running_bot_lock_prevents_backup(tmp_path):
    root = tmp_path / 'state'
    root.mkdir()
    with (root / '.pilot.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='Stop the bot'):
            BetaStateBackupService().backup(root, tmp_path / 'blocked.zip')
    assert not (tmp_path / 'blocked.zip').exists()


@pytest.mark.parametrize('name', ['token.json', 'google/user.json', '.env', 'operator-keyfile.json', 'credentials.json', 'private.pem', 'operator-key.json'])
def test_misplaced_credentials_rejected_without_archive(tmp_path, name):
    root = tmp_path / 'state'
    write(root, name, b'synthetic-secret')
    with pytest.raises(ValueError, match='Credentials'):
        BetaStateBackupService().backup(root, tmp_path / 'blocked.zip')
    assert not (tmp_path / 'blocked.zip').exists()


def test_oauth_pending_locks_temporary_files_and_caches_excluded(tmp_path):
    root = tmp_path / 'state'
    write(root, 'polling_inbox.json', b'{}')
    for name in ['google_oauth_pending/pending.json', 'cache/image.bin', '__pycache__/x.pyc',
                 'user-one/.access.lock', '.polling_inbox.json.abcdef', 'state.tmp', 'tmpa12bc34d']:
        write(root, name, b'not-operational')
    manifest = BetaStateBackupService().backup(root, tmp_path / 'state.zip')
    assert set(manifest['files']) == {'polling_inbox.json'}


def test_source_and_destination_symlinks_rejected(tmp_path):
    root = tmp_path / 'state'
    write(root, 'state.json', b'{}')
    (root / 'link').symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(ValueError):
        BetaStateBackupService().backup(root, tmp_path / 'bad.zip')
    (root / 'link').unlink()
    service = BetaStateBackupService()
    service.backup(root, tmp_path / 'state.zip')
    (tmp_path / 'link').symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        service.restore(tmp_path / 'state.zip', tmp_path / 'link/restored')


def test_existing_archive_and_restore_destination_never_overwritten(tmp_path):
    root = tmp_path / 'state'
    write(root, 'state.json', b'original')
    service = BetaStateBackupService()
    archive = tmp_path / 'state.zip'
    service.backup(root, archive)
    previous = archive.read_bytes()
    with pytest.raises(FileExistsError):
        service.backup(root, archive)
    assert archive.read_bytes() == previous
    for destination in (root, tmp_path / 'empty'):
        destination.mkdir(exist_ok=True)
        with pytest.raises(FileExistsError):
            service.restore(archive, destination)
    assert (root / 'state.json').read_bytes() == b'original'


def make_archive(path, files=None, *, extra=None, manifest=None):
    files = {'state.json': b'{}'} if files is None else files
    if manifest is None:
        manifest = {'version': 1, 'kind': 'invited-beta-state', 'files': {
            name: {'size': len(data), 'sha256': sha256(data).hexdigest()} for name, data in files.items()}}
    with ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        for name, data in files.items():
            archive.writestr('state/' + name, data)
        if extra:
            archive.writestr(*extra)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'dir/../../escape', 'dir\\escape', 'x//y', './state.json', 'token.json'])
def test_malicious_state_paths_rejected_before_destination_exists(tmp_path, name):
    archive = tmp_path / 'bad.zip'
    make_archive(archive, {name: b'{}'})
    with pytest.raises(ValueError):
        BetaStateBackupService().restore(archive, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('fault', ['extra', 'duplicate', 'symlink', 'checksum', 'version', 'oversize', 'missing', 'manifest-duplicate'])
def test_invalid_archives_rejected_before_any_destination_write(tmp_path, fault):
    archive = tmp_path / 'bad.zip'
    manifest = {'version': 1, 'kind': 'invited-beta-state', 'files': {
        'state.json': {'size': 2, 'sha256': sha256(b'{}').hexdigest()}}}
    extra = None
    files = {'state.json': b'{}'}
    if fault == 'extra':
        extra = ('state/unmanifested.json', b'{}')
    if fault == 'duplicate':
        extra = ('state/state.json', b'{}')
    if fault == 'symlink':
        link = ZipInfo('state/state.json')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        files = {}
        extra = (link, b'{}')
    if fault == 'checksum':
        files = {'state.json': b'[]'}
    if fault == 'version':
        manifest['version'] = 2
    if fault == 'oversize':
        manifest['files']['state.json']['size'] = BetaStateBackupService.MAX_FILE_BYTES + 1
    if fault == 'missing':
        files = {}
    if fault == 'duplicate':
        with pytest.warns(UserWarning, match='Duplicate name'):
            make_archive(archive, files, extra=extra, manifest=manifest)
    else:
        make_archive(archive, files, extra=extra, manifest=manifest)
    if fault == 'manifest-duplicate':
        with ZipFile(archive, 'w') as output:
            output.writestr('manifest.json', '{"version":1,"version":1,"kind":"invited-beta-state","files":{}}')
    with pytest.raises(ValueError):
        BetaStateBackupService().restore(archive, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


def test_size_limits_and_path_collisions_rejected_before_destination_write(tmp_path, monkeypatch):
    service = BetaStateBackupService()
    for index, files in enumerate([{'large.json': b'123'}, {'parent': b'', 'parent/child': b''}]):
        archive = tmp_path / f'bad-{index}.zip'
        make_archive(archive, files)
        monkeypatch.setattr(service, 'MAX_FILE_BYTES', 2)
        with pytest.raises(ValueError):
            service.restore(archive, tmp_path / 'restored')
        assert not (tmp_path / 'restored').exists()


def test_archive_publication_race_keeps_existing_file(tmp_path, monkeypatch):
    import os
    root = tmp_path / 'state'
    write(root, 'state.json', b'{}')
    archive = tmp_path / 'state.zip'
    original_link = os.link
    def racing_link(source, target):
        target.write_bytes(b'existing')
        return original_link(source, target)
    monkeypatch.setattr('services.beta_state_backup.os.link', racing_link)
    with pytest.raises(FileExistsError):
        BetaStateBackupService().backup(root, archive)
    assert archive.read_bytes() == b'existing'
    assert not list(tmp_path.glob('.beta-backup-*'))


def test_detect_changed_inventory_before_publishing(tmp_path, monkeypatch):
    root = tmp_path / 'state'
    write(root, 'state.json', b'{}')
    service = BetaStateBackupService()
    original = service._inventory
    calls = 0
    def changed(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            write(root, 'new.json', b'{}')
        return original(path)
    monkeypatch.setattr(service, '_inventory', changed)
    with pytest.raises(ValueError, match='changed'):
        service.backup(root, tmp_path / 'state.zip')
    assert not (tmp_path / 'state.zip').exists()


def test_cli_local_rehearsal_and_categorical_errors(tmp_path, capsys):
    root = tmp_path / 'state'
    write(root, 'state.json', b'{}')
    archive = tmp_path / 'state.zip'
    assert main(['backup', '--state-dir', str(root), '--archive', str(archive)]) == 0
    assert main(['restore', '--archive', str(archive), '--destination', str(tmp_path / 'restored')]) == 0
    assert main(['restore', '--archive', str(archive), '--destination', str(root)]) == 1
    output = capsys.readouterr()
    assert 'BACKUP OK' in output.out and 'RESTORE OK' in output.out
    assert str(root) not in output.err and 'FAILED' in output.err


def test_cli_rejects_unsupported_zip_compression_without_traceback_or_destination(tmp_path, capsys):
    archive = tmp_path / 'unsupported.zip'
    make_archive(archive)
    data = bytearray(archive.read_bytes())
    # Patch both local and central headers to a method Python cannot decode.
    for signature, offset in ((b'PK\x03\x04', 8), (b'PK\x01\x02', 10)):
        start = 0
        while (position := data.find(signature, start)) != -1:
            data[position + offset:position + offset + 2] = (99).to_bytes(2, 'little')
            start = position + len(signature)
    archive.write_bytes(data)
    destination = tmp_path / 'restored'
    assert main(['restore', '--archive', str(archive), '--destination', str(destination)]) == 1
    output = capsys.readouterr()
    assert 'RESTORE FAILED' in output.err and 'Traceback' not in output.err
    assert str(archive) not in output.err and not destination.exists()
