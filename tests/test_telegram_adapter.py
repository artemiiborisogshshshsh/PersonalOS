import asyncio
from unittest.mock import patch

from adapters.telegram_adapter import TelegramAdapter


def test_telegram_adapter_does_not_log_token_or_provider_error(capsys):
    token = 'TOP_SECRET_TOKEN'
    adapter = TelegramAdapter({'bot_token': token, 'chat_id': '42'})

    with patch('adapters.telegram_adapter.requests.get', side_effect=RuntimeError(
        f'https://api.telegram.org/bot{token}/getMe',
    )):
        assert asyncio.run(adapter.initialize()) is False

    output = capsys.readouterr().out
    assert output == 'Telegram adapter initialization failed\n'
    assert token not in output


def test_telegram_adapter_does_not_log_rejected_response_body(capsys):
    adapter = TelegramAdapter({'bot_token': 'token', 'chat_id': '42'})
    adapter._set_initialized(True)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {'ok': False, 'description': 'TOP_SECRET provider response'}

    with patch('adapters.telegram_adapter.requests.post', return_value=Response()):
        assert asyncio.run(adapter.send_notification('hello')) is False

    assert capsys.readouterr().out == 'Telegram notification was rejected\n'
