"""Learning updates preserve verified deletions after their saved row retires."""
from copy import deepcopy

import pytest

from services.published_plan_updates import PlanConflict
from tests.test_closed_beta_runtime import NOW, confirmation
from tests.test_learning_calendar import setup, operation
from tests.test_learning_sync_budget import instrument
from tests.test_published_plan_updates import ProcessCrash


def retired_preparation(tmp_path, *, tombstone):
    app, calendar, _, factory = setup(tmp_path)
    saved = operation(app)
    uid, row = next((uid, deepcopy(row)) for uid, row in saved.published_plan['rows'].items()
                    if row['kind'] == 'preparation')
    key = (saved.published_plan['calendar_id'], row['event_id'])
    if tombstone:
        calendar.remote[key].update(status='cancelled', etag='deleted-version')
    else:
        calendar.remote.pop(key)
    review = app.handle_text('101', '/review_calendar')
    assert review['buttons'], review
    assert 'приняты и сохранены' in app.handle_callback('101', confirmation(review))['text']
    saved = operation(app)
    saved.published_plan['rows'].pop(uid)
    saved.blocks = [block for block in saved.blocks if block.id != uid]
    saved.calendar_event_ids.pop(uid, None)
    app.operations.save(saved)
    app.learning.refresh()
    lesson = next(row for row in app.learning.store.lessons()
                  if row['source_personal_id'] == 'personal-university:other-class')
    app.learning.store.add_material(lesson['id'], 'Synthetic assignment', due_at=NOW.replace(day=26))
    return app, calendar, factory, uid, row


@pytest.mark.parametrize('tombstone', [False, True])
@pytest.mark.parametrize('batched', [False, True])
def test_learning_after_retired_preparation_preserves_deletion_and_is_idempotent(
        tmp_path, monkeypatch, tombstone, batched):
    app, calendar, factory, uid, _ = retired_preparation(tmp_path, tombstone=tombstone)
    journal = deepcopy(operation(app).published_plan['manual_resolutions'][uid])
    if batched:
        counts = instrument(calendar, monkeypatch)
    before = len(calendar.writes)
    assert app.sync_learning().startswith('Google Calendar обновлён')
    assert uid not in operation(app).published_plan['rows']
    assert operation(app).published_plan['manual_resolutions'][uid] == journal
    assert ('insert', uid) not in calendar.writes[before:]
    assert any(block.id.startswith('learning-prep:') for block in operation(app).blocks)
    if batched:
        assert all(uid in identities for identities in counts['batches'])
    restarted = factory(tmp_path)
    writes = list(calendar.writes)
    assert restarted.sync_learning() == ''
    assert calendar.writes == writes
    assert operation(restarted).published_plan['manual_resolutions'][uid] == journal


@pytest.mark.parametrize('fault', ['live', 'etag', 'missing', 'one_route', 'malformed'])
def test_learning_rejects_changed_or_unsafe_orphan_observation_before_writes(tmp_path, fault):
    app, calendar, _, uid, row = retired_preparation(tmp_path, tombstone=True)
    saved = operation(app)
    key = (saved.published_plan['calendar_id'], row['event_id'])
    if fault == 'live':
        calendar.remote[key]['status'] = 'confirmed'
    elif fault == 'etag':
        calendar.remote[key]['etag'] = 'unreviewed-deletion'
    elif fault == 'missing':
        calendar.remote.pop(key)
    elif fault == 'one_route':
        calendar.remote[key]['iCalUID'] = 'other-uid'
    else:
        saved.published_plan['manual_resolutions'][uid].pop('observed')
        app.operations.save(saved)
    writes = list(calendar.writes)
    with pytest.raises(PlanConflict):
        app.sync_learning()
    assert calendar.writes == writes


@pytest.mark.parametrize('boundary', ['target_preflight', 'before_write', 'resume'])
def test_learning_rechecks_retired_deletion_after_planning_and_on_resume(tmp_path, monkeypatch, boundary):
    app, calendar, _, uid, row = retired_preparation(tmp_path, tombstone=True)
    counts = instrument(calendar, monkeypatch)
    batch = calendar.get_event_pairs
    target_batch = 2 if boundary == 'target_preflight' else 3
    if boundary == 'resume':
        save = app.operations.save
        def crash_after_journal(current):
            save(current)
            if current.pending_plan_update.get('kind') == 'learning':
                raise ProcessCrash()
        monkeypatch.setattr(app.operations, 'save', crash_after_journal)
        with pytest.raises(ProcessCrash):
            app.sync_learning()
        monkeypatch.setattr(app.operations, 'save', save)
        key = (operation(app).published_plan['calendar_id'], row['event_id'])
        calendar.remote[key]['status'] = 'confirmed'
    else:
        def changed(calendar_id, identities):
            result = batch(calendar_id, identities)
            if len(counts['batches']) == target_batch:
                assert uid in result
                result[uid][1]['etag'] = 'changed-at-boundary'
            return result
        monkeypatch.setattr(calendar, 'get_event_pairs', changed)
    writes = list(calendar.writes)
    with pytest.raises(PlanConflict, match='удалённая идентичность'):
        app.sync_learning()
    assert calendar.writes == writes


def test_learning_pending_target_cannot_recreate_orphan_deleted_uid(tmp_path, monkeypatch):
    app, calendar, _, uid, row = retired_preparation(tmp_path, tombstone=False)
    save = app.operations.save
    def crash_after_journal(current):
        save(current)
        if current.pending_plan_update.get('kind') == 'learning':
            raise ProcessCrash()
    monkeypatch.setattr(app.operations, 'save', crash_after_journal)
    with pytest.raises(ProcessCrash):
        app.sync_learning()
    monkeypatch.setattr(app.operations, 'save', save)
    saved = operation(app)
    saved.pending_plan_update['rows'][uid] = row
    app.operations.save(saved)
    writes = list(calendar.writes)
    with pytest.raises(PlanConflict, match='Удалённая идентичность снова появилась в плане'):
        app.sync_learning()
    assert calendar.writes == writes
