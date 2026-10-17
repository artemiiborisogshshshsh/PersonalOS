"""Real OAuth Flow/PKCE, with only the external token exchange replaced."""
import base64
from hashlib import sha256
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from requests_oauthlib import OAuth2Session

from services.google_oauth_web import GoogleOAuthWeb, GoogleOAuthError
from services.invited_beta_runtime import Invitations
from services.user_registry import UserRegistryStore


def oauth_setup(tmp_path):
    invitations = Invitations(tmp_path / 'users')
    invitations.set_access('101', True)
    access = invitations.read('101')
    oauth = GoogleOAuthWeb(tmp_path / 'state', tmp_path / 'google', invitations,
                           client_id='synthetic.apps.googleusercontent.com', client_secret='synthetic',
                           redirect_uri='https://calendar.example/google/oauth/callback')
    return oauth, access['generation']


def link(oauth, generation):
    reply = oauth.create_link('101', generation)
    query = parse_qs(urlsplit(reply['buttons'][0][0]['url']).query)
    state = query['state'][0]
    path = oauth.pending_root / (sha256(state.encode('ascii')).hexdigest() + '.json')
    return state, path, query


def exchange(monkeypatch, seen, *, offline=True):
    def fake_fetch(session, token_url, **kwargs):
        assert token_url == 'https://oauth2.googleapis.com/token'
        assert kwargs['timeout'] == 20
        seen.append((kwargs.get('code_verifier'), session.redirect_uri, {'code': kwargs.get('code')}))
        session.token = {'access_token': 'synthetic-access', 'token_type': 'Bearer',
                                    'expires_at': 2000000000,
                                    **({'refresh_token': 'synthetic-refresh'} if offline else {})}
        return session.token
    monkeypatch.setattr(OAuth2Session, 'fetch_token', fake_fetch)


def test_pkce_survives_callback_in_new_instance_and_token_is_private(tmp_path, monkeypatch):
    oauth, generation = oauth_setup(tmp_path)
    state, path, query = link(oauth, generation)
    verifier = json.loads(path.read_text())['code_verifier']
    challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip('=')
    assert query['code_challenge'] == [challenge]
    assert query['code_challenge_method'] == ['S256']
    assert all(verifier not in value for values in query.values() for value in values)
    assert path.stat().st_mode & 0o077 == 0
    seen = []
    exchange(monkeypatch, seen)
    restarted = GoogleOAuthWeb(oauth.state_root, oauth.google_root, oauth.invitations,
                               oauth.client_id, oauth.client_secret, oauth.redirect_uri)
    assert restarted.complete(state, code='synthetic-code')[0] == '101'
    assert seen == [(verifier, oauth.redirect_uri, {'code': 'synthetic-code'})]
    token = oauth.google_root / (UserRegistryStore._user_id('101') + '.json')
    assert json.loads(token.read_text())['refresh_token'] == 'synthetic-refresh'
    assert token.stat().st_mode & 0o077 == 0
    with pytest.raises(GoogleOAuthError):
        restarted.complete(state, code='synthetic-code')
    assert len(seen) == 1


@pytest.mark.parametrize('failure', ['expired', 'newer_link', 'revoked', 'missing_verifier'])
def test_invalid_attempt_never_exchanges_code(tmp_path, monkeypatch, failure):
    oauth, generation = oauth_setup(tmp_path)
    state, path, _ = link(oauth, generation)
    pending = json.loads(path.read_text())
    if failure == 'expired':
        pending['expires_at'] = 0
        oauth._atomic_json(path, pending)
    elif failure == 'newer_link':
        link(oauth, generation)
    elif failure == 'revoked':
        oauth.invitations.set_access('101', False)
    else:
        pending.pop('code_verifier')
        oauth._atomic_json(path, pending)
    seen = []
    exchange(monkeypatch, seen)
    with pytest.raises(GoogleOAuthError):
        oauth.complete(state, code='synthetic-code')
    assert seen == [] and not oauth.google_root.exists()


def test_missing_offline_access_preserves_existing_connection(tmp_path, monkeypatch):
    oauth, generation = oauth_setup(tmp_path)
    token = oauth.google_root / (UserRegistryStore._user_id('101') + '.json')
    oauth._atomic_json(token, {'refresh_token': 'existing-synthetic'})
    state, _, _ = link(oauth, generation)
    exchange(monkeypatch, [], offline=False)
    with pytest.raises(GoogleOAuthError, match='offline'):
        oauth.complete(state, code='synthetic-code')
    assert json.loads(token.read_text()) == {'refresh_token': 'existing-synthetic'}


def test_token_timeout_releases_user_lock_and_allows_a_fresh_attempt(tmp_path, monkeypatch):
    import requests
    oauth, generation = oauth_setup(tmp_path)
    state, _, _ = link(oauth, generation)
    def timeout(session, token_url, **kwargs):
        assert kwargs['timeout'] == 20
        raise requests.Timeout('synthetic-secret-never-log')
    monkeypatch.setattr(OAuth2Session, 'fetch_token', timeout)
    with pytest.raises(GoogleOAuthError) as failure:
        oauth.complete(state, code='synthetic-code')
    assert 'synthetic-secret-never-log' not in str(failure.value)
    assert not oauth.google_root.exists()
    state, _, _ = link(oauth, generation)
    exchange(monkeypatch, [])
    assert oauth.complete(state, code='synthetic-code')[0] == '101'
