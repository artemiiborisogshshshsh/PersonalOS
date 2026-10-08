"""Synthetic temporal boundaries: history survives and new writes stay future-only."""
from copy import deepcopy
from datetime import timedelta, timezone

import pytest
import requests

from services.published_plan_updates import PublishedPlanUpdates, decode
from tests.test_closed_beta_runtime import NOW, ICS, confirmation, onboard, application
from tests.test_published_plan_updates import setup, move, ProcessCrash
from tests.test_published_plan_deletions import setup as deletion_setup, OTHER


def operation(app):
    return next(iter(app.operations.load_all().values()))


def next_week(content):
    content['ics'] = ICS.replace(b'pilot-lecture', b'next-week').replace(b'20260924', b'20261001')


def test_week_rollover_preserves_published_history_and_adds_future_once(tmp_path):
    app, calendar, content, factory = setup(tmp_path)
    before = deepcopy(operation(app).published_plan['rows'])
    remote_before = deepcopy(calendar.remote)
    next_week(content)
    app.now = lambda: NOW + timedelta(days=7)
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert 'завершённые — 2, текущие — 0' in preview['text']
    assert '+ Добавить' in preview['text']
    assert calendar.remote == remote_before
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    assert all(operation(app).published_plan['rows'][uid] == row for uid, row in before.items())
    assert all(calendar.remote[key] == row for key, row in remote_before.items())
    assert len(calendar.remote) == 4
    writes = list(calendar.writes)
    assert not app.handle_text('101', '/weekly_preview')['buttons']
    assert calendar.writes == writes
    restarted = factory(tmp_path, '101', calendar)
    restarted.now = app.now
    assert not restarted.handle_text('101', '/weekly_preview')['buttons']
    assert all(operation(restarted).published_plan['rows'][uid] == row for uid, row in before.items())


@pytest.mark.parametrize('delta', [timedelta(), timedelta(minutes=5), timedelta(hours=3)])
def test_started_class_and_linked_preparation_stay_frozen_when_source_metadata_changes(tmp_path, delta):
    app, calendar, content, _ = deletion_setup(tmp_path)
    base = deepcopy(operation(app).published_plan['rows'])
    row = next(row for row in base.values() if row['kind'] == 'class')
    app.now = lambda: decode(row['data']).dtstart + delta
    # Keep a future class in the verified source even after the first ends.
    content['ics'] = content['ics'].replace(b'SUMMARY:', b'SUMMARY:Updated ', 1)
    before = deepcopy(calendar.remote)
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    assert calendar.remote == before
    assert operation(app).published_plan['rows'] == base


@pytest.mark.parametrize('delta', [timedelta(), timedelta(minutes=5), timedelta(hours=1)])
def test_started_preparation_for_future_class_is_retained_without_regeneration(tmp_path, delta):
    app, calendar, content, _ = setup(tmp_path)
    base = deepcopy(operation(app).published_plan['rows'])
    uid, prep = next((uid, row) for uid, row in base.items() if row['kind'] == 'preparation')
    app.now = lambda: decode(prep['data']).dtstart + delta
    move(content)
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    current = operation(app)
    assert current.published_plan['rows'][uid] == prep
    assert len(current.blocks) == 1
    assert current.blocks[0].id == uid
    assert calendar.writes.count(('insert', uid)) == 1
    assert ('update', uid) not in calendar.writes


def test_phase_uses_instants_across_timezones(tmp_path):
    app, _, _, _ = setup(tmp_path)
    row = next(row for row in operation(app).published_plan['rows'].values() if row['kind'] == 'class')
    event = decode(row['data'])
    assert PublishedPlanUpdates.phase(row, event.dtstart.astimezone(timezone.utc)) == 'ongoing'
    assert PublishedPlanUpdates.phase(row, event.dtend.astimezone(timezone.utc)) == 'past'
    assert PublishedPlanUpdates.phase(row, event.dtstart - timedelta(microseconds=1)) == 'future'


def test_unchanged_source_horizon_rollover_changes_signature(tmp_path):
    app, _, content, _ = setup(tmp_path)
    next_event = ICS.split(b'BEGIN:VEVENT')[1].split(b'END:VEVENT')[0].replace(b'pilot-lecture', b'next-week').replace(b'20260924', b'20261001')
    content['ics'] = ICS.replace(b'END:VCALENDAR', b'BEGIN:VEVENT' + next_event + b'END:VEVENT\r\nEND:VCALENDAR')
    first = app._inputs()[3]
    app.now = lambda: NOW + timedelta(days=7)
    second = app._inputs()[3]
    assert first != second


def test_pending_target_and_source_decisions_survive_horizon_rollover(tmp_path):
    app, calendar, content, factory = setup(tmp_path)
    saved = operation(app)
    saved.published_plan['source_resolutions'] = ['historical-reviewed-source']
    app.operations.save(saved)
    move(content)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    calendar.crash_after_write = True
    with pytest.raises(ProcessCrash):
        app.handle_callback('101', token)
    pending = deepcopy(operation(app))
    next_week(content)
    restarted = factory(tmp_path, '101', calendar)
    restarted.now = lambda: NOW + timedelta(days=7)
    writes = list(calendar.writes)
    response = restarted.handle_text('101', '/weekly_preview')
    assert not response['buttons'] and 'оператор' in response['text']
    assert operation(restarted).pending_plan_update == pending.pending_plan_update
    assert operation(restarted).published_plan == pending.published_plan
    assert calendar.writes == writes


def test_initial_publication_stops_preparation_after_class_write_crosses_start(tmp_path):
    app = application(tmp_path)
    # Use same existing projector path with update-enabled temporal guards.
    app.updates = PublishedPlanUpdates(app)
    onboard(app)
    preview = app.handle_text('101', '/weekly_preview')
    prep = app.pending[2].blocks[0]
    clock = {'now': NOW}
    app.now = lambda: clock['now']
    original = app.adapter._insert_event
    def advance(data, calendar_id, **kwargs):
        result = original(data, calendar_id, **kwargs)
        clock['now'] = prep.start
        return result
    app.adapter._insert_event = advance
    result = app.handle_callback('101', confirmation(preview))
    assert 'не завершена' in result['text']
    assert len([row for row in app.adapter.writes if row[0] == 'insert']) == 1
    assert operation(app).projection_pending


def test_update_retry_rechecks_clock_before_second_mutation(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    move(content)
    preview = app.handle_text('101', '/weekly_preview')
    clock = {'now': NOW}
    app.now = lambda: clock['now']
    calls = []
    def transient(calendar_id, event_id, data, **kwargs):
        calls.append(data.uid)
        clock['now'] = data.dtstart
        raise requests.ConnectionError('synthetic transient error')
    calendar._update_event = transient
    result = app.handle_callback('101', confirmation(preview))
    assert 'не завершено' in result['text']
    assert len(calls) == 1
    assert operation(app).pending_plan_update


def test_delete_retry_and_midbatch_recheck_current_clock(tmp_path):
    app, calendar, content, _ = deletion_setup(tmp_path)
    content['ics'] = OTHER
    preview = app.handle_text('101', '/review_missing')
    targets = deepcopy(app.pending[3]['rows'])
    prep = next(row for row in targets.values() if row['kind'] == 'preparation')
    original = calendar._delete_event
    def advance(calendar_id, event_id, **kwargs):
        result = original(calendar_id, event_id, **kwargs)
        app.now = lambda: decode(prep['data']).dtstart
        return result
    calendar._delete_event = advance
    result = app.handle_callback('101', confirmation(preview))
    assert 'не завершено' in result['text'], result
    assert len([row for row in calendar.writes if row[0] == 'delete']) == 1
    assert operation(app).pending_plan_update


def test_historical_source_uid_cannot_be_reused_with_future_time(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    before = deepcopy(operation(app).published_plan)
    snapshot = app.snapshot.path.read_bytes()
    writes = list(calendar.writes)
    app.now = lambda: NOW + timedelta(days=7)
    content['ics'] = ICS.replace(b'20260924', b'20261001')
    preview = app.handle_text('101', '/weekly_preview')
    assert not preview['buttons'] and 'повторно использует ID' in preview['text']
    assert operation(app).published_plan == before
    assert app.snapshot.path.read_bytes() == snapshot
    assert calendar.writes == writes


@pytest.mark.parametrize('remap_uid', [False, True])
def test_unconfirmed_source_change_cannot_redefine_published_history(tmp_path, remap_uid):
    app, calendar, content, _ = setup(tmp_path)
    base = deepcopy(operation(app).published_plan)
    if remap_uid:
        next_week(content)
        later = NOW + timedelta(days=7)
    else:
        move(content)
        row = next(row for row in base['rows'].values() if row['kind'] == 'class')
        later = decode(row['data']).dtstart + timedelta(minutes=5)
    app.handle_text('101', '/weekly_preview')  # Source reconciled, never approved.
    snapshot = app.snapshot.path.read_bytes()
    writes = list(calendar.writes)
    app.now = lambda: later
    result = app.handle_text('101', '/weekly_preview')
    assert not result['buttons'] and 'идентичность' in result['text'], result
    assert app.snapshot.path.read_bytes() == snapshot
    assert operation(app).published_plan == base
    assert calendar.writes == writes


@pytest.mark.parametrize('kind', ['moved', 'deleted'])
def test_manual_class_decision_survives_restart_and_week_rollover(tmp_path, kind):
    from tests.test_manual_calendar_resolution import row_for, change_time
    app, calendar, content, factory = deletion_setup(tmp_path)
    uid, row = row_for(app, 'class')
    if kind == 'moved':
        change_time(calendar, row, '2026-09-24T12:00:00+07:00', '2026-09-24T13:30:00+07:00')
    else:
        calendar.remote[('Personal University Schedule', row['event_id'])]['status'] = 'cancelled'
    accepted = app.handle_callback('101', confirmation(app.handle_text('101', '/review_calendar')))
    assert 'приняты и сохранены' in accepted['text'], accepted
    decision = deepcopy(operation(app).published_plan['manual_resolutions'][uid])
    assert decision['source_identity']['uid'] == row['data']['system_source_event_id']
    next_week(content)
    restarted = factory(tmp_path)
    restarted.now = lambda: NOW + timedelta(days=7)
    before = deepcopy(calendar.remote)
    preview = restarted.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    result = restarted.handle_callback('101', confirmation(preview))
    assert 'обновлён' in result['text'], result
    assert operation(restarted).published_plan['manual_resolutions'][uid] == decision
    assert all(calendar.remote[key] == value for key, value in before.items())
    if kind == 'deleted':
        assert calendar.remote[('Personal University Schedule', row['event_id'])]['status'] == 'cancelled'


def test_planning_buffer_does_not_extend_sunday_preview_horizon(tmp_path):
    app, _, content, _ = setup(tmp_path)
    def source_block(uid, date):
        return (b'BEGIN:VEVENT' + ICS.split(b'BEGIN:VEVENT')[1].split(b'END:VEVENT')[0]
                .replace(b'pilot-lecture', uid).replace(b'20260924', date) + b'END:VEVENT\r\n')
    content['ics'] = ICS.replace(b'END:VCALENDAR', source_block(b'next-week', b'20261001')
                                + source_block(b'week-two', b'20261008')
                                + source_block(b'boundary', b'20261012')
                                + source_block(b'beyond-horizon', b'20261015') + b'END:VCALENDAR')
    app.now = lambda: NOW.replace(day=27, hour=23, minute=50)
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    rows = app.pending[3]['rows']
    assert 'personal-university:next-week' in rows
    assert 'personal-university:week-two' in rows
    assert 'personal-university:boundary' not in rows
    assert 'personal-university:beyond-horizon' not in rows
    preparation_sources = {
        row['data']['system_source_event_id']
        for row in rows.values() if row['kind'] == 'preparation'
    }
    assert 'personal-university:week-two' in preparation_sources
    assert 'personal-university:boundary' not in preparation_sources
    assert 'personal-university:beyond-horizon' not in preparation_sources
