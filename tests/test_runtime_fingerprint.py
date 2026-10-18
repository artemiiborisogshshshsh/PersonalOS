from pathlib import Path

from services.google_oauth_web import RUNTIME_FINGERPRINT_FILES, runtime_code_fingerprint


def _write_allowlisted_tree(root: Path):
    for relative_path in RUNTIME_FINGERPRINT_FILES:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('source:' + relative_path).encode('utf-8'))


def test_runtime_fingerprint_is_deterministic_and_tracks_allowlisted_code(tmp_path):
    assert 'services/published_plan_updates.py' in RUNTIME_FINGERPRINT_FILES
    _write_allowlisted_tree(tmp_path)

    first = runtime_code_fingerprint(tmp_path)
    second = runtime_code_fingerprint(tmp_path)
    assert first == second
    assert len(first) == 16
    assert all(character in '0123456789abcdef' for character in first)

    changed = tmp_path / RUNTIME_FINGERPRINT_FILES[0]
    changed.write_bytes(b'changed critical runtime code')
    assert runtime_code_fingerprint(tmp_path) != first


def test_runtime_fingerprint_ignores_unlisted_state_and_tokens(tmp_path):
    _write_allowlisted_tree(tmp_path)
    before = runtime_code_fingerprint(tmp_path)

    for relative_path, content in (
        ('state/users_registry.json', b'private user state'),
        ('google/token.json', b'private Google token'),
    ):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    assert runtime_code_fingerprint(tmp_path) == before


def test_runtime_fingerprint_missing_file_returns_safe_marker(tmp_path):
    _write_allowlisted_tree(tmp_path)
    (tmp_path / RUNTIME_FINGERPRINT_FILES[-1]).unlink()

    result = runtime_code_fingerprint(tmp_path)

    assert result == 'unavailable'
    assert str(tmp_path) not in result
