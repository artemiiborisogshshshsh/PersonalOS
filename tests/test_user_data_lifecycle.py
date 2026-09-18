import json

import pytest

from services.user_data_lifecycle import UserDataLifecycleService


def test_export_and_explicit_user_data_deletion_are_scoped_to_one_user(tmp_path):
    first = tmp_path / 'users' / '42'
    second = tmp_path / 'users' / '99'
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / 'planning_profile.json').write_text('{"sleep":"23:00"}', encoding='utf-8')
    (second / 'planning_profile.json').write_text('{"keep":true}', encoding='utf-8')
    lifecycle = UserDataLifecycleService(tmp_path)

    exported = json.loads(lifecycle.export_json('42'))
    assert exported['files']['planning_profile.json'] == '{"sleep":"23:00"}'
    with pytest.raises(PermissionError):
        lifecycle.delete('42')
    lifecycle.delete('42', confirmed=True)

    assert not first.exists()
    assert second.exists()


def test_export_uses_an_allowlist_and_never_follows_secret_symlinks(tmp_path):
    directory = tmp_path / 'users' / '42'
    directory.mkdir(parents=True)
    (directory / 'planning_profile.json').write_text('{"sleep":"23:00"}', encoding='utf-8')
    (directory / 'google-token.json').write_text('TOP_SECRET', encoding='utf-8')
    (directory / 'private-note.txt').write_text('do not export', encoding='utf-8')
    secret = tmp_path / 'secret.json'
    secret.write_text('TOP_SECRET', encoding='utf-8')
    (directory / 'update_all.json').symlink_to(secret)

    exported = UserDataLifecycleService(tmp_path).export_json('42')

    assert 'planning_profile.json' in exported
    assert 'google-token.json' not in exported
    assert 'private-note.txt' not in exported
    assert 'TOP_SECRET' not in exported


def test_lifecycle_rejects_a_user_directory_symlink(tmp_path):
    root = tmp_path / 'users'
    root.mkdir()
    external = tmp_path / 'external'
    external.mkdir()
    (root / '42').symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match='escape'):
        UserDataLifecycleService(tmp_path).export('42')


def test_user_data_lifecycle_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        UserDataLifecycleService(tmp_path).export('../other-user')
