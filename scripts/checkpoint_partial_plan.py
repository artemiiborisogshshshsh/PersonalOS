#!/usr/bin/env python3
"""Operator-only read-verified checkpoint for one interrupted plan update."""
import argparse
import asyncio
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.partial_plan_checkpoint import PartialCheckpointError, checkpoint
from services.published_plan_updates import digest


def _safe_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise PermissionError('Private regular file required')


def _atomic_save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise PermissionError('State file cannot be a symlink')
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp.exists():
            temp.unlink()


def _read_only_batch(body):
    raw = body.decode('utf-8') if isinstance(body, bytes) else str(body or '')
    requests = re.findall(r'(?m)^([A-Z]+) (\S+) HTTP/\d(?:\.\d)?\r?$', raw)
    return bool(requests) and all(verb == 'GET' and uri.startswith('/calendar/v3/')
                                  for verb, uri in requests)


def _readonly(service):
    http = service._http
    original = http.request
    def request(uri, method='GET', body=None, *args, **kwargs):
        verb = method.upper()
        if verb in {'GET', 'HEAD'}:
            return original(uri, method, body, *args, **kwargs)
        if (verb == 'POST' and uri == 'https://www.googleapis.com/batch/calendar/v3'
                and _read_only_batch(body)):
            return original(uri, method, body, *args, **kwargs)
        raise PermissionError('Read-only Calendar policy rejected request')
    http.request = request


def _already_done(document, expected_digest):
    for raw in document.get('operations', []):
        history = raw.get('published_plan', {}).get('recovery_history', {})
        for record in history.values():
            if (record.get('kind') == 'verified-partial-checkpoint'
                    and record.get('document_before_digest') == expected_digest):
                return True
    return False


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Persist the verified local checkpoint')
    parser.add_argument('--expected-digest', required=True)
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--old-rows', type=int, required=True)
    parser.add_argument('--new-classes', type=int, required=True)
    parser.add_argument('--new-preparations', type=int, required=True)
    parser.add_argument('--absent-preparations', type=int, required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    previous_umask = os.umask(0o077)
    try:
        if not re.fullmatch(r'[a-f0-9]{64}', args.expected_digest):
            raise ValueError('Invalid expected digest')
        from scripts.invited_beta_bot import configuration
        from scripts.closed_beta_bot import instance_lock
        from services.user_registry import UserRegistryStore
        state_root, google_root = configuration(os.environ)
        if not re.fullmatch(r'user-[0-9a-f]{24}', args.user_id):
            raise ValueError('Invalid user ID')
        scoped_root = state_root / args.user_id / 'state'
        registry_path = scoped_root / 'users_registry.json'
        _safe_file(registry_path)
        registry = UserRegistryStore(registry_path)
        accounts = [account for account in registry.accounts() if account.id == args.user_id]
        if len(accounts) != 1 or UserRegistryStore._user_id(accounts[0].telegram_chat_id) != args.user_id:
            raise PermissionError('User registry binding mismatch')
        user_dir = scoped_root / 'users' / args.user_id
        if user_dir.is_symlink() or not user_dir.is_dir() or user_dir.stat().st_mode & 0o077:
            raise PermissionError('Private user state directory required')
        path = user_dir / 'draft_operations.json'
        _safe_file(path)
        document = json.loads(path.read_text(encoding='utf-8'))
        if digest(document) != args.expected_digest:
            if _already_done(document, args.expected_digest):
                print('CHECKPOINT ALREADY APPLIED')
                return 0
            raise PartialCheckpointError('Document digest mismatch')
        counts = {'old_rows': args.old_rows, 'new_classes': args.new_classes,
                  'new_preparations': args.new_preparations,
                  'absent_preparations': args.absent_preparations}
        # All pollers share this lock. No state mutation happens before verification.
        with instance_lock(state_root):
            _safe_file(path)
            original_bytes = path.read_bytes()
            document = json.loads(original_bytes)
            if digest(document) != args.expected_digest:
                if _already_done(document, args.expected_digest):
                    print('CHECKPOINT ALREADY APPLIED')
                    return 0
                raise PartialCheckpointError('Document changed before verification')
            op = document['operations'][0]
            pending, base = op['pending_plan_update'], op['published_plan']
            projection_path = user_dir / 'university_calendar_projection.json'
            _safe_file(projection_path)
            projection = json.loads(projection_path.read_text(encoding='utf-8')).get('rows', {})
            token = google_root / (args.user_id + '.json')
            _safe_file(token)
            from adapters.google_calendar_adapter import GoogleCalendarAdapter
            adapter = GoogleCalendarAdapter({'token_path': str(token), 'initialize_calendar': False,
                                             'allow_interactive_auth': False})
            if not asyncio.run(adapter.initialize()):
                raise RuntimeError('Calendar authorization unavailable')
            _readonly(adapter.service)
            calendars = adapter.list_visible_calendars()
            if base['calendar_id'] not in {item.get('id') for item in calendars}:
                raise RuntimeError('Published calendar unavailable')
            identities = {}
            for uid in pending['rows']:
                identities[uid] = base['rows'].get(uid, {}).get('event_id') or pending['applied'].get(uid)
            for uid, decision in base.get('manual_resolutions', {}).items():
                if uid not in identities:
                    observed = decision.get('observed', {})
                    item = observed.get('by_uid') or observed.get('by_id')
                    identities[uid] = item.get('id') if item else None
            pairs = adapter.get_event_pairs(base['calendar_id'], identities)
            for uid, event_id in pending['applied'].items():
                row = pending['rows'][uid]
                if row['kind'] != 'class':
                    continue
                saved = projection.get(uid)
                if (not saved or saved.get('calendar_id') != base['calendar_id']
                        or saved.get('event_id') != event_id
                        or saved.get('pending') is not None or saved.get('override') is not None
                        or saved.get('start') != row['data']['dtstart']
                        or saved.get('end') != row['data']['dtend']):
                    raise PartialCheckpointError('Class projection checkpoint mismatch')
            result, audit = checkpoint(document, pairs, args.expected_digest, counts,
                                       __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())
            if path.read_bytes() != original_bytes:
                raise PartialCheckpointError('Document changed before atomic save')
            if args.apply:
                _atomic_save(path, result)
                print('CHECKPOINT APPLIED')
            else:
                print('DRY RUN VERIFIED')
            print(json.dumps(audit, sort_keys=True))
        return 0
    except Exception as error:
        category = 'verification_failed'
        if isinstance(error, PermissionError):
            category = 'permission_or_read_failed'
        elif isinstance(error, FileNotFoundError):
            category = 'required_file_missing'
        elif isinstance(error, PartialCheckpointError):
            category = 'checkpoint_rejected'
        print('CHECKPOINT FAILED: ' + category, file=sys.stderr)
        return 1
    finally:
        os.umask(previous_umask)


if __name__ == '__main__':
    raise SystemExit(main())
