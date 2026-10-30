import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from services.release_delivery import ReleaseAnnouncementDelivery, release_artifact_fingerprint
from services.release_confirmation import HEALTH_URL


def setup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fingerprint = release_artifact_fingerprint(root)
    manifest, receipt = tmp_path / 'manifest.json', tmp_path / 'receipt.json'
    manifest.write_text(json.dumps({'id': 'update-test', 'revision': 'a' * 40,
                                    'text': 'Release notes', 'status': 'released'}))
    receipt.write_text(json.dumps({'status': 'success', 'release_id': 'update-test',
                                   'revision': 'a' * 40,
                                   'artifact_fingerprint': fingerprint}))
    env = {'PERSONAL_OS_RELEASE_MANIFEST': str(manifest),
           'PERSONAL_OS_RELEASE_RECEIPT': str(receipt),
           'PERSONAL_OS_ANNOUNCEMENT_DIR': str(tmp_path / 'ledger'),
           'PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL': 'https://example.test/healthz',
           'TELEGRAM_ANNOUNCEMENT_BOT_TOKEN': 'dedicated-test-token'}
    return root, fingerprint, manifest, receipt, env


def response(status, data=None, text=''):
    return SimpleNamespace(status_code=status, text=text, json=lambda: data)


def test_disabled_has_zero_io(tmp_path):
    calls = []
    delivery = ReleaseAnnouncementDelivery(root=tmp_path, environ={}, get=lambda *a, **k: calls.append(a),
                                           post=lambda *a, **k: calls.append(a))
    assert delivery.check_once() == {'status': 'disabled'}
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_main_bot_token_alone_keeps_announcements_disabled(tmp_path):
    calls = []
    delivery = ReleaseAnnouncementDelivery(root=tmp_path, environ={'TELEGRAM_BOT_TOKEN': 'main-test-token'},
        get=lambda *a, **k: calls.append(a), post=lambda *a, **k: calls.append(a))
    assert delivery.check_once() == {'status': 'disabled'}
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_explicit_main_bot_opt_in_uses_main_token(tmp_path):
    root, fingerprint, _, _, env = setup(tmp_path)
    env.pop('TELEGRAM_ANNOUNCEMENT_BOT_TOKEN')
    env['PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT'] = '1'
    env['TELEGRAM_BOT_TOKEN'] = 'main-test-token'
    posts = []
    get = lambda *a, **k: response(200, text=f'Release fingerprint: {fingerprint}.')
    def post(url, **kwargs):
        posts.append((url, kwargs))
        return response(200, {'ok': True, 'result': {'message_id': 7, 'chat': {'id': -1001}}})
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post)
    assert delivery.check_once() == {'status': 'sent', 'message_id': 7}
    assert posts[0][0] == 'https://api.telegram.org/botmain-test-token/sendMessage'


def test_main_bot_opt_in_requires_main_token(tmp_path):
    delivery = ReleaseAnnouncementDelivery(root=tmp_path,
        environ={'PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT': '1'})
    assert delivery.check_once() == {'status': 'invalid'}


def test_main_bot_opt_in_rejects_ambiguous_dedicated_token(tmp_path):
    _, _, _, _, env = setup(tmp_path)
    env['PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT'] = '1'
    env['TELEGRAM_BOT_TOKEN'] = 'main-test-token'
    delivery = ReleaseAnnouncementDelivery(root=tmp_path, environ=env)
    assert delivery.check_once() == {'status': 'invalid'}


def test_invalid_main_bot_flag_fails_closed(tmp_path):
    for flag in ('true', '0', 'yes'):
        delivery = ReleaseAnnouncementDelivery(root=tmp_path,
            environ={'PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT': flag,
                     'TELEGRAM_BOT_TOKEN': 'main-test-token'})
        assert delivery.check_once() == {'status': 'invalid'}


def test_draft_and_missing_receipt_are_pending_without_network(tmp_path):
    root, _, manifest, receipt, env = setup(tmp_path)
    calls = []
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env,
        get=lambda *a, **k: calls.append(a), post=lambda *a, **k: calls.append(a))
    data = json.loads(manifest.read_text()); data['status'] = 'draft'
    manifest.write_text(json.dumps(data))
    assert delivery.check_once() == {'status': 'pending'}
    data['status'] = 'released'; manifest.write_text(json.dumps(data)); receipt.unlink()
    assert delivery.check_once() == {'status': 'pending'}
    assert calls == []


def test_fingerprint_and_health_gates_prevent_post(tmp_path):
    root, fingerprint, _, receipt, env = setup(tmp_path)
    posts = []
    def get(*args, **kwargs):
        assert kwargs == {'timeout': (3, 5), 'allow_redirects': False}
        return response(200, text='Release fingerprint: stale.')
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get,
                                            post=lambda *a, **k: posts.append(a))
    assert delivery.check_once() == {'status': 'blocked'}
    assert posts == []
    data = json.loads(receipt.read_text()); data['artifact_fingerprint'] = '0' * 64
    receipt.write_text(json.dumps(data))
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get,
                                            post=lambda *a, **k: posts.append(a))
    assert delivery.check_once() == {'status': 'blocked'}
    assert posts == []
    assert fingerprint != 'unavailable'


def test_successful_integrated_core_path_sends_once_and_restart_dedups(tmp_path):
    root, fingerprint, _, _, env = setup(tmp_path)
    posts = []
    def get(*args, **kwargs):
        return response(200, text=f'Release fingerprint: {fingerprint}.')
    def post(url, **kwargs):
        posts.append((url, kwargs))
        return response(200, {'ok': True, 'result': {'message_id': 42, 'chat': {'id': -1001}}})
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post)
    assert delivery.check_once() == {'status': 'sent', 'message_id': 42}
    assert len(posts) == 1


def test_previous_release_receipt_stays_pending_until_atomically_replaced(tmp_path):
    root, fingerprint, _, receipt_path, env = setup(tmp_path)
    receipt_path.write_text(json.dumps({
        'status': 'success', 'release_id': 'previous-release', 'revision': 'b' * 40,
        'artifact_fingerprint': fingerprint,
    }))
    posts = []
    get = lambda *a, **k: response(200, text=f'Release fingerprint: {fingerprint}.')
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get,
        post=lambda *a, **k: posts.append(a))
    assert delivery.check_once() == {'status': 'pending'}
    assert posts == [] and delivery.stopped is False

    replacement = receipt_path.with_suffix('.tmp')
    replacement.write_text(json.dumps({
        'status': 'success', 'release_id': 'update-test', 'revision': 'a' * 40,
        'artifact_fingerprint': fingerprint,
    }))
    os.replace(replacement, receipt_path)
    def post(url, **kwargs):
        posts.append((url, kwargs))
        return response(200, {'ok': True, 'result': {'message_id': 42, 'chat': {'id': -1001}}})
    delivery.post = post
    assert delivery.check_once() == {'status': 'sent', 'message_id': 42}
    restarted = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post)
    assert restarted.check_once() == {'status': 'already_sent', 'message_id': 42}
    assert len(posts) == 1
    assert posts[0][0] == 'https://api.telegram.org/botdedicated-test-token/sendMessage'
    assert posts[0][1]['json'] == {'chat_id': '@myaicalendar_updates', 'text': 'Release notes'}
    assert posts[0][1]['allow_redirects'] is False
    restarted = ReleaseAnnouncementDelivery(root=root, environ=env, get=get,
                                              post=lambda *a, **k: posts.append((a, k)))
    assert restarted.check_once() == {'status': 'already_sent', 'message_id': 42}
    assert len(posts) == 1


def test_redirect_and_ambiguous_post_are_fail_closed(tmp_path):
    root, fingerprint, _, _, env = setup(tmp_path)
    posts = []
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env,
        get=lambda *a, **k: response(302, text=f'Release fingerprint: {fingerprint}.'),
        post=lambda *a, **k: posts.append(a))
    assert delivery.check_once() == {'status': 'blocked'} and posts == []
    def get(*a, **k):
        return response(200, text=f'Release fingerprint: {fingerprint}.')
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get,
        post=lambda *a, **k: response(200, {'ok': True, 'result': {'message_id': 42}}))
    assert delivery.check_once() == {'status': 'uncertain'}
    assert delivery.check_once() == {'status': 'stopped'}


def test_symlink_in_artifact_is_unavailable(tmp_path):
    for name in ('adapters', 'services', 'scripts', 'persistence', 'cli'):
        (tmp_path / name).mkdir()
    for name in ('models.py', 'planning_engine.py', 'ids.py', 'results.py', 'requirements-closed-beta.txt'):
        (tmp_path / name).write_text('x')
    (tmp_path / 'services' / 'bad.py').symlink_to(tmp_path / 'models.py')
    assert release_artifact_fingerprint(tmp_path) == 'unavailable'


def test_manual_with_image_receipt_mode_validates_health_but_never_posts_or_reserves(tmp_path):
    root, fingerprint, manifest_path, _receipt, env = setup(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest['announcement_mode'] = 'manual_with_image'
    manifest_path.write_text(json.dumps(manifest))
    get = Mock(return_value=response(200, text=f'Release fingerprint: {fingerprint}.'))
    post, announce = Mock(), Mock()
    state_dir = Path(env['PERSONAL_OS_ANNOUNCEMENT_DIR'])
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post, announce=announce)

    assert delivery.check_once() == {'status': 'manual'}
    assert delivery.stopped
    get.assert_called_once_with(env['PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL'],
                                timeout=(3, 5), allow_redirects=False)
    post.assert_not_called()
    announce.assert_not_called()
    assert not state_dir.exists()
    assert delivery.check_once() == {'status': 'stopped'}
    post.assert_not_called()


def github_setup(tmp_path, mode='manual_with_image'):
    root = tmp_path / 'repo'
    releases = root / 'releases'
    releases.mkdir(parents=True)
    manifest = {'id': 'manual-test', 'revision': 'a' * 40,
                'text': 'Release notes with screenshot', 'status': 'released'}
    if mode is not None:
        manifest['announcement_mode'] = mode
    manifest_path = releases / 'current.json'
    manifest_path.write_text(json.dumps(manifest))
    env = {'PERSONAL_OS_RELEASE_CONFIRMATION': 'github',
           'PERSONAL_OS_RELEASE_MANIFEST': 'releases/current.json',
           'PERSONAL_OS_ANNOUNCEMENT_DIR': str(tmp_path / 'github-state'),
           'PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL': HEALTH_URL,
           'TELEGRAM_ANNOUNCEMENT_BOT_TOKEN': 'dedicated-test-token'}
    return root, manifest, env


def test_manual_with_image_github_mode_still_confirms_release_and_health(tmp_path, monkeypatch):
    root, manifest, env = github_setup(tmp_path)
    fingerprint = 'f' * 64
    monkeypatch.setattr('services.release_delivery.release_artifact_fingerprint', lambda _root: fingerprint)
    confirmation = SimpleNamespace(confirm=Mock(return_value={'status': 'confirmed', 'revision': 'b' * 40}))
    get = Mock(return_value=response(200, text=f'Release fingerprint: {fingerprint}.'))
    post, announce = Mock(), Mock()
    state_dir = Path(env['PERSONAL_OS_ANNOUNCEMENT_DIR'])
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post,
                                           announce=announce, confirmation=confirmation)

    assert delivery.check_once() == {'status': 'manual'}
    assert delivery.stopped
    confirmation.confirm.assert_called_once_with(manifest, fingerprint)
    get.assert_called_once_with(HEALTH_URL, timeout=(3, 5), allow_redirects=False)
    post.assert_not_called()
    announce.assert_not_called()
    assert not state_dir.exists()


def test_manual_github_mode_waits_for_confirmation_and_health(tmp_path, monkeypatch):
    root, _manifest, env = github_setup(tmp_path)
    fingerprint = 'c' * 64
    monkeypatch.setattr('services.release_delivery.release_artifact_fingerprint', lambda _root: fingerprint)
    confirmation = SimpleNamespace(confirm=Mock(return_value={'status': 'pending'}))
    get, post, announce = Mock(), Mock(), Mock()
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post,
                                           announce=announce, confirmation=confirmation)
    assert delivery.check_once() == {'status': 'pending'}
    confirmation.confirm.assert_called_once()
    get.assert_not_called()
    post.assert_not_called()
    announce.assert_not_called()


def test_unknown_announcement_mode_fails_closed_in_both_confirmation_paths(tmp_path, monkeypatch):
    root, fingerprint, manifest_path, receipt_path, env = setup(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest['announcement_mode'] = 'guess'
    manifest_path.write_text(json.dumps(manifest))
    get, post, announce = Mock(), Mock(), Mock()
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post, announce=announce)
    assert delivery.check_once() == {'status': 'blocked'}
    get.assert_not_called()
    post.assert_not_called()
    announce.assert_not_called()

    github_root, _manifest, github_env = github_setup(tmp_path / 'github', mode='guess')
    fingerprint = 'e' * 64
    monkeypatch.setattr('services.release_delivery.release_artifact_fingerprint', lambda _root: fingerprint)
    confirmation = Mock()
    github_get, github_post, github_announce = Mock(), Mock(), Mock()
    github_delivery = ReleaseAnnouncementDelivery(root=github_root, environ=github_env,
        get=github_get, post=github_post, announce=github_announce, confirmation=confirmation)
    assert github_delivery.check_once() == {'status': 'blocked'}
    confirmation.confirm.assert_not_called()
    github_get.assert_not_called()
    github_post.assert_not_called()
    github_announce.assert_not_called()


def test_manual_receipt_mode_does_not_bypass_health_fingerprint(tmp_path):
    root, fingerprint, manifest_path, _receipt, env = setup(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest['announcement_mode'] = 'manual_with_image'
    manifest_path.write_text(json.dumps(manifest))
    get, post, announce = Mock(return_value=response(200, text='Release fingerprint: stale.')), Mock(), Mock()
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post, announce=announce)
    assert delivery.check_once() == {'status': 'blocked'}
    get.assert_called_once()
    post.assert_not_called()
    announce.assert_not_called()
    assert fingerprint != 'unavailable'


def test_manual_with_image_invalid_receipt_remains_blocked(tmp_path):
    root, _fingerprint, manifest_path, receipt_path, env = setup(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest['announcement_mode'] = 'manual_with_image'
    manifest_path.write_text(json.dumps(manifest))
    receipt = json.loads(receipt_path.read_text())
    receipt['artifact_fingerprint'] = '0' * 64
    receipt_path.write_text(json.dumps(receipt))
    get, post, announce = Mock(), Mock(), Mock()
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post, announce=announce)
    assert delivery.check_once() == {'status': 'blocked'}
    get.assert_not_called()
    post.assert_not_called()
    announce.assert_not_called()
