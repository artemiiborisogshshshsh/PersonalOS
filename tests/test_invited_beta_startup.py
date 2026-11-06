import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import invited_beta_startup as startup


def valid():
    return {'user_id': 'user-' + 'a' * 24, 'expected_digest': 'b' * 64,
            'old_rows': 23, 'new_classes': 11, 'new_preparations': 1,
            'absent_preparations': 10}


def test_no_operator_file_leaves_normal_startup(tmp_path):
    assert startup.checkpoint_arguments(tmp_path / 'absent') is None


def test_operator_environment_uses_same_strict_configuration_without_file(tmp_path):
    args = startup.checkpoint_arguments(tmp_path / 'absent', json.dumps(valid()))
    assert '--apply' in args and '--expected-digest' in args
    with pytest.raises(ValueError):
        startup.checkpoint_arguments(tmp_path / 'absent', json.dumps({**valid(), 'extra': 'bad'}))
    with pytest.raises(ValueError):
        startup.checkpoint_arguments(tmp_path / 'absent', ' ' * 4097)


def test_encoded_environment_supports_forms_that_forbid_quotes(tmp_path):
    raw = json.dumps(valid())
    encoded = 'base64:' + base64.b64encode(raw.encode()).decode('ascii')
    assert startup.checkpoint_arguments(tmp_path / 'absent', encoded) == startup.checkpoint_arguments(tmp_path / 'absent', raw)


@pytest.mark.parametrize('encoded', ['base64:!!!', 'base64:bm90LWpzb24='])
def test_invalid_encoded_configuration_is_rejected(tmp_path, encoded):
    with pytest.raises(ValueError):
        startup.checkpoint_arguments(tmp_path / 'absent', encoded)


@pytest.mark.parametrize('change', [
    {'expected_digest': 'bad'}, {'user_id': '../../other'}, {'new_classes': True},
    {'old_rows': -1}, {'new_preparations': 10001}, {'extra': 'value'},
])
def test_rejects_invalid_operator_configuration(tmp_path, change):
    path = tmp_path / 'operator.json'
    path.write_text(json.dumps({**valid(), **change}))
    with pytest.raises(ValueError):
        startup.checkpoint_arguments(path)


def test_rejects_symlink(tmp_path):
    target = tmp_path / 'target'; target.write_text(json.dumps(valid()))
    link = tmp_path / 'link'; link.symlink_to(target)
    with pytest.raises(ValueError):
        startup.checkpoint_arguments(link)


def test_rejects_configuration_writable_by_other_users(tmp_path):
    path = tmp_path / 'operator.json'; path.write_text(json.dumps(valid()))
    path.chmod(0o666)
    with pytest.raises(ValueError):
        startup.checkpoint_arguments(path)


def test_starts_normal_bot_after_rejected_repair(tmp_path, monkeypatch):
    path = tmp_path / 'operator.json'; path.write_text(json.dumps(valid()))
    run, execute = Mock(return_value=SimpleNamespace(returncode=2)), Mock()
    monkeypatch.setattr(startup, 'OPERATOR_FILE', path)
    monkeypatch.setattr(startup.subprocess, 'run', run)
    monkeypatch.setattr(startup.os, 'execv', execute)
    startup.main()
    command = run.call_args.args[0]
    assert '--apply' in command and '--expected-digest' in command
    assert run.call_args.kwargs['timeout'] == 180
    assert execute.call_args.args[1][-1] == 'run'


def test_timeout_still_starts_normal_bot_without_error_payload(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'operator.json'; path.write_text(json.dumps(valid()))
    execute = Mock()
    monkeypatch.setattr(startup, 'OPERATOR_FILE', path)
    monkeypatch.setattr(startup.subprocess, 'run', Mock(side_effect=RuntimeError('secret-provider-message')))
    monkeypatch.setattr(startup.os, 'execv', execute)
    startup.main()
    execute.assert_called_once()
    assert 'secret-provider-message' not in capsys.readouterr().out
