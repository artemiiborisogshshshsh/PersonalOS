"""Public branding pages never expose OAuth or application state."""
import re
import threading
from pathlib import Path
from unittest.mock import Mock

import requests

from services.google_oauth_web import GoogleOAuthWeb, runtime_code_fingerprint
from services.invited_beta_runtime import Invitations
from tests.test_invited_beta import setup, envelope


def test_privacy_command_uses_configured_domain_without_starting_domain_application(tmp_path):
    bot, calendars, _ = setup(tmp_path)
    bot.invites.set_access('101', True)
    bot.google_oauth = Mock(redirect_uri='https://calendar.example/google/oauth/callback')
    response = bot.process_update(envelope(1, text='/privacy'))
    assert response['text'].endswith('https://calendar.example/privacy')
    assert bot.applications == {}
    assert calendars['101'].writes == []
    assert bot.process_update(envelope(2, chat='202', text='/privacy')) is None


def test_public_pages_are_accessible_without_oauth_and_private_paths_stay_closed(tmp_path):
    oauth = GoogleOAuthWeb(tmp_path / 'state', tmp_path / 'google', Invitations(tmp_path / 'invites'))
    oauth.complete = Mock()
    server = oauth.make_server(Mock(), host='127.0.0.1', port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        for path, phrase, mime in [('/', 'MyAiCalendar', 'text/html'),
                                   ('/privacy', '30 календарных дней', 'text/html'),
                                   ('/styles.css', '@media', 'text/css')]:
            response = requests.get(base + path, timeout=3)
            assert response.status_code == 200
            assert phrase in response.text
            assert response.headers['Content-Type'].startswith(mime)
            assert "default-src 'none'" in response.headers['Content-Security-Policy']
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
        health = requests.get(base + '/healthz', timeout=3)
        assert health.status_code == 200
        assert health.text.startswith(
            'PersonalOS closed beta is running. Image schedule limit: '
        )
        assert 'Image request format: fixed-vision-v3. OAuth flow: pkce-v1.' in health.text
        expected = runtime_code_fingerprint(Path(__file__).resolve().parent.parent)
        assert expected != 'unavailable'
        assert health.text.endswith(f'Runtime fingerprint: {expected}.')
        assert re.search(r'Runtime fingerprint: [0-9a-f]{16}\.$', health.text)
        for path in ['/google/client_secret.json', '/docs/PRIVACY_POLICY_DRAFT.md',
                     '/state/users_registry.json', '/web/public/../../services/google_oauth_web.py']:
            assert requests.get(base + path, timeout=3).status_code == 404
        assert requests.get(base + '/google/oauth/callback?state=invalid&code=invalid', timeout=3).status_code == 404
        oauth.complete.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
