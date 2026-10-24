from types import SimpleNamespace
from pathlib import Path
import re
import threading

import requests

from services.google_oauth_web import GoogleOAuthWeb, runtime_code_fingerprint
from services.invited_beta_runtime import InvitedBot
from services.invited_beta_runtime import Invitations


def test_release_check_runs_after_verified_poll_and_failure_does_not_stop_poll(tmp_path, monkeypatch, caplog):
    events = []
    bot = InvitedBot('synthetic-token', tmp_path, lambda chat: chat,
                     application_factory=lambda *args: SimpleNamespace(
                         handle_text=lambda *a: None, handle_callback=lambda *a: None))
    class Delivery:
        def check_once(self):
            events.append('announcement')
            raise RuntimeError('synthetic-private-exception synthetic-token')
    bot.release_delivery = Delivery()
    original = bot.process_update
    bot.process_update = lambda update: (events.append('update'), original(update))[1]
    responses = [SimpleNamespace(raise_for_status=lambda: events.append('verified'),
                 json=lambda: {'ok': True, 'result': []})]
    polls = []
    def get(*args, **kwargs):
        polls.append(True)
        if responses:
            return responses.pop(0)
        raise KeyboardInterrupt()
    monkeypatch.setattr('services.invited_beta_runtime.requests.get', get)
    try:
        bot.run_forever(workers=1, max_pending=2)
    except KeyboardInterrupt:
        pass
    assert events.index('verified') < events.index('announcement')
    assert bot.release_delivery_stopped is True
    assert len(polls) == 2
    assert 'Release announcement stopped: error.' in caplog.text
    assert 'synthetic-private-exception' not in caplog.text
    assert 'synthetic-token' not in caplog.text


def test_malformed_success_payload_does_not_trigger_release_check(tmp_path, monkeypatch):
    bot = InvitedBot('synthetic-token', tmp_path, lambda chat: chat,
                     application_factory=lambda *args: SimpleNamespace())
    class Delivery:
        def check_once(self):
            raise AssertionError('must not check on malformed polling response')
    bot.release_delivery = Delivery()
    responses = [SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'ok': True})]
    def get(*args, **kwargs):
        if responses:
            return responses.pop()
        raise KeyboardInterrupt()
    monkeypatch.setattr('services.invited_beta_runtime.requests.get', get)
    try:
        bot.run_forever(workers=1, max_pending=2)
    except KeyboardInterrupt:
        pass
    assert bot.release_delivery_stopped is False


def test_health_keeps_runtime_fingerprint_last_and_includes_release_fingerprint(tmp_path):
    oauth = GoogleOAuthWeb(tmp_path / 'state', tmp_path / 'google', Invitations(tmp_path / 'invites'))
    server = oauth.make_server(SimpleNamespace(), host='127.0.0.1', port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        response = requests.get(f'http://127.0.0.1:{server.server_port}/healthz', timeout=3)
        assert response.status_code == 200
        expected = runtime_code_fingerprint(Path(__file__).resolve().parents[1])
        assert response.text.endswith(f'Runtime fingerprint: {expected}.')
        assert re.search(r'Release fingerprint: [0-9a-f]{64}\. Runtime fingerprint: [0-9a-f]{16}\.$', response.text)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
