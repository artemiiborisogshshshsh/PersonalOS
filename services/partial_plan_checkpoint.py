"""Strict, offline checkpointing for one interrupted add-only plan update."""
from copy import deepcopy
from types import SimpleNamespace

from services.adaptive_preparation_service import DraftCalendarProjector
from services.draft_operation_store import DraftOperationStore
from services.published_plan_updates import PublishedPlanUpdates, digest, encode


class PartialCheckpointError(ValueError):
    """The saved operation or remote observations are not safe to checkpoint."""


class _ObservedAdapter:
    def __init__(self, calendar, pairs):
        self.calendar, self.pairs = calendar, pairs

    def list_visible_calendars(self):
        return [{'id': self.calendar}]

    def get_event_pairs(self, calendar, identities):
        if calendar != self.calendar or set(identities) - set(self.pairs):
            raise PartialCheckpointError('Incomplete read-only Calendar observations')
        for uid, event_id in identities.items():
            pair = self.pairs[uid]
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise PartialCheckpointError('Malformed Calendar observation')
            for item in pair:
                if item is not None and event_id and item.get('id') != event_id:
                    raise PartialCheckpointError('Calendar identity mismatch')
        return {uid: self.pairs[uid] for uid in identities}


def _require(condition, message):
    if not condition:
        raise PartialCheckpointError(message)


def _counts(counts):
    required = {'old_rows', 'new_classes', 'new_preparations', 'absent_preparations'}
    _require(isinstance(counts, dict) and set(counts) == required, 'Invalid expected counts')
    _require(all(type(value) is int and value >= 0 for value in counts.values()), 'Invalid expected counts')


def checkpoint(document, pairs, expected_document_digest, expected_counts, at):
    """Return (new serialized document, audit) without mutating inputs or writing files."""
    _counts(expected_counts)
    _require(digest(document) == expected_document_digest, 'Document digest mismatch')
    _require(isinstance(document, dict) and isinstance(document.get('operations'), list)
             and len(document['operations']) == 1, 'Expected exactly one operation')
    original = deepcopy(document['operations'][0])
    try:
        op = DraftOperationStore._deserialize(original)
    except Exception as error:
        raise PartialCheckpointError('Operation cannot be deserialized') from error
    pending = op.pending_plan_update
    _require(op.status == 'confirmed' and op.projection_pending is True, 'Operation is not pending projection')
    _require(isinstance(pending, dict) and set(pending) ==
             {'base_hash', 'signature', 'operation', 'rows', 'attempted', 'applied'}, 'Unknown pending operation shape')
    base = op.published_plan
    _require(pending['base_hash'] == digest(base), 'Published baseline digest mismatch')
    _require(op.pending_calendar_writes == {}, 'Pending Calendar writes are present')
    candidate_data = pending['operation']
    try:
        candidate = DraftOperationStore._deserialize(candidate_data)
    except Exception as error:
        raise PartialCheckpointError('Candidate cannot be deserialized') from error
    _require(candidate.id == op.id and candidate.version == op.version + 1, 'Candidate identity or version mismatch')
    _require(candidate.pending_calendar_writes == {}, 'Candidate contains pending Calendar writes')
    rows = pending['rows']
    _require(isinstance(rows, dict) and isinstance(pending['applied'], dict)
             and isinstance(pending['attempted'], list), 'Malformed pending rows')
    _require(len(pending['attempted']) == len(set(pending['attempted'])), 'Duplicate attempted identity')
    _require(set(pending['attempted']) == set(pending['applied']), 'Ambiguous attempted rows')
    old_rows = base.get('rows')
    _require(isinstance(old_rows, dict) and set(old_rows).issubset(rows), 'Pending update removed baseline rows')
    _require(all(rows[uid] == row for uid, row in old_rows.items()), 'Baseline row changed in pending update')
    verifier = PublishedPlanUpdates(SimpleNamespace(adapter=_ObservedAdapter(base['calendar_id'], pairs)))
    try:
        verifier.validate(op, pending)
    except Exception as error:
        raise PartialCheckpointError('Saved baseline or pending plan failed validation') from error

    out = deepcopy(op)
    new_ids, absent = [], []
    class_count = prep_count = 0
    _require(set(rows).issubset(pairs), 'Observation set does not cover pending rows')
    for uid, row in rows.items():
        if uid in old_rows:
            continue
        _require(row.get('kind') in {'class', 'preparation'}, 'Unknown pending row kind')
        _require(isinstance(row.get('data'), dict) and row['data'].get('uid') == uid,
                 'Pending row UID mismatch')
        by_uid, by_id = pairs[uid]
        _require(by_uid is None or isinstance(by_uid, dict), 'Malformed UID observation')
        _require(by_id is None or isinstance(by_id, dict), 'Malformed ID observation')
        if by_uid is None:
            _require(by_id is None and uid not in pending['attempted'] and uid not in pending['applied'],
                     'Attempted or conflicting row is absent')
            _require(row['kind'] == 'preparation', 'Only unattempted preparations may be absent')
            absent.append(uid)
            continue
        _require(uid in pending['attempted'] and uid in pending['applied'], 'Observed write lacks exact journal entry')
        _require(by_id is not None and by_uid.get('status') != 'cancelled', 'Written row is missing or cancelled')
        _require(by_uid.get('id') == by_id.get('id') == pending['applied'][uid], 'Observed ID differs from applied ID')
        _require(by_uid.get('etag') and by_uid['etag'] == by_id.get('etag'), 'Observed ETags differ')
        _require(PublishedPlanUpdates.matches(by_uid, row['data']) and
                 PublishedPlanUpdates.matches(by_id, row['data']), 'Observed Calendar data or ownership mismatch')
        saved = deepcopy(row)
        saved.update(event_id=by_uid['id'], etag=by_uid['etag'])
        _require(uid not in out.published_plan['rows'], 'New UID already exists in baseline')
        out.published_plan['rows'][uid] = saved
        new_ids.append(uid)
        if row['kind'] == 'preparation':
            matches = [item for item in candidate.blocks if item.id == uid]
            _require(len(matches) == 1 and all(item.id != uid for item in op.blocks),
                     'Preparation block identity mismatch')
            block = matches[0]
            generated = DraftCalendarProjector._event_data(block, candidate)
            generated_data = encode({key: getattr(generated, key) for key in row['data']})
            _require(generated_data == row['data'], 'Preparation projection data mismatch')
            _require(candidate.calendar_event_ids.get(uid) == by_uid['id'], 'Preparation event ID mismatch')
            out.blocks.append(deepcopy(block))
            out.calendar_event_ids[uid] = by_uid['id']
            prep_count += 1
        else:
            class_count += 1

    _require(class_count == expected_counts['new_classes'] and prep_count == expected_counts['new_preparations']
             and len(absent) == expected_counts['absent_preparations']
             and len(old_rows) == expected_counts['old_rows'], 'Checkpoint counts mismatch')
    _require(set(pending['applied']) == set(new_ids), 'Applied journal includes non-new rows')
    history_key = 'partial-update-' + digest(original)[:20]
    history = out.published_plan.setdefault('recovery_history', {})
    _require(history_key not in history, 'Recovery history key collision')
    recovery_record = {
        'record_format_version': 1,
        'kind': 'verified-partial-checkpoint', 'verified_at': at,
        'document_before_digest': expected_document_digest,
        'operation_before_digest': digest(original), 'operation_before': deepcopy(original),
        'verified_rows': new_ids, 'unattempted_absent_rows': absent,
        'observations': deepcopy(pairs), 'expected_counts': deepcopy(expected_counts),
        'calendar_writes': 0,
    }
    history[history_key] = recovery_record
    out.version = candidate.version
    out.published_plan['version'] = candidate.version
    out.content_hash = 'operator-recovery:' + digest(original)
    out.pending_plan_update = {}
    out.projection_pending = False
    try:
        verifier.validate(out)
        after_without_receipt = deepcopy(out)
        del after_without_receipt.published_plan['recovery_history'][history_key]
        recovery_record['operation_after_without_checkpoint_receipt'] = \
            DraftOperationStore._serialize(after_without_receipt)
        serialized = DraftOperationStore._serialize(out)
        _require(DraftOperationStore._serialize(DraftOperationStore._deserialize(serialized)) == serialized,
                 'Operation serializer roundtrip mismatch')
    except PartialCheckpointError:
        raise
    except Exception as error:
        raise PartialCheckpointError('Checkpoint failed final validation') from error
    result = deepcopy(document)
    result['operations'][0] = serialized
    _require(all(serialized['published_plan']['rows'][uid] == row for uid, row in old_rows.items()),
             'Baseline rows were not preserved')
    original_blocks = original.get('blocks', [])
    _require(serialized['blocks'][:len(original_blocks)] == original_blocks,
             'Existing preparation blocks changed')
    original_event_ids = original.get('calendar_event_ids', {})
    _require(all(serialized['calendar_event_ids'].get(uid) == event_id
                 for uid, event_id in original_event_ids.items()), 'Existing calendar event IDs changed')
    for key, value in original.items():
        if key not in {'version', 'content_hash', 'published_plan', 'pending_plan_update',
                       'projection_pending', 'blocks', 'calendar_event_ids'}:
            _require(serialized[key] == value, 'Operation metadata changed')
    for key, value in base.items():
        if key not in {'version', 'rows', 'recovery_history'}:
            _require(serialized['published_plan'][key] == value, 'Published-plan metadata changed')
    for key, value in base.get('recovery_history', {}).items():
        _require(serialized['published_plan']['recovery_history'].get(key) == value,
                 'Existing recovery history changed')
    audit = {'document_before_digest': expected_document_digest, 'document_after_digest': digest(result),
             'operation_before_digest': digest(original), 'old_rows': len(old_rows),
             'verified_new_rows': len(new_ids), 'absent_preparations': len(absent),
             'new_preparation_blocks': prep_count, 'calendar_writes': 0}
    return result, audit
