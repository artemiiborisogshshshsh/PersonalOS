"""Synthetic-only tests for strict local checkpoint guards and atomic writes."""
import json
import os
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.checkpoint_partial_plan import _already_done, _atomic_save, _read_only_batch
from services.adaptive_preparation_service import (
    CalendarRoute, DraftOperation, DraftPreparationBlock, DraftCalendarProjector,
)
from services.draft_operation_store import DraftOperationStore
from services.partial_plan_checkpoint import PartialCheckpointError, checkpoint
from services.published_plan_updates import digest, encode
from services.user_registry import UserAccount, UserRegistryStore


COUNTS = {'old_rows': 23, 'new_classes': 11, 'new_preparations': 1,
          'absent_preparations': 10}


def _data(uid, summary):
    return {'uid': uid, 'system_block_id': uid, 'system_operation_id': 'op',
            'system_source_event_id': 'source-' + uid, 'summary': summary,
            'description': 'synthetic', 'location': '',
            'dtstart': '2030-01-01T10:00:00+00:00',
            'dtend': '2030-01-01T11:00:00+00:00'}


def _remote(uid, event_id, etag, data):
    return {'id': event_id, 'etag': etag, 'status': 'confirmed', 'iCalUID': uid,
            'summary': data['summary'], 'description': data['description'],
            'location': data['location'],
            'start': {'dateTime': data['dtstart']}, 'end': {'dateTime': data['dtend']},
            'extendedProperties': {'private': {
                'personal_os_block_id': data['system_block_id'],
                'personal_os_operation_id': data['system_operation_id'],
                'personal_os_source_event_id': data['system_source_event_id']}}}


def _pending_document():
    old = {'kind': 'class', 'data': _data('old', 'Old'), 'event_id': 'old-event', 'etag': 'old-v1'}
    new = {'kind': 'class', 'data': _data('new', 'New')}
    base = {'version': 3, 'calendar_id': 'calendar', 'rows': {'old': old}, 'manual_resolutions': {}}
    operation = DraftOperation(id='op', blocks=[], version=3, status='confirmed',
                               published_plan=base, projection_pending=True)
    candidate = DraftOperationStore._serialize(operation)
    candidate['version'] = 4
    candidate['published_plan']['version'] = 4
    payload = {'base_hash': digest(base), 'signature': 'sig', 'operation': candidate,
               'rows': {'old': old, 'new': new}, 'attempted': ['new'], 'applied': {'new': 'new-event'}}
    operation.pending_plan_update = payload
    document = {'version': 1, 'operations': [DraftOperationStore._serialize(operation)]}
    remote_old, remote_new = _remote('old', 'old-event', 'old-v1', old['data']), _remote(
        'new', 'new-event', 'new-v1', new['data'])
    pairs = {'old': (remote_old, remote_old), 'new': (remote_new, remote_new)}
    return document, pairs


def _block(uid, title, source, *, status='confirmed'):
    return DraftPreparationBlock(
        id=uid, source_event_id=source, title=title,
        start=datetime(2020, 1, 1, 10, tzinfo=timezone.utc),
        end=datetime(2020, 1, 1, 11, tzinfo=timezone.utc), minutes=60,
        reason='synthetic recovery fixture', calendar=CalendarRoute.STUDY, status=status,
        session_type='lecture')


def _prep_data(block, candidate):
    event = DraftCalendarProjector._event_data(block, candidate)
    return encode({key: getattr(event, key) for key in (
        'uid', 'system_block_id', 'system_source_event_id', 'system_operation_id',
        'summary', 'description', 'location', 'dtstart', 'dtend', 'event_type')})


def _preparation_document(*, orphan_tombstone=False):
    old = {'kind': 'class', 'data': _data('old', 'Old'), 'event_id': 'old-event', 'etag': 'old-v1'}
    old_block = _block('old-prep', 'Old preparation', 'old-source')
    written_block = _block('written-prep', 'Written preparation', 'written-source')
    absent_block = _block('absent-prep', 'Absent preparation', 'absent-source')
    manual = {}
    tombstone = {'id': 'gone-event', 'status': 'cancelled', 'etag': 'gone-v1'}
    if orphan_tombstone:
        manual['orphan-deleted'] = {'kind': 'deleted',
                                    'observed': {'by_uid': None, 'by_id': dict(tombstone)}}
    base = {'version': 3, 'calendar_id': 'calendar', 'rows': {'old': old},
            'manual_resolutions': manual,
            'recovery_history': {'prior': {'kind': 'previous-record', 'marker': 'keep'}}}
    operation = DraftOperation(
        id='op', blocks=[old_block], version=3, status='confirmed', published_plan=base,
        calendar_event_ids={'old-prep': 'old-prep-event'}, projection_pending=True)
    candidate = DraftOperationStore._serialize(operation)
    candidate['version'] = 4
    candidate['content_hash'] = 'sig'
    candidate['published_plan']['version'] = 4
    candidate['blocks'] = [candidate['blocks'][0],
                           DraftOperationStore._serialize_block(written_block),
                           DraftOperationStore._serialize_block(absent_block)]
    candidate['calendar_event_ids']['written-prep'] = 'written-prep-event'
    prep_written = {'kind': 'preparation', 'data': _prep_data(written_block,
                                                              DraftOperationStore._deserialize(candidate))}
    prep_absent = {'kind': 'preparation', 'data': _prep_data(absent_block,
                                                             DraftOperationStore._deserialize(candidate))}
    rows = {'old': old, 'new': {'kind': 'class', 'data': _data('new', 'New')},
            'written-prep': prep_written, 'absent-prep': prep_absent}
    payload = {'base_hash': digest(base), 'signature': 'sig', 'operation': candidate,
               'rows': rows, 'attempted': ['new', 'written-prep'],
               'applied': {'new': 'new-event', 'written-prep': 'written-prep-event'}}
    operation.pending_plan_update = payload
    document = {'version': 1, 'operations': [DraftOperationStore._serialize(operation)]}
    old_remote = _remote('old', 'old-event', 'old-v1', old['data'])
    new_data = rows['new']['data']
    new_remote = _remote('new', 'new-event', 'new-v1', new_data)
    written_remote = _remote('written-prep', 'written-prep-event', 'prep-v1', prep_written['data'])
    pairs = {'old': (old_remote, old_remote), 'new': (new_remote, new_remote),
             'written-prep': (written_remote, written_remote), 'absent-prep': (None, None)}
    if orphan_tombstone:
        pairs['orphan-deleted'] = (None, dict(tombstone, iCalUID='orphan-deleted'))
    return document, pairs


def test_digest_pin_fails_closed_before_interpreting_document():
    with pytest.raises(PartialCheckpointError, match='Document digest mismatch'):
        checkpoint({'operations': []}, {}, '0' * 64, COUNTS, '2026-11-06T00:00:00+00:00')


def test_success_adds_only_verified_row_and_keeps_old_row():
    document, pairs = _pending_document()
    result, audit = checkpoint(document, pairs, digest(document),
                               {'old_rows': 1, 'new_classes': 1, 'new_preparations': 0,
                                'absent_preparations': 0}, '2030-01-01T00:00:00+00:00')
    before, after = document['operations'][0], result['operations'][0]
    assert after['published_plan']['rows']['old'] == before['published_plan']['rows']['old']
    assert after['published_plan']['rows']['new']['event_id'] == 'new-event'
    assert after['published_plan']['recovery_history']
    assert audit['calendar_writes'] == 0
    assert not after['pending_plan_update'] and not after['projection_pending']


def test_ended_written_preparation_is_checkpointed_but_absent_one_is_not():
    document, pairs = _preparation_document()
    result, _ = checkpoint(document, pairs, digest(document),
                           {'old_rows': 1, 'new_classes': 1, 'new_preparations': 1,
                            'absent_preparations': 1}, '2030-01-01T00:00:00+00:00')
    before, after = document['operations'][0], result['operations'][0]
    assert after['blocks'][0] == before['blocks'][0]
    assert after['blocks'][1]['id'] == 'written-prep'
    assert all(block['id'] != 'absent-prep' for block in after['blocks'])
    assert after['calendar_event_ids']['old-prep'] == 'old-prep-event'
    assert after['calendar_event_ids']['written-prep'] == 'written-prep-event'
    assert 'absent-prep' not in after['calendar_event_ids']
    assert after['published_plan']['rows']['written-prep']['event_id'] == 'written-prep-event'
    assert 'absent-prep' not in after['published_plan']['rows']
    history = next(record for record in after['published_plan']['recovery_history'].values()
                   if record.get('kind') == 'verified-partial-checkpoint')
    assert history['operation_before'] == before
    assert history['record_format_version'] == 1
    assert history['expected_counts'] == {'old_rows': 1, 'new_classes': 1,
                                          'new_preparations': 1, 'absent_preparations': 1}
    assert 'row' not in history
    after_snapshot = history['operation_after_without_checkpoint_receipt']
    assert after_snapshot['version'] == 4
    assert after_snapshot['published_plan']['version'] == 4
    assert after_snapshot['content_hash'] == after['content_hash']
    assert after_snapshot['pending_plan_update'] == {}
    assert after_snapshot['projection_pending'] is False
    assert after_snapshot['blocks'] == after['blocks']
    assert after_snapshot['calendar_event_ids'] == after['calendar_event_ids']
    assert after_snapshot['published_plan']['rows'] == after['published_plan']['rows']
    assert after_snapshot['published_plan']['recovery_history'] == {'prior': {
        'kind': 'previous-record', 'marker': 'keep'}}
    assert after['published_plan']['recovery_history']['prior'] == {
        'kind': 'previous-record', 'marker': 'keep'}
    assert 'partial-update-' + history['operation_before_digest'][:20] not in \
        after_snapshot['published_plan']['recovery_history']
    assert after['content_hash'].startswith('operator-recovery:')
    assert after['content_hash'] != before['content_hash']


def test_wrong_preparation_block_metadata_fails_closed():
    from copy import deepcopy
    document, pairs = _preparation_document()
    document = deepcopy(document)
    raw = document['operations'][0]
    raw['pending_plan_update']['operation']['blocks'][1]['title'] = 'Tampered title'
    with pytest.raises(PartialCheckpointError, match='(?i)preparation projection data mismatch'):
        checkpoint(document, pairs, digest(document),
                   {'old_rows': 1, 'new_classes': 1, 'new_preparations': 1,
                    'absent_preparations': 1}, '2030-01-01T00:00:00+00:00')


def test_orphan_manual_deleted_tombstone_is_preserved_and_revalidated():
    document, pairs = _preparation_document(orphan_tombstone=True)
    result, _ = checkpoint(document, pairs, digest(document),
                           {'old_rows': 1, 'new_classes': 1, 'new_preparations': 1,
                            'absent_preparations': 1}, '2030-01-01T00:00:00+00:00')
    decision = result['operations'][0]['published_plan']['manual_resolutions']['orphan-deleted']
    assert decision == document['operations'][0]['published_plan']['manual_resolutions']['orphan-deleted']


@pytest.mark.parametrize('mutation', ['reappeared', 'changed_tombstone_etag'])
def test_orphan_manual_deleted_tombstone_mutation_rejected(mutation):
    from copy import deepcopy
    document, pairs = _preparation_document(orphan_tombstone=True)
    pairs = deepcopy(pairs)
    if mutation == 'reappeared':
        remote = _remote('orphan-deleted', 'gone-event', 'gone-v1', _data('orphan-deleted', 'Back'))
        pairs['orphan-deleted'] = (remote, remote)
    else:
        pairs['orphan-deleted'] = (None, {**pairs['orphan-deleted'][1], 'etag': 'gone-v2'})
    with pytest.raises(PartialCheckpointError):
        checkpoint(document, pairs, digest(document),
                   {'old_rows': 1, 'new_classes': 1, 'new_preparations': 1,
                    'absent_preparations': 1}, '2030-01-01T00:00:00+00:00')


def test_unattempted_but_existing_new_uid_is_rejected():
    from copy import deepcopy
    document, pairs = _pending_document()
    document, pairs = deepcopy(document), deepcopy(pairs)
    raw = document['operations'][0]
    rogue_data = _data('rogue', 'Unexpected')
    raw['pending_plan_update']['rows']['rogue'] = {'kind': 'class', 'data': rogue_data}
    remote = _remote('rogue', 'rogue-event', 'rogue-v1', rogue_data)
    pairs['rogue'] = (remote, remote)
    with pytest.raises(PartialCheckpointError):
        checkpoint(document, pairs, digest(document),
                   {'old_rows': 1, 'new_classes': 1, 'new_preparations': 0,
                    'absent_preparations': 0}, '2030-01-01T00:00:00+00:00')


@pytest.mark.parametrize('mutation', ['attempted_absent', 'id_mismatch', 'etag_mismatch',
                                      'ownership_mismatch', 'count_mismatch', 'changed_old_row'])
def test_adversarial_observations_and_journal_fail_closed(mutation):
    from copy import deepcopy
    document, pairs = _pending_document()
    document, pairs = deepcopy(document), deepcopy(pairs)
    counts = {'old_rows': 1, 'new_classes': 1, 'new_preparations': 0,
              'absent_preparations': 0}
    if mutation == 'attempted_absent':
        pairs['new'] = (None, None)
    elif mutation == 'id_mismatch':
        pairs['new'] = (_remote('new', 'other-id', 'new-v1', _data('new', 'New')),
                        pairs['new'][1])
    elif mutation == 'etag_mismatch':
        pairs['new'] = (pairs['new'][0], {**pairs['new'][1], 'etag': 'other-etag'})
    elif mutation == 'ownership_mismatch':
        bad = deepcopy(pairs['new'][0])
        bad['extendedProperties']['private']['personal_os_operation_id'] = 'other-op'
        pairs['new'] = (bad, bad)
    elif mutation == 'count_mismatch':
        counts['new_classes'] = 2
    elif mutation == 'changed_old_row':
        raw = document['operations'][0]
        raw['pending_plan_update']['rows']['old']['data']['summary'] = 'changed baseline'
        raw['pending_plan_update']['base_hash'] = digest(raw['published_plan'])
        raw['pending_plan_update']['operation'] = raw['pending_plan_update']['operation']
    with pytest.raises(PartialCheckpointError):
        checkpoint(document, pairs, digest(document), counts, '2030-01-01T00:00:00+00:00')


def test_wrong_shape_and_counts_are_explicit_errors():
    document = {'operations': []}
    with pytest.raises(PartialCheckpointError, match='Invalid expected counts'):
        checkpoint(document, {}, digest(document), {'old_rows': True}, 'now')
    with pytest.raises(PartialCheckpointError, match='Expected exactly one operation'):
        checkpoint(document, {}, digest(document), COUNTS, 'now')


def test_already_applied_detection_uses_exact_document_pin():
    record = {'kind': 'verified-partial-checkpoint', 'document_before_digest': 'a' * 64}
    document = {'operations': [{'published_plan': {'recovery_history': {'r': record}}}]}
    assert _already_done(document, 'a' * 64)
    assert not _already_done(document, 'b' * 64)


def test_atomic_writer_creates_private_file_and_roundtrips(tmp_path):
    target = tmp_path / 'draft_operations.json'
    payload = {'operations': [], 'private': 'synthetic'}
    _atomic_save(target, payload)
    assert json.loads(target.read_text()) == payload
    assert os.stat(target).st_mode & 0o777 == 0o600


def test_symlink_target_is_rejected_without_touching_target(tmp_path):
    real = tmp_path / 'real.json'
    real.write_text('{"keep":true}')
    link = tmp_path / 'draft_operations.json'
    link.symlink_to(real)
    with pytest.raises(PermissionError):
        _atomic_save(link, {'changed': True})
    assert json.loads(real.read_text()) == {'keep': True}


def test_batch_allowlist_checks_every_http_request_line():
    valid = ('Content-Type: application/http\r\n\r\n'
             'GET /calendar/v3/calendars/c/events/e HTTP/1.1\r\n\r\n')
    mixed_other = valid + ('Content-Type: application/http\r\n\r\n'
                           'POST /other/service HTTP/1.1\r\n\r\n')
    mixed_write = valid + ('Content-Type: application/http\r\n\r\n'
                           'PATCH /calendar/v3/calendars/c/events/e HTTP/1.1\r\n\r\n')
    absolute_write = 'PATCH https://www.googleapis.com/calendar/v3/events/e HTTP/1.1\r\n'
    assert _read_only_batch(valid)
    assert not _read_only_batch(mixed_other)
    assert not _read_only_batch(mixed_write)
    assert not _read_only_batch(absolute_write)


def test_cli_dry_run_apply_and_repeat_use_scoped_layout(tmp_path, monkeypatch, capsys):
    import scripts.checkpoint_partial_plan as cli
    from services.user_registry import UserRegistryStore

    state_root, google_root = tmp_path / 'state', tmp_path / 'google'
    (state_root).mkdir(mode=0o700)
    google_root.mkdir(mode=0o700)
    # Hash-binding fixture uses a synthetic numeric chat and its real opaque ID.
    chat = '101'
    user_id = UserRegistryStore._user_id(chat)
    scoped_root = state_root / user_id / 'state'
    user_dir = scoped_root / 'users' / user_id
    user_dir.mkdir(parents=True, mode=0o700)
    registry_path = scoped_root / 'users_registry.json'
    registry_path.parent.mkdir(mode=0o700, exist_ok=True)
    registry = UserRegistryStore(registry_path)
    registry._save([UserAccount(id=user_id, telegram_chat_id=chat)])
    (google_root / f'{user_id}.json').write_text('{}')
    os.chmod(google_root / f'{user_id}.json', 0o600)
    document, pairs = _pending_document()
    store = user_dir / 'draft_operations.json'
    store.write_text(json.dumps(document))
    os.chmod(store, 0o600)
    projection = {'version': 1, 'rows': {'new': {
        'calendar_id': 'calendar', 'event_id': 'new-event',
        'start': '2030-01-01T10:00:00+00:00', 'end': '2030-01-01T11:00:00+00:00'}}}
    projection_path = user_dir / 'university_calendar_projection.json'
    projection_path.write_text(json.dumps(projection))
    os.chmod(projection_path, 0o600)
    sentinel = user_dir / 'planning_profile.json'
    sentinel.write_text('{"synthetic":"unchanged"}')
    os.chmod(sentinel, 0o600)
    read_fail = {'enabled': False}

    class FakeAdapter:
        def __init__(self, config):
            self.service = SimpleNamespace(_http=SimpleNamespace(request=lambda *a, **k: None))

        async def initialize(self):
            return True

        def list_visible_calendars(self):
            return [{'id': 'calendar'}]

        def get_event_pairs(self, calendar, identities):
            assert calendar == 'calendar'
            if read_fail['enabled']:
                raise OSError('synthetic read failure')
            return {uid: pairs[uid] for uid in identities}

    monkeypatch.setattr('scripts.invited_beta_bot.configuration', lambda environ: (state_root, google_root))
    monkeypatch.setattr('scripts.closed_beta_bot.instance_lock', lambda root: nullcontext())
    monkeypatch.setattr('adapters.google_calendar_adapter.GoogleCalendarAdapter', FakeAdapter)
    fixed_files = [registry_path, google_root / f'{user_id}.json', projection_path, sentinel]
    fixed_before = {path: path.read_bytes() for path in fixed_files}
    read_fail['enabled'] = True
    monkeypatch.setattr('sys.argv', ['checkpoint_partial_plan.py', '--apply', '--expected-digest',
                                    digest(document), '--user-id', user_id, '--old-rows', '1',
                                    '--new-classes', '1', '--new-preparations', '0',
                                    '--absent-preparations', '0'])
    assert cli.main() == 1
    assert store.read_bytes() == json.dumps(document).encode()
    assert {path: path.read_bytes() for path in fixed_files} == fixed_before
    read_fail['enabled'] = False
    monkeypatch.setattr('sys.argv', ['checkpoint_partial_plan.py', '--expected-digest', digest(document),
                                    '--user-id', user_id, '--old-rows', '1', '--new-classes', '1',
                                    '--new-preparations', '0', '--absent-preparations', '0'])
    before = store.read_bytes()
    assert cli.main() == 0
    assert store.read_bytes() == before
    monkeypatch.setattr('sys.argv', ['checkpoint_partial_plan.py', '--apply', '--expected-digest',
                                    digest(document), '--user-id', user_id, '--old-rows', '1',
                                    '--new-classes', '1', '--new-preparations', '0',
                                    '--absent-preparations', '0'])
    assert cli.main() == 0
    assert os.stat(store).st_mode & 0o777 == 0o600
    assert {path: path.read_bytes() for path in fixed_files} == fixed_before
    saved = json.loads(store.read_text())
    assert saved['operations'][0]['published_plan']['rows']['new']['event_id'] == 'new-event'
    assert cli.main() == 0
    assert capsys.readouterr().out.count('CHECKPOINT ALREADY APPLIED') == 1
