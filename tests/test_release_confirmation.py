import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.confirm_amvera_release import confirm_amvera_release
from services.release_confirmation import (
    DEPLOYMENT_ENVIRONMENT, GITHUB_API, GITHUB_CREATOR, GITHUB_REPOSITORY,
    GitHubReleaseConfirmation, HEALTH_URL, manifest_confirmation_digest, valid_released_manifest,
)
from services.release_delivery import ReleaseAnnouncementDelivery, release_artifact_fingerprint


SHA = 'a' * 40
FINGERPRINT = 'b' * 64


def response(status, payload):
    return SimpleNamespace(status_code=status, json=lambda: payload)


def deployment(**changes):
    value = {
        'id': 17, 'sha': SHA, 'environment': DEPLOYMENT_ENVIRONMENT,
        'creator': {'login': GITHUB_CREATOR},
        'payload': {'source_branch': 'main', 'release_id': 'release-2',
                    'artifact_fingerprint': FINGERPRINT,
                    'manifest_sha256': manifest_confirmation_digest(
                        {'id': 'release-2', 'text': 'Release notes', 'status': 'released'})},
    }
    value.update(changes)
    return value


def released_manifest():
    return {'id': 'release-2', 'text': 'Release notes', 'status': 'released'}


def test_announcement_mode_is_validated_and_bound_to_confirmation_digest():
    base = released_manifest()
    automatic = {**base, 'announcement_mode': 'automatic'}
    manual = {**base, 'announcement_mode': 'manual_with_image'}
    assert manifest_confirmation_digest(base) == manifest_confirmation_digest(automatic)
    assert manifest_confirmation_digest(base) != manifest_confirmation_digest(manual)
    for unsupported in ('manual', 'automatic_with_image', '', None, 1):
        invalid = {**base, 'announcement_mode': unsupported}
        assert not valid_released_manifest(invalid)
        with pytest.raises(ValueError):
            manifest_confirmation_digest(invalid)


def test_confirmation_requires_trusted_exact_deployment_and_latest_success(tmp_path):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if url.endswith('/deployments'):
            return response(200, [
                deployment(creator={'login': 'untrusted[bot]'}),
                deployment(sha='c' * 40),
                deployment(payload={**deployment()['payload'], 'artifact_fingerprint': 'c' * 64}),
                deployment(),
            ])
        return response(200, [{'state': 'failure', 'creator': {'login': GITHUB_CREATOR}},
                              {'state': 'success', 'creator': {'login': GITHUB_CREATOR}}])
    observer = GitHubReleaseConfirmation(tmp_path / 'state', get=get, clock=lambda: 1000)
    assert observer.confirm(released_manifest(), FINGERPRINT) == {'status': 'pending'}
    assert calls == [f'{GITHUB_API}/repos/{GITHUB_REPOSITORY}/deployments',
                     f'{GITHUB_API}/repos/{GITHUB_REPOSITORY}/deployments/17/statuses']


def test_confirmation_accepts_matching_trusted_main_success(tmp_path):
    def get(url, **kwargs):
        if url.endswith('/deployments'):
            return response(200, [deployment()])
        return response(200, [{'state': 'success', 'creator': {'login': GITHUB_CREATOR}}])
    observer = GitHubReleaseConfirmation(tmp_path / 'state', get=get, clock=lambda: 1000)
    assert observer.confirm(released_manifest(), FINGERPRINT) == {'status': 'confirmed', 'revision': SHA}


def test_confirmation_does_not_reuse_success_for_a_different_announcement_mode(tmp_path):
    def get(url, **kwargs):
        if url.endswith('/deployments'):
            return response(200, [deployment()])  # The observed deployment binds automatic mode.
        return response(200, [{'state': 'success', 'creator': {'login': GITHUB_CREATOR}}])
    observer = GitHubReleaseConfirmation(tmp_path / 'state', get=get, clock=lambda: 1000)
    manifest = {**released_manifest(), 'announcement_mode': 'manual_with_image'}
    assert observer.confirm(manifest, FINGERPRINT) == {'status': 'pending'}


def test_confirmation_is_durable_rate_limited_across_restarts_and_retries_transient_errors(tmp_path):
    now = [1000]
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response(503, {})
    observer = GitHubReleaseConfirmation(tmp_path / 'state', get=get, clock=lambda: now[0])
    assert observer.confirm(released_manifest(), FINGERPRINT) == {'status': 'pending'}
    restarted = GitHubReleaseConfirmation(tmp_path / 'state', get=get, clock=lambda: now[0])
    assert restarted.confirm(released_manifest(), FINGERPRINT) == {'status': 'pending'}
    assert len(calls) == 1
    now[0] += 300
    assert restarted.confirm(released_manifest(), FINGERPRINT) == {'status': 'pending'}
    assert len(calls) == 2


def test_confirmation_rejects_success_status_from_untrusted_actor(tmp_path):
    def get(url, **kwargs):
        if url.endswith('/deployments'):
            return response(200, [deployment()])
        return response(200, [{'state': 'success', 'creator': {'login': 'someone-else'}}])
    observer = GitHubReleaseConfirmation(tmp_path / 'state', get=get, clock=lambda: 1000)
    assert observer.confirm(released_manifest(), FINGERPRINT) == {'status': 'pending'}


def test_confirmation_rate_limit_rejects_non_finite_or_extreme_persisted_time(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    calls = []
    for serialized in ('{"last_check":NaN}', '{"last_check":-Infinity}',
                       '{"last_check":' + '9' * 400 + '}'):
        (state / 'release_confirmation_poll.json').write_text(serialized)
        observer = GitHubReleaseConfirmation(state, get=lambda *a, **k: calls.append(a), clock=lambda: 1000)
        assert observer.confirm(released_manifest(), FINGERPRINT) == {'status': 'pending'}
    assert calls == []


def fake_root(tmp_path, manifest):
    for directory in ('adapters', 'services', 'scripts', 'persistence', 'cli', 'releases'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    for name in ('models.py', 'planning_engine.py', 'ids.py', 'results.py', 'requirements-closed-beta.txt'):
        (tmp_path / name).write_text('synthetic artifact')
    (tmp_path / 'releases' / 'current.json').write_text(json.dumps(manifest))
    return tmp_path


def test_confirmation_workflow_skips_draft_without_creating_deployment(tmp_path):
    root = fake_root(tmp_path, {'id': 'release-2', 'text': 'Not approved', 'status': 'draft'})
    calls = []
    assert confirm_amvera_release(root, SHA, 'synthetic-token',
        post=lambda *a, **k: calls.append((a, k))) == 'pending'
    assert calls == []


def test_confirmation_workflow_rejects_manifest_revision_mismatch_before_api_calls(tmp_path):
    root = fake_root(tmp_path, {**released_manifest(), 'revision': 'c' * 40})
    calls = []
    assert confirm_amvera_release(root, SHA, 'synthetic-token',
        post=lambda *a, **k: calls.append(a)) == 'invalid'
    assert calls == []


def test_observer_rejects_invalid_released_text_without_api_calls(tmp_path):
    calls = []
    observer = GitHubReleaseConfirmation(tmp_path / 'state', get=lambda *a, **k: calls.append(a))
    invalid = {**released_manifest(), 'text': '  '}
    assert observer.confirm(invalid, FINGERPRINT) == {'status': 'pending'}
    assert calls == []


def test_confirmation_workflow_creates_deployment_then_success_only_after_health_match(tmp_path):
    manifest = released_manifest()
    root = fake_root(tmp_path, manifest)
    fingerprint = release_artifact_fingerprint(root)
    posts, health_calls = [], []
    def post(url, **kwargs):
        posts.append((url, kwargs))
        if url.endswith('/deployments'):
            return response(201, {'id': 23, 'sha': SHA, 'environment': DEPLOYMENT_ENVIRONMENT})
        return response(201, {})
    def get(url, **kwargs):
        health_calls.append(url)
        return SimpleNamespace(status_code=200, text=f'Release fingerprint: {fingerprint}.')
    now = [0]
    def sleep(duration):
        now[0] += duration
    assert confirm_amvera_release(root, SHA, 'synthetic-token', get=get, post=post,
        clock=lambda: now[0], sleep=sleep) == 'confirmed'
    assert [url for url, _ in posts] == [
        f'{GITHUB_API}/repos/{GITHUB_REPOSITORY}/deployments',
        f'{GITHUB_API}/repos/{GITHUB_REPOSITORY}/deployments/23/statuses',
        f'{GITHUB_API}/repos/{GITHUB_REPOSITORY}/deployments/23/statuses',
    ]
    assert [kwargs['json'].get('state') for _, kwargs in posts[1:]] == ['in_progress', 'success']
    payload = posts[0][1]['json']['payload']
    assert payload == {'source_branch': 'main', 'release_id': 'release-2',
                       'artifact_fingerprint': fingerprint,
                       'manifest_sha256': manifest_confirmation_digest(manifest)}
    assert health_calls == [HEALTH_URL, HEALTH_URL]


def test_confirmation_requires_two_spaced_matching_health_reads(tmp_path):
    manifest = released_manifest()
    root = fake_root(tmp_path, manifest)
    fingerprint = release_artifact_fingerprint(root)
    posts = []
    health = iter([
        f'Release fingerprint: {fingerprint}.',
        'Release fingerprint: ' + 'c' * 64 + '.',
    ])
    def post(url, **kwargs):
        posts.append((url, kwargs))
        if url.endswith('/deployments'):
            return response(201, {'id': 23, 'sha': SHA, 'environment': DEPLOYMENT_ENVIRONMENT})
        return response(201, {})
    def get(url, **kwargs):
        return SimpleNamespace(status_code=200, text=next(health))
    now = [0]
    def sleep(duration):
        now[0] += duration
    assert confirm_amvera_release(root, SHA, 'synthetic-token', get=get, post=post,
        sleep=sleep, clock=lambda: now[0], max_wait=30) == 'timeout'
    assert [kwargs['json'].get('state') for _, kwargs in posts[1:]] == ['in_progress', 'failure']


def test_github_mode_synthesizes_receipt_from_observed_sha_and_keeps_restart_dedup(tmp_path):
    root = fake_root(tmp_path, released_manifest())
    fingerprint = release_artifact_fingerprint(root)
    manifest_path = 'releases/current.json'
    state_dir = tmp_path / 'announcement-state'
    env = {
        'PERSONAL_OS_RELEASE_CONFIRMATION': 'github',
        'PERSONAL_OS_RELEASE_MANIFEST': manifest_path,
        'PERSONAL_OS_ANNOUNCEMENT_DIR': str(state_dir),
        'PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL': HEALTH_URL,
        'PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT': '1',
        'TELEGRAM_BOT_TOKEN': 'synthetic-main-token',
    }
    class Confirmation:
        def confirm(self, manifest, local_fingerprint):
            assert manifest == released_manifest()
            assert local_fingerprint == fingerprint
            return {'status': 'confirmed', 'revision': SHA}
    requests = []
    def get(url, **kwargs):
        requests.append(url)
        return SimpleNamespace(status_code=200, text=f'Release fingerprint: {fingerprint}.')
    posts = []
    def post(url, **kwargs):
        posts.append((url, kwargs))
        return response(200, {'ok': True, 'result': {'message_id': 42, 'chat': {'id': -1001}}})
    delivery = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post,
                                             confirmation=Confirmation())
    assert delivery.check_once() == {'status': 'sent', 'message_id': 42}
    assert requests == [HEALTH_URL]
    assert posts[0][1]['json']['chat_id'] == '@myaicalendar_updates'
    restarted = ReleaseAnnouncementDelivery(root=root, environ=env, get=get, post=post,
                                              confirmation=Confirmation())
    assert restarted.check_once() == {'status': 'already_sent', 'message_id': 42}
    assert len(posts) == 1
