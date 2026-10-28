"""Learning projection uses fresh complete batches at mutation boundaries only."""
from copy import deepcopy

import pytest

from services.calendar_availability_service import CalendarAvailabilityService
from services.published_plan_updates import PlanConflict, decode
from tests.test_plan_apply_validation_budget import _setup_many
from tests.test_learning_calendar import (
    setup, operation, prepare_homework, add_personal_meeting,
)
from tests.test_published_plan_updates import ProcessCrash


def instrument(calendar, monkeypatch):
    counts = {'batches': [], 'uid': 0, 'id': 0, 'availability': 0}
    original_uid = calendar.get_event_by_uid
    original_id = getattr(calendar, 'get_event_by_id', None)
    original_load = CalendarAvailabilityService.load
    def uid_read(*args, **kwargs):
        counts['uid'] += 1
        return original_uid(*args, **kwargs)
    def id_read(calendar_id, event_id, **kwargs):
        counts['id'] += 1
        if original_id:
            return original_id(calendar_id, event_id, **kwargs)
        return deepcopy(calendar.remote.get((calendar_id, event_id)))
    def batch(calendar_id, identities):
        counts['batches'].append(dict(identities))
        if calendar.read_failure:
            raise ValueError('synthetic batch failure')
        rows = {event.get('iCalUID'): event for (key, _), event in calendar.remote.items()
                if key == calendar_id and event.get('iCalUID')}
        return {uid: (deepcopy(rows.get(uid)), deepcopy(calendar.remote.get((calendar_id, event_id))))
                for uid, event_id in identities.items()}
    def load(self, *args, **kwargs):
        counts['availability'] += 1
        return original_load(self, *args, **kwargs)
    monkeypatch.setattr(calendar, 'get_event_by_uid', uid_read)
    monkeypatch.setattr(calendar, 'get_event_by_id', id_read, raising=False)
    monkeypatch.setattr(calendar, 'get_event_pairs', batch, raising=False)
    monkeypatch.setattr(CalendarAvailabilityService, 'load', load)
    return counts


def save_note(app):
    app.learning.refresh()
    lesson = next(row for row in app.learning.store.lessons()
                  if row['source_personal_id'] == 'personal-university:pilot-lecture')
    app.learning.store.add_material(lesson['id'], 'Тема: процессы', kind='note')


def test_two_material_writes_do_not_scan_each_unchanged_row(tmp_path, monkeypatch):
    app, calendar = _setup_many(tmp_path)
    save_note(app)
    baseline = deepcopy(operation(app).published_plan)
    counts = instrument(calendar, monkeypatch)
    before = len(calendar.writes)
    assert app.sync_learning().startswith('Google Calendar обновлён')
    writes = len(calendar.writes) - before
    assert writes == 2  # One class and its existing preparation; other rows are no-ops.
    assert len(baseline['rows']) > 10
    assert len(counts['batches']) == writes + 3  # Planning, target preflight, each write, final.
    assert counts['availability'] == writes + 1  # Planning and immediately before each mutation.
    assert counts['uid'] == counts['id'] == 0
    identities = {uid: row['event_id'] for uid, row in baseline['rows'].items()}
    assert all(batch == identities for batch in counts['batches'])
    assert not operation(app).pending_plan_update
    # A repeat performs only the planning preflight and no new remote writes.
    counts['batches'].clear()
    counts['availability'] = 0
    assert app.sync_learning() == ''
    assert len(calendar.writes) - before == writes
    assert len(counts['batches']) == counts['availability'] == 1
    assert counts['uid'] == counts['id'] == 0


@pytest.mark.parametrize('fault', ['id', 'ownership', 'etag', 'missing_etag', 'partial'])
def test_batched_target_preflight_preserves_strict_identity_and_etags(tmp_path, monkeypatch, fault):
    app, calendar, _, _ = setup(tmp_path)
    save_note(app)
    counts = instrument(calendar, monkeypatch)
    batch = calendar.get_event_pairs
    def damaged(calendar_id, identities):
        result = batch(calendar_id, identities)
        if len(counts['batches']) == 2:  # After planning, before any actual mutation.
            uid = next(iter(result))
            remote, by_id = result[uid]
            if fault == 'partial':
                del result[uid]
            elif fault == 'id':
                by_id['id'] = 'replacement'
            elif fault == 'ownership':
                by_id['extendedProperties'] = {}
            elif fault == 'missing_etag':
                remote.pop('etag')
                by_id.pop('etag')
            else:
                by_id['etag'] = 'different-version'
        return result
    monkeypatch.setattr(calendar, 'get_event_pairs', damaged)
    writes = list(calendar.writes)
    with pytest.raises((PlanConflict, KeyError)):
        app.sync_learning()
    assert calendar.writes == writes
    assert operation(app).pending_plan_update


def test_fresh_batch_before_next_write_detects_other_row_edit(tmp_path, monkeypatch):
    app, calendar, _, _ = setup(tmp_path)
    save_note(app)
    counts = instrument(calendar, monkeypatch)
    update = calendar._update_event
    def update_then_edit_other(calendar_id, event_id, data, **kwargs):
        result = update(calendar_id, event_id, data, **kwargs)
        other = next(row for (key, identifier), row in calendar.remote.items()
                     if key == calendar_id and identifier != event_id and row.get('iCalUID', '').startswith('personal-university:'))
        other.update(summary='Ручное изменение', etag='manual')
        return result
    monkeypatch.setattr(calendar, '_update_event', update_then_edit_other)
    before = len(calendar.writes)
    with pytest.raises(PlanConflict, match='Ручное изменение'):
        app.sync_learning()
    assert len(calendar.writes) - before == 1
    assert len(counts['batches']) == 4  # Planning, preflight, write1, rejected write2.
    assert operation(app).pending_plan_update


def test_batched_learning_retry_still_checks_personal_event_added_after_journal(tmp_path, monkeypatch):
    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app)
    counts = instrument(calendar, monkeypatch)
    app.learning.store.set_homework_duration(lesson_id, 120)
    save = app.operations.save
    def save_then_crash(current):
        save(current)
        if current.pending_plan_update.get('kind') == 'learning':
            raise ProcessCrash()
    monkeypatch.setattr(app.operations, 'save', save_then_crash)
    with pytest.raises(ProcessCrash):
        app.sync_learning()
    monkeypatch.setattr(app.operations, 'save', save)
    current = operation(app)
    target = next(decode(row['data']) for uid, row in current.pending_plan_update['rows'].items()
                  if uid not in current.published_plan['rows'] and row['kind'] == 'preparation')
    add_personal_meeting(calendar, target.dtstart, target.dtend)
    before = len(calendar.writes)
    with pytest.raises(PlanConflict, match='личным событием'):
        app.sync_learning()
    assert len(calendar.writes) == before
    assert operation(app).pending_plan_update
    assert counts['uid'] == counts['id'] == 0


def test_batched_sparse_tombstone_retry_skips_already_deleted_row(tmp_path, monkeypatch):
    app, calendar, _, factory = setup(tmp_path)
    lesson_id = prepare_homework(app)
    block = next(block for block in operation(app).blocks if block.id.startswith('learning-prep:'))
    row = operation(app).published_plan['rows'][block.id]
    counts = instrument(calendar, monkeypatch)
    delete = calendar._delete_event
    def delete_then_crash(calendar_id, event_id, **kwargs):
        delete(calendar_id, event_id, **kwargs)
        calendar.remote[(calendar_id, event_id)] = {'id': event_id, 'status': 'cancelled'}
        raise ProcessCrash()
    monkeypatch.setattr(calendar, '_delete_event', delete_then_crash)
    app.learning.store.record_preparation_result(lesson_id, True)
    with pytest.raises(ProcessCrash):
        app.sync_learning()
    monkeypatch.setattr(calendar, '_delete_event', delete)
    restarted = factory(tmp_path)
    assert restarted.sync_learning().startswith('Google Calendar обновлён')
    assert [entry for entry in calendar.writes if entry == ('delete', row['event_id'])] == [('delete', row['event_id'])]
    assert block.id not in operation(restarted).published_plan['rows']
    assert not operation(restarted).pending_plan_update
    assert counts['uid'] == 0
    assert counts['id'] <= 2  # Only deletion helper's bounded exact-ID verification.
