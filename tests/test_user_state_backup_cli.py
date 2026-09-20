import shutil
import subprocess
import sys


SCRIPT = 'scripts/user_state_backup.py'


def test_backup_cli_round_trip_is_allowlisted_and_quiet(tmp_path):
    data_dir = tmp_path / 'data'
    user_id = 'user-0123456789abcdef01234567'
    user_dir = data_dir / 'users' / user_id
    user_dir.mkdir(parents=True)
    (user_dir / 'planning_profile.json').write_text('{"version": 1}', encoding='utf-8')
    (user_dir / 'google-token.json').write_text('TOP_SECRET', encoding='utf-8')
    archive = tmp_path / 'backup.zip'

    backup = subprocess.run(
        [sys.executable, SCRIPT, '--data-dir', str(data_dir), '--archive', str(archive),
         'backup', user_id],
        text=True, capture_output=True, check=False,
    )
    assert backup.returncode == 0
    assert 'BACKUP OK: 1 state files' in backup.stdout
    assert 'TOP_SECRET' not in backup.stdout + backup.stderr

    shutil.rmtree(user_dir)
    restore = subprocess.run(
        [sys.executable, SCRIPT, '--data-dir', str(data_dir), '--archive', str(archive),
         'restore', user_id],
        text=True, capture_output=True, check=False,
    )
    assert restore.returncode == 0
    assert 'RESTORE OK: 1 state files' in restore.stdout
    assert (user_dir / 'planning_profile.json').read_text(encoding='utf-8') == '{"version": 1}'
    assert not (user_dir / 'google-token.json').exists()


def test_restore_cli_refuses_implicit_overwrite_without_exposing_state(tmp_path):
    data_dir = tmp_path / 'data'
    user_id = 'user-0123456789abcdef01234567'
    user_dir = data_dir / 'users' / user_id
    user_dir.mkdir(parents=True)
    profile = user_dir / 'planning_profile.json'
    profile.write_text('{"private": "VALUE"}', encoding='utf-8')
    archive = tmp_path / 'backup.zip'
    subprocess.run(
        [sys.executable, SCRIPT, '--data-dir', str(data_dir), '--archive', str(archive),
         'backup', user_id],
        text=True, capture_output=True, check=True,
    )

    result = subprocess.run(
        [sys.executable, SCRIPT, '--data-dir', str(data_dir), '--archive', str(archive),
         'restore', user_id],
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 1
    assert 'RESTORE FAILED: existing state would be overwritten' in result.stderr
    assert 'VALUE' not in result.stdout + result.stderr
