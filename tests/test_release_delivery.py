import json
import os
from pathlib import Path
from types import SimpleNamespace

from services.release_delivery import ReleaseAnnouncementDelivery, release_artifact_fingerprint


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
