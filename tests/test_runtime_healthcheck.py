import os
import subprocess
import sys


SCRIPT = 'scripts/runtime_healthcheck.py'


def test_healthcheck_rejects_missing_telegram_configuration(tmp_path):
    env = {**os.environ, 'PERSONAL_OS_DATA_DIR': str(tmp_path)}
    env.pop('TELEGRAM_BOT_TOKEN', None)
    env.pop('TELEGRAM_CHAT_ID', None)

    result = subprocess.run(
        [sys.executable, SCRIPT], env=env, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 1
    assert 'TELEGRAM_BOT_TOKEN' in result.stdout


def test_healthcheck_validates_config_without_printing_secrets(tmp_path):
    env = {
        **os.environ,
        'PERSONAL_OS_DATA_DIR': str(tmp_path),
        'TELEGRAM_BOT_TOKEN': 'secret-value',
        'TELEGRAM_CHAT_ID': '42',
        'UNIVERSITY_SCHEDULE_URL': 'https://example.test/feed.ics',
    }

    result = subprocess.run(
        [sys.executable, SCRIPT], env=env, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0
    assert 'READY' in result.stdout
    assert 'secret-value' not in result.stdout


def test_healthcheck_rejects_insecure_or_invalid_source_without_echoing_it(tmp_path):
    secret_url = 'http://example.test/feed?token=TOP_SECRET'
    env = {
        **os.environ,
        'PERSONAL_OS_DATA_DIR': str(tmp_path),
        'TELEGRAM_BOT_TOKEN': 'token',
        'TELEGRAM_CHAT_ID': '42',
        'UNIVERSITY_SCHEDULE_URL': secret_url,
    }

    result = subprocess.run(
        [sys.executable, SCRIPT], env=env, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 1
    assert 'HTTPS URL' in result.stdout
    assert 'TOP_SECRET' not in result.stdout + result.stderr


def test_healthcheck_rejects_chat_id_that_runtime_cannot_bind(tmp_path):
    env = {
        **os.environ,
        'PERSONAL_OS_DATA_DIR': str(tmp_path),
        'TELEGRAM_BOT_TOKEN': 'token',
        'TELEGRAM_CHAT_ID': '../another-user',
    }

    result = subprocess.run(
        [sys.executable, SCRIPT], env=env, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 1
    assert 'TELEGRAM_CHAT_ID is invalid' in result.stdout
    assert '../another-user' not in result.stdout + result.stderr
