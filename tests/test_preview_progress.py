import threading
import json

import pytest

from services.invited_beta_runtime import DurablePolling, InvitedBot


def update(update_id, chat_id='101', text='/weekly_preview'):
    return {'update_id': update_id, 'message': {
        'chat': {'id': chat_id, 'type': 'private'},
        'from': {'id': chat_id},
        'text': text,
    }}


@pytest.mark.parametrize('command', [
    '/weekly_preview', '/preparations', '/update_all', '/update_schedule',
    '/weekly_preview@calendar_bot', '/preparations@calendar_bot',
    '/update_all@calendar_bot', '/update_schedule@calendar_bot',
])
def test_preview_ack_precedes_handler_and_keeps_final_reply(tmp_path, command):
    order, sent = [], []

    class App:
        def handle_text(self, chat_id, text):
            order.append('handler')
            return {'text': 'FINAL PREVIEW', 'buttons': []}

    bot = InvitedBot('synthetic', tmp_path, lambda chat: None,
                     application_factory=lambda *args: App())
    bot.invites.set_access('101', True)

    def send_message(chat_id, text, buttons=None):
        cursor = bot.invites.directory(chat_id) / 'last_update.json'
        assert json.loads(cursor.read_text()) == 1
        sent.append(text)
        order.append('ack')

    bot.send_message = send_message
    result = bot.process_update(update(1, text=command))

    assert order == ['ack', 'handler']
    assert sent == [bot.PREVIEW_ACK]
    assert result == {'text': 'FINAL PREVIEW', 'buttons': []}


def test_durable_duplicate_and_foreign_updates_do_not_receive_preview_ack(tmp_path):
    entered, release, final_sent = threading.Event(), threading.Event(), threading.Event()
    order, sent = [], []

    class App:
        def handle_text(self, chat_id, text):
            order.append('handler')
            entered.set()
            assert release.wait(5)
            return {'text': 'FINAL PREVIEW', 'buttons': []}

    bot = InvitedBot('synthetic', tmp_path, lambda chat: None,
                     application_factory=lambda *args: App())
    bot.invites.set_access('101', True)

    def send_message(chat_id, text, buttons=None):
        sent.append(text)
        order.append('ack' if text == bot.PREVIEW_ACK else 'final')
        if text == bot.PREVIEW_ACK:
            assert polling.state['entries'][0]['status'] == 'started'
            cursor = bot.invites.directory(chat_id) / 'last_update.json'
            assert json.loads(cursor.read_text()) == 7
        if text == 'FINAL PREVIEW':
            final_sent.set()

    bot.send_message = send_message
    polling = DurablePolling(bot, workers=1, max_pending=4)
    polling.start()
    try:
        spoofed = update(6)
        spoofed['message']['from']['id'] = '202'
        group = update(6)
        group['message']['chat']['type'] = 'group'
        assert polling.accept(spoofed) is False
        assert polling.accept(group) is False
        preview = update(7)
        assert polling.accept(preview)
        assert entered.wait(5)
        assert order == ['ack', 'handler']
        assert sent == [bot.PREVIEW_ACK]
        assert polling.accept(preview) is False
        assert polling.accept(update(8, chat_id='202')) is False
        assert order == ['ack', 'handler']
    finally:
        release.set()
        polling.close()

    assert final_sent.is_set()
    assert sent == [bot.PREVIEW_ACK, 'FINAL PREVIEW']


def test_non_preview_command_and_duplicate_application_claim_do_not_ack(tmp_path):
    sent = []

    class App:
        def handle_text(self, chat_id, text):
            return {'text': 'HELP', 'buttons': []}

    bot = InvitedBot('synthetic', tmp_path, lambda chat: None,
                     application_factory=lambda *args: App())
    bot.invites.set_access('101', True)
    bot.send_message = lambda chat_id, text, buttons=None: sent.append(text)

    assert bot.process_update(update(3, text='/help'))['text'] == 'HELP'
    assert bot.process_update(update(3, text='/weekly_preview')) is None
    assert sent == []


def test_failed_progress_delivery_does_not_prevent_final_preview(tmp_path):
    handled = []

    class App:
        def handle_text(self, chat_id, text):
            handled.append(text)
            return {'text': 'FINAL PREVIEW', 'buttons': []}

    bot = InvitedBot('synthetic', tmp_path, lambda chat: None,
                     application_factory=lambda *args: App())
    bot.invites.set_access('101', True)

    def fail_ack(*args, **kwargs):
        import requests
        raise requests.ConnectionError('synthetic Telegram failure')

    bot.send_message = fail_ack

    assert bot.process_update(update(4)) == {'text': 'FINAL PREVIEW', 'buttons': []}
    assert handled == ['/weekly_preview']
