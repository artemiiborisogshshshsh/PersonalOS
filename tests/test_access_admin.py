"""Local operator CLI and beta UI behavior; no network or live data."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest

from services.access_billing import AccessBillingStore
from tests.test_access_billing import NOW, old_ledger


SCRIPT = str(Path(__file__).resolve().parents[1] / 'scripts' / 'access_admin.py')
USER = 'user-0123456789abcdef01234567'


def cli(path, *arguments):
    return subprocess.run([sys.executable, SCRIPT, '--ledger', str(path), *arguments],
                          text=True, capture_output=True, check=False)


def extension_arguments(user=USER, *, days='21', request='operator-request', reason='Free beta continuation'):
    return ('extend', user, '--days', days, '--request-id', request, '--reason', reason)


@pytest.fixture
def ledger(tmp_path):
    store = AccessBillingStore(tmp_path / 'ledger.sqlite3')
    store.ensure_user(USER, NOW)
    return store


def test_cli_preview_and_status_are_read_only_and_apply_is_explicit(ledger):
    original = ledger.path.read_bytes()
    status = cli(ledger.path, 'status', USER)
    assert status.returncode == 0
    assert json.loads(status.stdout)['mode'] == 'read_only'
    preview = cli(ledger.path, *extension_arguments())
    assert preview.returncode == 0
    payload = json.loads(preview.stdout)
    assert payload['mode'] == 'preview'
    assert payload['extension']['user_id'] == USER
    assert payload['extension']['days'] == 21
    assert ledger.path.read_bytes() == original
    assert ledger.export_user(USER, NOW)['beta_extensions'] == []
    applied = cli(ledger.path, *extension_arguments(), '--apply')
    assert applied.returncode == 0
    payload = json.loads(applied.stdout)
    assert payload['mode'] == 'applied'
    assert not payload['extension']['duplicate']
    assert payload['status']['beta_active']
    assert payload['status']['paid_through'] is None
    assert payload['status']['first_payment_at'] is None
    assert payload['status']['bonus_points'] == 0
    expires = datetime.fromisoformat(payload['extension']['beta_through'])
    assert expires > datetime.now(timezone.utc) + timedelta(days=20)


def test_cli_duplicate_exact_retry_and_mismatched_reuse(ledger):
    first = cli(ledger.path, *extension_arguments(), '--apply')
    assert first.returncode == 0
    first = json.loads(first.stdout)['extension']
    retry = cli(ledger.path, *extension_arguments(), '--apply')
    assert retry.returncode == 0
    retried = json.loads(retry.stdout)['extension']
    assert retried['duplicate']
    assert retried['beta_through'] == first['beta_through']
    assert retried['granted_at'] == first['granted_at']
    for arguments in [extension_arguments(days='22'), extension_arguments(reason='Changed reason')]:
        mismatch = cli(ledger.path, *arguments, '--apply')
        assert mismatch.returncode == 1
        assert mismatch.stdout == '' and 'ACCESS ADMIN FAILED' in mismatch.stderr
    assert len(ledger.export_user(USER, NOW)['beta_extensions']) == 1


def test_cli_expired_renewal_starts_from_granted_time_without_trial_reset(ledger):
    original_trial = ledger.start_trial(USER, datetime.now(timezone.utc) - timedelta(days=100))
    result = cli(ledger.path, *extension_arguments(), '--apply')
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    extension = payload['extension']
    assert datetime.fromisoformat(extension['beta_through']) == datetime.fromisoformat(extension['granted_at']) + timedelta(days=21)
    assert payload['status']['trial_started_at'] == original_trial['trial_started_at']
    assert payload['status']['trial_ends_at'] == original_trial['trial_ends_at']
    assert payload['status']['access_allowed']


def test_cli_storage_failure_rolls_back_audit_and_does_not_echo_reason(ledger):
    before = ledger.export_user(USER, NOW)
    with sqlite3.connect(ledger.path) as db:
        db.execute("""CREATE TRIGGER fail_cli_beta BEFORE UPDATE OF beta_through ON access_users
                      BEGIN SELECT RAISE(ABORT, 'private storage detail'); END""")
    result = cli(ledger.path, *extension_arguments(reason='private administrative reason'), '--apply')
    assert result.returncode == 1
    assert result.stdout == ''
    assert 'private' not in result.stderr
    assert ledger.export_user(USER, NOW) == before


def test_cli_missing_ledger_unknown_user_and_invalid_days_never_create(ledger, tmp_path):
    missing = tmp_path / 'does-not-exist.sqlite3'
    result = cli(missing, *extension_arguments(), '--apply')
    assert result.returncode == 1 and not missing.exists()
    original = ledger.path.read_bytes()
    for args in [('status', 'opaque-typo'), extension_arguments('opaque-typo') + ('--apply',),
                 extension_arguments(days='0'), extension_arguments(days='366') + ('--apply',),
                 extension_arguments(reason='')]:
        result = cli(ledger.path, *args)
        assert result.returncode == 1
    assert ledger.path.read_bytes() == original
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT count(*) FROM access_users').fetchone()[0] == 1


def test_cli_rejects_symlink_and_insecure_file_without_changing_permissions(ledger, tmp_path):
    link = tmp_path / 'ledger-link.sqlite3'
    link.symlink_to(ledger.path)
    assert cli(link, 'status', USER).returncode == 1
    ledger.path.chmod(0o644)
    assert cli(ledger.path, 'status', USER).returncode == 1
    assert ledger.path.stat().st_mode & 0o777 == 0o644


def test_cli_old_schema_preview_has_no_migration_then_apply_migrates(tmp_path):
    path = tmp_path / 'previous.sqlite3'
    old_ledger(path)
    original = path.read_bytes()
    result = cli(path, *extension_arguments('old'))
    assert result.returncode == 0
    assert path.read_bytes() == original
    result = cli(path, *extension_arguments('old'), '--apply')
    assert result.returncode == 0
    status = json.loads(result.stdout)['status']
    assert status['bonus_points'] == 25
    assert status['first_payment_at'] == NOW.isoformat()
    assert status['trial_ends_at'] == (NOW + timedelta(days=21)).isoformat()


def test_beta_extension_status_label_warning_suppression_and_resume_barrier(tmp_path, monkeypatch):
    from tests.test_access_runtime import expire, publish
    from tests.test_closed_beta_runtime import application, NOW as RUNTIME_NOW, confirmation
    app = application(tmp_path)
    publish(app)
    expire(app)
    assert app.access._state()['resume_required']
    app.access.store.grant_beta_extension(app.account.id, 14, 'operator-free-beta', 'Free beta continuation', app.now())
    status_text = app.handle_text('101', '/access')['text']
    assert 'Бесплатное продление беты до:' in status_text
    assert 'Оплачено до:' not in status_text
    assert 'новый /weekly_preview' in status_text
    send = Mock()
    assert app.access.allowed()
    assert not app.access.background_allowed(send)
    send.assert_not_called()
    monkeypatch.setattr(app, '_preview_permitted', lambda: app.reply('Этот план уже опубликован. Изменений и повторных записей нет.'))
    fresh = app.preview()
    token = confirmation(fresh).removeprefix('pilot:apply:')
    assert 'возобновлены' in app.apply(token)['text']
    assert app.access.background_allowed(send)
    send.assert_not_called()
    assert app.access.store.status(app.account.id, now=RUNTIME_NOW)['paid_through'] is None


def test_active_beta_suppresses_ending_trial_warnings_before_trial_expiry(tmp_path):
    from tests.test_access_runtime import publish
    from tests.test_closed_beta_runtime import application, NOW as RUNTIME_NOW
    app = application(tmp_path)
    publish(app)
    app.access.store.grant_beta_extension(app.account.id, 10, 'operator-beta', 'Free beta', app.now())
    app.now = lambda: RUNTIME_NOW + timedelta(days=20)
    send = Mock()
    assert app.access.background_allowed(send)
    send.assert_not_called()
