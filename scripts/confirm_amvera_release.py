#!/usr/bin/env python3
"""Record a GitHub deployment confirmation after Amvera serves the exact artifact."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.release_confirmation import (  # noqa: E402
    DEPLOYMENT_ENVIRONMENT, GITHUB_API, GITHUB_REPOSITORY, HEALTH_URL,
    manifest_confirmation_digest, valid_released_manifest,
)
from services.release_delivery import release_artifact_fingerprint  # noqa: E402


_SHA = re.compile(r'^[0-9a-f]{40}$')
_FINGERPRINT_LINE = re.compile(r'Release fingerprint: ([0-9a-f]{64})\.')
MAX_WAIT_SECONDS = 15 * 60
HEALTH_INTERVAL_SECONDS = 15


def _headers(token):
    return {'Accept': 'application/vnd.github+json',
            'Authorization': 'Bearer ' + token,
            'X-GitHub-Api-Version': '2022-11-28'}


def _api_post(post, url, token, body):
    try:
        response = post(url, json=body, headers=_headers(token),
                        timeout=(3, 10), allow_redirects=False)
        return response if response.status_code in (200, 201, 202) else None
    except Exception:
        return None


def confirm_amvera_release(root, source_sha, token, *, get=None, post=None,
                           sleep=None, clock=None, max_wait=MAX_WAIT_SECONDS):
    """Create status metadata for this exact main SHA after public health matches."""
    get = get or requests.get
    post = post or requests.post
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    root = Path(root)
    if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha) or not token:
        return 'invalid'
    manifest_path = root / 'releases' / 'current.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return 'invalid'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return 'invalid'
    if not isinstance(manifest, dict) or manifest.get('status') == 'draft':
        return 'pending'
    if not valid_released_manifest(manifest):
        return 'invalid'
    if manifest.get('revision') is not None and manifest.get('revision') != source_sha:
        return 'invalid'
    fingerprint = release_artifact_fingerprint(root)
    if fingerprint == 'unavailable':
        return 'invalid'
    try:
        digest = manifest_confirmation_digest(manifest)
    except (KeyError, TypeError, ValueError, UnicodeError):
        return 'invalid'

    base = f'{GITHUB_API}/repos/{GITHUB_REPOSITORY}/deployments'
    payload = {'source_branch': 'main', 'release_id': manifest['id'],
               'artifact_fingerprint': fingerprint, 'manifest_sha256': digest}
    created = _api_post(post, base, token, {
        'ref': source_sha, 'task': 'deploy:amvera-confirmation', 'auto_merge': False,
        'required_contexts': [], 'environment': DEPLOYMENT_ENVIRONMENT,
        'transient_environment': False, 'production_environment': True, 'payload': payload,
    })
    if created is None:
        return 'error'
    try:
        deployment = created.json()
        deployment_id = deployment.get('id')
        if (not isinstance(deployment_id, int) or isinstance(deployment_id, bool)
                or deployment_id <= 0 or deployment.get('sha') != source_sha
                or deployment.get('environment') != DEPLOYMENT_ENVIRONMENT):
            return 'error'
    except Exception:
        return 'error'
    status_url = f'{base}/{deployment_id}/statuses'
    if _api_post(post, status_url, token, {'state': 'in_progress'}) is None:
        return 'error'

    deadline = clock() + max_wait
    matching_since = None
    while clock() < deadline:
        try:
            health = get(HEALTH_URL, timeout=(3, 8), allow_redirects=False)
            match = _FINGERPRINT_LINE.search(health.text) if health.status_code == 200 else None
            if match and match.group(1) == fingerprint:
                if matching_since is None:
                    matching_since = clock()
                elif clock() - matching_since >= HEALTH_INTERVAL_SECONDS:
                    completed = _api_post(post, status_url, token, {'state': 'success'})
                    return 'confirmed' if completed is not None else 'error'
            else:
                matching_since = None
        except Exception:
            matching_since = None
        sleep(min(HEALTH_INTERVAL_SECONDS, max(0, deadline - clock())))
    _api_post(post, status_url, token, {'state': 'failure'})
    return 'timeout'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha', default=os.environ.get('GITHUB_SHA', ''))
    parser.add_argument('--source-ref', default=os.environ.get('GITHUB_REF', ''))
    args = parser.parse_args(argv)
    if args.source_ref != 'refs/heads/main':
        print('pending')
        return 0
    result = confirm_amvera_release(Path(__file__).resolve().parent.parent,
        args.source_sha, os.environ.get('GITHUB_TOKEN', ''))
    print(result)
    return 0 if result in ('pending', 'confirmed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
