import pytest
from services.user_state_backup import UserStateBackupService


def test_backup_restore_is_allowlisted_and_verified(tmp_path):
    service = UserStateBackupService(tmp_path)
    source = tmp_path / 'users' / 'user-abc'; source.mkdir(parents=True)
    (source / 'planning_profile.json').write_text('{"version": 1}', encoding='utf-8')
    (source / 'token.json').write_text('secret', encoding='utf-8')
    archive = tmp_path / 'backup.zip'
    manifest = service.backup('user-abc', archive)
    assert 'token.json' not in manifest['files']
    service.lifecycle.delete('user-abc', confirmed=True)
    restored = service.restore('user-abc', archive)
    assert restored['restored_files'] == ['planning_profile.json']
    assert not (tmp_path / 'users' / 'user-abc' / 'token.json').exists()


def test_restore_refuses_wrong_user_or_overwrite(tmp_path):
    service = UserStateBackupService(tmp_path)
    directory = tmp_path / 'users' / 'user-a'; directory.mkdir(parents=True)
    (directory / 'planning_profile.json').write_text('{}')
    archive = tmp_path / 'backup.zip'; service.backup('user-a', archive)
    with pytest.raises(ValueError, match='does not belong'):
        service.restore('user-b', archive)
    with pytest.raises(FileExistsError, match='overwrite'):
        service.restore('user-a', archive)


def test_restore_never_follows_broken_state_symlink(tmp_path):
    service = UserStateBackupService(tmp_path)
    directory = tmp_path / 'users' / 'user-a'
    directory.mkdir(parents=True)
    state = directory / 'planning_profile.json'
    state.write_text('{}', encoding='utf-8')
    archive = tmp_path / 'backup.zip'
    service.backup('user-a', archive)

    state.unlink()
    external = tmp_path / 'outside.json'
    state.symlink_to(external)

    with pytest.raises(ValueError, match='symbolic link'):
        service.restore('user-a', archive)
    assert not external.exists()
