"""Configuration and startup checks never need a real token or provider."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from adapters.google_calendar_adapter import GoogleCalendarAdapter
from scripts.closed_beta_bot import configuration, instance_lock


def environment(tmp_path):
    token = tmp_path / 'synthetic-token.json'
    token.write_text('{}')
    token.chmod(0o600)
    return {'TELEGRAM_BOT_TOKEN': 'synthetic-not-a-secret', 'TELEGRAM_CHAT_ID': '101',
            'PERSONAL_OS_DATA_DIR': str(tmp_path / 'state'),
            'GOOGLE_CALENDAR_TOKEN_PATH': str(token)}


def test_configuration_requires_explicit_private_paths_and_chat(tmp_path):
    env = environment(tmp_path)
    assert configuration(env)[1] == '101'
    for changes in ({'TELEGRAM_CHAT_ID': '-101'}, {'TELEGRAM_CHAT_ID': '101,202'},
                    {'PERSONAL_OS_DATA_DIR': ''}, {'PERSONAL_OS_DATA_DIR': 'data'},
                    {'GOOGLE_CALENDAR_TOKEN_PATH': str(tmp_path / 'missing')}):
        with pytest.raises(ValueError):
            configuration(env | changes)
    Path(env['GOOGLE_CALENDAR_TOKEN_PATH']).chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        configuration(env)


def test_token_cannot_be_inside_state_and_instance_is_exclusive(tmp_path):
    env = environment(tmp_path)
    with pytest.raises(ValueError, match='outside state'):
        configuration(env | {'PERSONAL_OS_DATA_DIR': str(tmp_path)})
    root = tmp_path / 'state'
    with instance_lock(root):
        with pytest.raises(ValueError, match='Another process'):
            with instance_lock(root):
                pass
    with instance_lock(root):
        pass


def test_read_only_google_initialization_cannot_create_calendar(monkeypatch):
    adapter = GoogleCalendarAdapter({'initialize_calendar': False, 'allow_interactive_auth': False})
    monkeypatch.setattr(adapter, '_get_calendar_service', Mock(return_value=object()))
    creation = Mock(side_effect=AssertionError('No Calendar writes before approval'))
    monkeypatch.setattr(adapter, '_get_or_create_calendar', creation)
    assert asyncio.run(adapter.initialize())
    assert adapter.is_initialized
    creation.assert_not_called()


def test_pilot_google_auth_never_opens_interactive_flow(tmp_path, monkeypatch):
    adapter = GoogleCalendarAdapter({'allow_interactive_auth': False,
                                    'token_path': str(tmp_path / 'missing')})
    browser = Mock(side_effect=AssertionError('No browser in bot process'))
    monkeypatch.setattr('adapters.google_calendar_adapter.InstalledAppFlow.from_client_secrets_file', browser)
    with pytest.raises(RuntimeError, match='Operator authorization'):
        adapter._get_calendar_service()
    browser.assert_not_called()


def test_pilot_import_never_opens_legacy_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-c', '''
import sqlite3
sqlite3.connect = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('database access'))
from services.closed_beta_runtime import ClosedBetaApplication
print('IMPORT OK')
'''], cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(root), 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'IMPORT OK' in result.stdout
    assert not list(tmp_path.iterdir())


def test_check_does_not_claim_provider_readiness_or_leak_values(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = environment(tmp_path)
    result = subprocess.run([sys.executable, str(root / 'scripts/closed_beta_bot.py'), '--check'],
        env={**env, 'PATH': os.environ.get('PATH', ''), 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True)
    assert result.returncode == 0
    assert 'NOT verified' in result.stdout
    assert 'synthetic-not-a-secret' not in result.stdout + result.stderr
    assert not Path(env['PERSONAL_OS_DATA_DIR']).exists()


def test_real_entrypoint_binds_restricted_transport_without_legacy_runtime(tmp_path, monkeypatch):
    from scripts import closed_beta_bot
    from services.closed_beta_runtime import PilotBot
    env = environment(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['closed_beta_bot'])
    monkeypatch.setattr(os, 'environ', env)
    observed = []
    def run(bot):
        assert bot.private_owner_only
        assert bot.scheduled_tick is None
        assert bot.application.adapter.config['initialize_calendar'] is False
        assert bot.application.adapter.config['allow_interactive_auth'] is False
        observed.append(bot.handle_text('101', '/start')['step'])
        assert bot.handle_text('202', '/start') is None
    monkeypatch.setattr(PilotBot, 'run_forever', run)
    previous_umask = os.umask(0o077)
    try:
        assert closed_beta_bot.main() == 0
    finally:
        os.umask(previous_umask)
    assert observed == ['timezone']
