"""Resume a journaled room/source migration without relaxing ownership checks."""
from copy import deepcopy

import pytest

from services.published_plan_updates import PlanConflict, decode
from tests.test_closed_beta_runtime import confirmation
from tests.test_published_plan_updates import ProcessCrash, setup


def migration(tmp_path):
    app, calendar, content, factory = setup(tmp_path)
    content['ics'] = content['ics'].replace(b'END:VEVENT', b'LOCATION:Room 109\r\nEND:VEVENT')
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    payload = app.pending[3]
    uid = next(uid for uid, row in payload['rows'].items() if row['kind'] == 'class')
    # Model the already reconciled room-derived source UID migration. Personal
    # identity stays fixed; source reconciliation itself is outside this test.
    payload['rows'][uid]['data']['system_source_event_id'] = 'source-room-109'
    return app, calendar, factory, preview, uid


def operation(app):
    return next(iter(app.operations.load_all().values()))


def write_target(calendar, saved, payload, uid):
    old = saved.published_plan['rows'][uid]
    calendar._update_event(saved.published_plan['calendar_id'], old['event_id'],
                           decode(payload['rows'][uid]['data']), strict=True, expected_etag=old['etag'])


@pytest.mark.parametrize('receipt', [False, True])
def test_room_source_migration_crash_resume_finalizes_without_second_patch(tmp_path, monkeypatch, receipt):
    app, calendar, factory, preview, uid = migration(tmp_path)
    if receipt:
        # Exercise the production-style batched UID/ID lookup as well as the
        # synthetic adapter's separate UID/list-by-ID fallback.
        def pairs(calendar_id, identities):
            return {key: (calendar.get_event_by_uid(calendar_id, key, strict=True),
                          deepcopy(calendar.remote.get((calendar_id, event_id))))
                    for key, event_id in identities.items()}
        monkeypatch.setattr(calendar, 'get_event_pairs', pairs, raising=False)
    old = deepcopy(operation(app).published_plan['rows'][uid])
    # The fresh prewrite guard still accepts the old source marker and ETag.
    assert app.updates.validate(operation(app), app.pending[3])[uid]['etag'] == old['etag']
    calendar.crash_after_write = True
    with pytest.raises(ProcessCrash):
        app.handle_callback('101', confirmation(preview))
    saved = operation(app)
    assert saved.pending_plan_update['attempted'] == [uid]
    assert uid not in saved.pending_plan_update['applied']
    if receipt:
        saved.pending_plan_update['applied'][uid] = old['event_id']
        app.operations.save(saved)
    writes = list(calendar.writes)
    remote_ids = set(calendar.remote)
    assert calendar.update_count == 1
    restarted = factory(tmp_path, '101', calendar)
    continuation = restarted.handle_text('101', '/weekly_preview')
    assert 'продолжение частичной записи' in continuation['text'], continuation
    assert 'обновлён и проверен' in restarted.handle_callback('101', confirmation(continuation))['text']
    final = operation(restarted)
    assert final.version == 2 and not final.pending_plan_update and not final.projection_pending
    row = final.published_plan['rows'][uid]
    assert row['event_id'] == old['event_id'] and row['etag'] != old['etag']
    assert row['data']['system_source_event_id'] == 'source-room-109'
    assert row['data']['location'] == 'Room 109'
    # The room also changes preparation metadata; its first patch may still be
    # pending. The already completed class patch must never run a second time.
    assert calendar.writes.count(('update', uid)) == writes.count(('update', uid)) == 1
    assert set(calendar.remote) == remote_ids
    writes = list(calendar.writes)
    assert not restarted.handle_text('101', '/weekly_preview')['buttons']
    assert calendar.writes == writes


def test_attempt_journal_before_patch_still_requires_the_original_etag(tmp_path):
    app, calendar, _, _, uid = migration(tmp_path)
    saved = operation(app)
    payload = app.pending[3]
    payload['attempted'] = [uid]
    old = saved.published_plan['rows'][uid]
    assert app.updates.validate(saved, payload)[uid]['etag'] == old['etag']
    calendar.remote[(saved.published_plan['calendar_id'], old['event_id'])]['etag'] = 'unreviewed-version'
    with pytest.raises(PlanConflict):
        app.updates.validate(saved, payload)


@pytest.mark.parametrize('failure', [
    'no_attempt', 'no_payload', 'block', 'operation', 'uid', 'source_unexpected',
    'source_empty', 'summary', 'description', 'location', 'time', 'pair_target',
    'uid_missing', 'id_missing', 'uid_wrong_id', 'id_wrong_id',
    'etag_missing_uid', 'etag_missing_id', 'etag_different', 'cancelled',
    'manual_moved', 'manual_deleted', 'preparation', 'desired_preparation', 'old_etag_missing',
])
def test_source_transition_requires_exact_journaled_class_target(tmp_path, monkeypatch, failure):
    app, calendar, _, _, uid = migration(tmp_path)
    saved = operation(app)
    payload = deepcopy(app.pending[3])
    payload['attempted'] = [uid]
    target = payload['rows'][uid]
    if failure in {'block', 'operation', 'uid'}:
        key = {'block': 'system_block_id', 'operation': 'system_operation_id', 'uid': 'uid'}[failure]
        target['data'][key] = 'foreign-identity'
    if failure == 'source_empty':
        target['data']['system_source_event_id'] = ''
    write_target(calendar, saved, payload, uid)
    old = saved.published_plan['rows'][uid]
    event = deepcopy(calendar.remote[(saved.published_plan['calendar_id'], old['event_id'])])
    by_id = deepcopy(event)
    if failure == 'no_attempt':
        payload['attempted'] = []
    elif failure == 'source_unexpected':
        event['extendedProperties']['private']['personal_os_source_event_id'] = 'unreviewed-source'
        by_id = deepcopy(event)
    elif failure in {'summary', 'description', 'location'}:
        event[failure] = 'manual edit'
        by_id = deepcopy(event)
    elif failure == 'time':
        event['start']['dateTime'] = '2026-09-24T11:00:00+07:00'
        by_id = deepcopy(event)
    elif failure == 'pair_target':
        by_id['summary'] = 'manual edit on ID route'
    elif failure == 'uid_missing':
        event = None
    elif failure == 'id_missing':
        by_id = None
    elif failure == 'uid_wrong_id':
        event['id'] = 'foreign-google-id'
    elif failure == 'id_wrong_id':
        by_id['id'] = 'foreign-google-id'
    elif failure == 'etag_missing_uid':
        event.pop('etag')
    elif failure == 'etag_missing_id':
        by_id.pop('etag')
    elif failure == 'etag_different':
        by_id['etag'] = 'other-version'
    elif failure == 'cancelled':
        event['status'] = by_id['status'] = 'cancelled'
    elif failure == 'manual_moved':
        saved.published_plan['manual_resolutions'] = {uid: {'kind': 'moved', 'row': deepcopy(old)}}
    elif failure == 'manual_deleted':
        saved.published_plan['manual_resolutions'] = {uid: {'kind': 'deleted'}}
    elif failure == 'preparation':
        old['kind'] = target['kind'] = 'preparation'
    elif failure == 'desired_preparation':
        target['kind'] = 'preparation'
    elif failure == 'old_etag_missing':
        old.pop('etag')

    def pairs(calendar_id, identities):
        result = {}
        for key, event_id in identities.items():
            result[key] = ((event, by_id) if key == uid else (
                calendar.get_event_by_uid(calendar_id, key, strict=True),
                deepcopy(calendar.remote.get((calendar_id, event_id)))))
        return result

    monkeypatch.setattr(calendar, 'get_event_pairs', pairs, raising=False)
    before = list(calendar.writes)
    with pytest.raises(PlanConflict):
        app.updates.validate(saved, None if failure == 'no_payload' else payload)
    assert calendar.writes == before
