"""Legacy confirmed deletions remain durable even if their old row was retired."""
from copy import deepcopy
import pytest

from services.published_plan_updates import digest
from tests.test_closed_beta_runtime import confirmation
from tests.test_published_plan_deletions import setup


def published(app):
    return next(iter(app.operations.load_all().values()))


def archived_by_id_deletion(tmp_path, monkeypatch, *, batched=False, both_routes=False):
    app, calendar, content, _ = setup(tmp_path)
    operation = published(app)
    uid, row = next((uid, row) for uid, row in operation.published_plan['rows'].items()
                    if row['kind'] == 'class')
    key = (operation.published_plan['calendar_id'], row['event_id'])
    calendar.remote.pop(key)
    observation = {'by_uid': None, 'by_id': {
        'id': row['event_id'], 'status': 'cancelled', 'etag': 'deleted-v1',
    }}
    if both_routes:
        observation['by_uid'] = {**observation['by_id'], 'iCalUID': uid}
    original_by_uid = calendar.get_event_by_uid
    original_by_id = calendar.get_event_by_id

    def read_by_uid(calendar_id, candidate_uid, **kwargs):
        if (calendar_id, candidate_uid) == (key[0], uid):
            return deepcopy(observation['by_uid'])
        return original_by_uid(calendar_id, candidate_uid, **kwargs)

    def read_by_id(calendar_id, event_id, **kwargs):
        if (calendar_id, event_id) == key:
            return deepcopy(observation['by_id'])
        return original_by_id(calendar_id, event_id, **kwargs)

    monkeypatch.setattr(calendar, 'get_event_by_uid', read_by_uid)
    monkeypatch.setattr(calendar, 'get_event_by_id', read_by_id)
    review = app.handle_text('101', '/review_calendar')
    assert 'приняты и сохранены' in app.handle_callback('101', confirmation(review))['text']
    operation = published(app)
    operation.published_plan['rows'].pop(uid)
    app.operations.save(operation)

    if batched:
        def read_pairs(calendar_id, identities, *, require_matching_uid=False, retry_transient=True):
            if require_matching_uid:
                assert not retry_transient and identities == {uid: key[1]}
            return {candidate_uid: (read_by_uid(calendar_id, candidate_uid),
                                    read_by_id(calendar_id, event_id) if event_id else None)
                    for candidate_uid, event_id in identities.items()}

        def preview_pairs(calendar_id, identities, *, exact_uids):
            assert uid in exact_uids and identities[uid] == key[1]
            return read_pairs(calendar_id, identities)

        monkeypatch.setattr(calendar, 'get_event_pairs', read_pairs, raising=False)
        monkeypatch.setattr(calendar, 'get_preview_event_pairs', preview_pairs, raising=False)
    return app, calendar, content, uid, observation


@pytest.mark.parametrize('batched', [False, True])
def test_archived_deletion_etag_drift_keeps_proposed_plan_read_only(tmp_path, monkeypatch, batched):
    app, calendar, content, uid, observation = archived_by_id_deletion(
        tmp_path, monkeypatch, batched=batched,
    )
    content['ics'] = content['ics'].replace(b'20260925T030000Z', b'20260925T040000Z').replace(
        b'20260925T043000Z', b'20260925T053000Z',
    )
    operation = published(app)
    journal = deepcopy(operation.published_plan['manual_resolutions'][uid])
    observation['by_id']['etag'] = 'deleted-v2'
    writes = list(calendar.writes)

    preview = app.handle_text('101', '/update_schedule')

    assert 'Обновление плана' in preview['text'], preview
    assert 'Перенести: ОС (ЛК)' in preview['text']
    assert 'сверить архивную отметку об удалении' in preview['text']
    assert not preview['buttons'] and app.pending is None
    assert published(app).published_plan['manual_resolutions'][uid] == journal
    assert uid not in published(app).published_plan['rows']
    assert not published(app).pending_plan_update
    assert calendar.writes == writes


@pytest.mark.parametrize('batched', [False, True])
def test_reconcile_archived_deletion_restores_a_manual_preview_after_pausing_background(
        tmp_path, monkeypatch, batched):
    app, calendar, content, uid, observation = archived_by_id_deletion(
        tmp_path, monkeypatch, batched=batched,
    )
    unrelated_uid = 'other-archived-deletion'
    operation = published(app)
    operation.published_plan['manual_resolutions'][unrelated_uid] = {
        'kind': 'deleted', 'observed': {'by_uid': None, 'by_id': None},
    }
    app.operations.save(operation)
    content['ics'] = content['ics'].replace(b'20260925T030000Z', b'20260925T040000Z').replace(
        b'20260925T043000Z', b'20260925T053000Z',
    )
    observation['by_id']['etag'] = 'deleted-v2'
    assert not app.handle_text('101', '/update_schedule')['buttons']
    writes = list(calendar.writes)
    save_operation = app.operations.save
    saves = []

    def paused_save(updated):
        assert app.access._state()['resume_required']
        saves.append(updated.id)
        save_operation(updated)

    monkeypatch.setattr(app.operations, 'save', paused_save)
    calendar.is_initialized = False
    command = '/review_calendar archive ' + digest(uid)[:12]
    result = app.handle_text('101', command)

    assert 'сверена и сохранена локально' in result['text'], result
    assert not result['buttons'] and app.pending is None
    assert calendar.is_initialized
    operation.published_plan['manual_resolutions'][uid]['observed']['by_id']['etag'] = 'deleted-v2'
    assert published(app) == operation
    assert saves == [operation.id]
    access_state = app.access._state()
    assert 'уже соответствует' in app.handle_text('101', command)['text']
    assert saves == [operation.id] and app.access._state() == access_state

    def unexpected_background(*args, **kwargs):
        pytest.fail('Background writes must stay paused through manual preview')

    with monkeypatch.context() as background:
        background.setattr(app.learning, 'refresh', unexpected_background)
        assert app.background_tick(lambda *_: None, lambda *_: None) == []
    preview = app.handle_text('101', '/update_schedule')
    assert 'Перенести: ОС (ЛК)' in preview['text'], preview
    assert preview['buttons'][0][0]['text'] == 'Подтвердить обновление'
    assert app.access.fresh_token == app.pending[0]
    with monkeypatch.context() as background:
        background.setattr(app.learning, 'refresh', unexpected_background)
        assert app.background_tick(lambda *_: None, lambda *_: None) == []
    assert calendar.writes == writes
    assert published(app) == operation


def test_reconcile_archived_deletion_requires_two_equal_observations(tmp_path, monkeypatch):
    app, calendar, _, uid, observation = archived_by_id_deletion(tmp_path, monkeypatch)
    operation = published(app)
    writes = list(calendar.writes)
    read_by_id = calendar.get_event_by_id
    versions = iter(('deleted-v2', 'deleted-v3'))

    def changed_read(calendar_id, event_id, *, strict):
        assert strict and calendar_id == operation.published_plan['calendar_id']
        assert event_id == observation['by_id']['id']
        return {**read_by_id(calendar_id, event_id, strict=strict), 'etag': next(versions)}

    monkeypatch.setattr(calendar, 'get_event_by_id', changed_read)
    result = app.handle_text('101', '/review_calendar archive ' + digest(uid)[:12])

    assert 'изменилась во время проверки' in result['text'], result
    assert not result['buttons'] and published(app) == operation
    assert not app.access._state().get('resume_required')
    assert calendar.writes == writes


def test_reconcile_current_archive_pauses_background_without_rewriting_the_journal(tmp_path, monkeypatch):
    app, calendar, _, uid, _ = archived_by_id_deletion(tmp_path, monkeypatch)
    operation = published(app)
    writes = list(calendar.writes)

    def unexpected_save(_):
        pytest.fail('A current archive must not be rewritten')

    monkeypatch.setattr(app.operations, 'save', unexpected_save)
    assert not app.access._state().get('resume_required')
    command = '/review_calendar archive ' + digest(uid)[:12]
    result = app.handle_text('101', command)

    assert 'уже соответствует' in result['text'] and 'приостановлены' in result['text'], result
    paused = app.access._state()
    assert paused['resume_required']
    assert app.handle_text('101', command) == result
    assert app.access._state() == paused
    assert published(app) == operation and calendar.writes == writes


def test_reconcile_archive_checks_scope_and_journals_before_calendar(tmp_path, monkeypatch):
    app, calendar, _, uid, _ = archived_by_id_deletion(tmp_path, monkeypatch)
    command = '/review_calendar archive ' + digest(uid)[:12]
    operation = published(app)
    writes = list(calendar.writes)

    def unexpected_calendar():
        pytest.fail('Calendar must only be consulted for an eligible journal')

    monkeypatch.setattr(app, '_connected', unexpected_calendar)
    assert app.handle_text('102', command) is None
    for invalid in ('/review_calendar archive', command + ' extra', '/review_calendar archive 2g'):
        assert 'код архивной записи' in app.handle_text('101', invalid)['text']
    assert 'однозначно найти' in app.handle_text('101', '/review_calendar archive 123456abcdef')['text']
    with monkeypatch.context() as unauthorised:
        unauthorised.setattr(app.access, 'allowed', lambda: False)
        assert app.access.DENIED in app.handle_text('101', command)['text']
    for field, value in (('pending_plan_update', {'kind': 'learning'}), ('projection_pending', True),
                         ('pending_calendar_writes', {uid: 'pending'})):
        pending = deepcopy(operation)
        setattr(pending, field, value)
        app.operations.save(pending)
        assert 'Сначала заверши' in app.handle_text('101', command)['text']
        assert published(app) == pending

    ambiguous = deepcopy(operation)
    decisions = ambiguous.published_plan['manual_resolutions']
    decisions['second-decision'] = deepcopy(decisions[uid])
    app.operations.save(ambiguous)
    monkeypatch.setattr('services.published_plan_updates.digest', lambda _: digest(uid))
    assert 'однозначно найти' in app.handle_text('101', command)['text']
    assert published(app) == ambiguous
    assert not app.access._state().get('resume_required')
    assert calendar.writes == writes


def test_archived_deletion_etag_drift_unchanged_plan_cannot_resume_writes(tmp_path, monkeypatch):
    app, calendar, _, uid, observation = archived_by_id_deletion(tmp_path, monkeypatch)
    _, _, _, signature, _ = app._inputs()
    operation = published(app)
    operation.content_hash = signature
    app.operations.save(operation)
    access_state = app.access._state()
    access_state['resume_required'] = True
    app.access._save(access_state)
    observation['by_id']['etag'] = 'deleted-v2'
    writes = list(calendar.writes)

    preview = app.handle_text('101', '/update_schedule')

    assert 'уже опубликован' in preview['text'].lower(), preview
    assert 'Изменений и повторных записей нет' in preview['text']
    assert 'сверить архивную отметку об удалении' in preview['text']
    assert not preview['buttons'] and app.pending is None
    assert app.access._state()['resume_required']
    assert published(app).published_plan['manual_resolutions'][uid] == (
        operation.published_plan['manual_resolutions'][uid]
    )
    assert calendar.writes == writes


def test_archived_deletion_etag_drift_does_not_change_the_pending_plan(tmp_path, monkeypatch):
    app, calendar, _, _, observation = archived_by_id_deletion(tmp_path, monkeypatch)
    preview = app.handle_text('101', '/update_schedule')
    assert preview['buttons'], preview
    payload = deepcopy(app.pending[3])
    operation = published(app)
    operation.pending_plan_update = deepcopy(payload)
    operation.projection_pending = True
    app.operations.save(operation)

    prepare_restores = app.updates.prepare_attendance_restores

    def add_preview_metadata(operation, payload):
        prepare_restores(operation, payload)
        payload['attendance_restores'] = {}

    monkeypatch.setattr(app.updates, 'prepare_attendance_restores', add_preview_metadata)
    observation['by_id']['etag'] = 'deleted-v2'
    writes = list(calendar.writes)

    preview = app.handle_text('101', '/update_schedule')

    assert 'продолжение частичной записи' in preview['text']
    assert 'сверить архивную отметку об удалении' in preview['text']
    assert not preview['buttons'] and app.pending is None
    assert published(app).pending_plan_update == payload
    assert published(app).projection_pending
    assert calendar.writes == writes


@pytest.mark.parametrize('unsafe', ['active', 'id', 'missing', 'new_route', 'uid', 'blank_etag'])
def test_archived_deletion_etag_preview_still_blocks_unsafe_observations(tmp_path, monkeypatch, unsafe):
    app, calendar, _, uid, observation = archived_by_id_deletion(tmp_path, monkeypatch)
    operation = published(app)
    observation['by_id']['etag'] = 'deleted-v2'
    if unsafe == 'active':
        observation['by_id']['status'] = 'confirmed'
    elif unsafe == 'id':
        observation['by_id']['id'] = 'replacement-id'
    elif unsafe == 'missing':
        observation['by_id'] = None
    elif unsafe == 'new_route':
        observation['by_uid'] = {**observation['by_id'], 'iCalUID': uid}
    elif unsafe == 'uid':
        observation['by_id']['iCalUID'] = 'other-uid'
    else:
        observation['by_id']['etag'] = '  '
    writes = list(calendar.writes)

    preview = app.handle_text('101', '/update_schedule')

    assert 'Ранее удалённая идентичность изменилась' in preview['text'], preview
    assert not preview['buttons'] and app.pending is None
    result = app.handle_text('101', '/review_calendar archive ' + digest(uid)[:12])
    assert 'другие сведения об удалении' in result['text'], result
    assert not result['buttons'] and published(app) == operation
    assert not app.access._state().get('resume_required')
    assert calendar.writes == writes


def test_archived_deletion_etag_preview_rejects_conflicting_uid_on_the_other_route(tmp_path, monkeypatch):
    app, calendar, _, uid, observation = archived_by_id_deletion(tmp_path, monkeypatch, both_routes=True)
    operation = published(app)
    observation['by_uid']['etag'] = 'deleted-v2'
    observation['by_id']['iCalUID'] = 'other-uid'
    writes = list(calendar.writes)

    preview = app.handle_text('101', '/update_schedule')

    assert 'Ранее удалённая идентичность изменилась' in preview['text'], preview
    assert not preview['buttons'] and app.pending is None
    result = app.handle_text('101', '/review_calendar archive ' + digest(uid)[:12])
    assert 'другие сведения об удалении' in result['text'], result
    observation['by_id']['iCalUID'] = uid
    observation['by_uid']['iCalUID'] = 'other-uid'
    result = app.handle_text('101', '/review_calendar archive ' + digest(uid)[:12])
    assert 'другие сведения об удалении' in result['text'], result
    observation['by_uid']['iCalUID'] = uid
    result = app.handle_text('101', '/review_calendar archive ' + digest(uid)[:12])
    assert 'вернули разные версии' in result['text'], result
    assert published(app) == operation and not app.access._state().get('resume_required')
    assert calendar.writes == writes


def test_apply_still_rejects_archived_deletion_etag_changed_after_preview(tmp_path, monkeypatch):
    app, calendar, _, _, observation = archived_by_id_deletion(tmp_path, monkeypatch)
    preview = app.handle_text('101', '/update_schedule')
    assert preview['buttons'], preview
    observation['by_id']['etag'] = 'deleted-v2'
    writes = list(calendar.writes)

    result = app.handle_callback('101', confirmation(preview))

    assert 'изменилась версия отметки об удалении' in result['text'], result
    assert not published(app).pending_plan_update
    assert calendar.writes == writes


def test_orphaned_confirmed_deletion_stays_excluded_across_preview_apply_restart(tmp_path):
    app, calendar, _, factory = setup(tmp_path)
    operation = published(app)
    uid, row = next((uid, row) for uid, row in operation.published_plan['rows'].items()
                    if row['kind'] == 'class')

    calendar.remote.pop((operation.published_plan['calendar_id'], row['event_id']))
    review = app.handle_text('101', '/review_calendar')
    assert 'приняты и сохранены' in app.handle_callback('101', confirmation(review))['text']

    operation = published(app)
    journal = deepcopy(operation.published_plan['manual_resolutions'][uid])
    # The legacy archived form predates source_identity on accepted decisions.
    journal.pop('source_identity', None)
    operation.published_plan['manual_resolutions'][uid] = deepcopy(journal)
    operation.published_plan['rows'].pop(uid)
    app.operations.save(operation)
    writes = list(calendar.writes)

    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert calendar.writes == writes
    assert uid not in app.pending[3]['rows']

    result = app.handle_callback('101', confirmation(preview))
    assert 'обновлён и проверен' in result['text'].lower(), result
    assert ('insert', uid) not in calendar.writes[len(writes):]
    updated = published(app)
    assert uid not in updated.published_plan['rows']
    assert updated.published_plan['manual_resolutions'][uid] == journal
    assert (updated.published_plan['calendar_id'], row['event_id']) not in calendar.remote

    restarted = factory(tmp_path)
    writes = list(calendar.writes)
    repeated = restarted.handle_text('101', '/weekly_preview')
    assert 'повторных записей нет' in repeated['text']
    assert calendar.writes == writes
    assert published(restarted).published_plan['manual_resolutions'][uid] == journal


def test_orphaned_deleted_identity_reappearance_conflicts_before_writes(tmp_path):
    app, calendar, _, _ = setup(tmp_path)
    operation = published(app)
    uid, row = next((uid, row) for uid, row in operation.published_plan['rows'].items()
                    if row['kind'] == 'class')
    calendar.remote.pop((operation.published_plan['calendar_id'], row['event_id']))
    review = app.handle_text('101', '/review_calendar')
    app.handle_callback('101', confirmation(review))
    operation = published(app)
    operation.published_plan['rows'].pop(uid)
    app.operations.save(operation)
    writes = list(calendar.writes)

    # A replacement under the same UID is ambiguous because the orphaned
    # journal no longer carries an event ID to compare.
    replacement = deepcopy(row)
    calendar.remote[(operation.published_plan['calendar_id'], 'replacement-id')] = {
        'id': 'replacement-id', 'iCalUID': uid, 'etag': 'replacement-v1',
        'status': 'confirmed', 'extendedProperties': {'private': {}},
    }
    preview = app.handle_text('101', '/weekly_preview')
    assert not preview['buttons']
    assert 'идентичность' in preview['text'].lower() or 'удал' in preview['text'].lower()
    assert calendar.writes == writes


def test_orphaned_deleted_malformed_journal_is_actionable_conflict(tmp_path):
    app, _, _, _ = setup(tmp_path)
    operation = published(app)
    uid = next(uid for uid, row in operation.published_plan['rows'].items() if row['kind'] == 'class')
    operation.published_plan['rows'].pop(uid)
    operation.published_plan.setdefault('manual_resolutions', {})[uid] = {'kind': 'deleted'}
    app.operations.save(operation)

    preview = app.handle_text('101', '/weekly_preview')
    assert not preview['buttons']
    assert 'журнал' in preview['text'].lower() or 'неоднозначно' in preview['text'].lower()


def test_orphaned_moved_journal_without_saved_row_is_actionable_conflict(tmp_path):
    app, _, _, _ = setup(tmp_path)
    operation = published(app)
    uid = next(uid for uid, row in operation.published_plan['rows'].items() if row['kind'] == 'class')
    operation.published_plan['rows'].pop(uid)
    operation.published_plan.setdefault('manual_resolutions', {})[uid] = {'kind': 'moved'}
    app.operations.save(operation)

    preview = app.handle_text('101', '/weekly_preview')
    assert not preview['buttons']
    assert 'перенос' in preview['text'].lower() or 'журнал' in preview['text'].lower()


@pytest.mark.parametrize('invalid', ['mixed_timezones', 'naive_times', 'wrong_uid', 'wrong_time_type'])
def test_orphaned_moved_invalid_projection_fails_closed(tmp_path, invalid):
    app, calendar, _, _ = setup(tmp_path)
    operation = published(app)
    uid, original = next((uid, row) for uid, row in operation.published_plan['rows'].items()
                         if row['kind'] == 'class')
    row = deepcopy(original)
    if invalid == 'mixed_timezones':
        row['data']['dtstart'] = '2026-09-24T10:00:00'
        row['data']['dtend'] = '2026-09-24T11:30:00+07:00'
    elif invalid == 'naive_times':
        row['data']['dtstart'] = '2026-09-24T10:00:00'
        row['data']['dtend'] = '2026-09-24T11:30:00'
    elif invalid == 'wrong_uid':
        row['data']['uid'] = 'another-identity'
    else:
        row['data']['dtstart'] = 123
    operation.published_plan['rows'].pop(uid)
    operation.published_plan.setdefault('manual_resolutions', {})[uid] = {'kind': 'moved', 'row': row}
    app.operations.save(operation)
    writes = list(calendar.writes)
    preview = app.handle_text('101', '/weekly_preview')
    assert not preview['buttons']
    assert 'перенос' in preview['text'].lower() or 'журнал' in preview['text'].lower()
    assert calendar.writes == writes
