"""Clock-only expiration compatibility retains the legacy source hash proof."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from services.attendance_preferences import AttendancePreferenceStore, SubjectAttendancePreference
from services.published_plan_updates import PlanConflict, PublishedPlanUpdates, decode
from tests.test_closed_beta_runtime import ICS, NOW, confirmation
from tests.test_published_plan_updates import ProcessCrash, setup, move


AFTER = NOW.replace(hour=9, minute=10)


def expiring_update(tmp_path):
    app, calendar, content, factory = setup(tmp_path)
    prefs = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
    prefs.subjects['Unselected'] = SubjectAttendancePreference(lectures=False)
    prefs.save()
    early = (b'BEGIN:VEVENT\r\nUID:unselected\r\nDTSTART:20260922T010000Z\r\n'
             b'DTEND:20260922T020500Z\r\nSUMMARY:Unselected (\xd0\x9b\xd0\x9a)\r\nEND:VEVENT\r\n')
    move(content)
    content['ics'] = content['ics'].replace(b'END:VCALENDAR', early + b'END:VCALENDAR')
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert any(event.university_event_uid == 'unselected' for event in app._inputs()[2])
    return app, calendar, content, factory, preview


def operation(app):
    return next(iter(app.operations.load_all().values()))


def legacy_hash(app, settings, now, events):
    """Independent copy of the prechange schemas, including all hash inputs."""
    state = app.source.state_store.load(app.account.id)
    source = state.sources[state.active_source_id]
    first = sha256(json.dumps({
        'source': source.location, 'profile': asdict(settings),
        'events': [(e.id, e.title, e.start_time.isoformat(), e.end_time.isoformat(),
                    ('missing' if e.metadata.get('change_reason') == 'missing_from_university_schedule'
                     else e.state.value), e.description, e.location) for e in sorted(events, key=lambda e: e.id)],
    }, sort_keys=True, default=str).encode()).hexdigest()
    prefs = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
    return sha256(json.dumps({
        'signature': first, 'attendance': prefs.as_rule_metadata(),
        'horizon_end': settings.profile.planning_horizon_end(now).isoformat(),
        'raw': sorted([app.snapshot._source_to_dict(event) for event in app.snapshot._load()['source_events']],
                      key=lambda row: row['uid']),
    }, sort_keys=True, default=str).encode()).hexdigest()


def test_same_day_expiration_resumes_immutable_target_and_next_preview_is_noop(tmp_path):
    app, calendar, _, factory, preview = expiring_update(tmp_path)
    pending = deepcopy(app.pending[3])
    settings, now, events, before, _ = app._inputs()
    assert before == pending['signature'] == legacy_hash(app, settings, now, events)
    calendar.crash_after_write = True
    with pytest.raises(ProcessCrash):
        app.handle_callback('101', confirmation(preview))
    restarted = factory(tmp_path, '101', calendar)
    restarted.now = lambda: AFTER
    settings, now, events, after, _ = restarted._inputs()
    assert now == AFTER and before != after
    assert after == legacy_hash(restarted, settings, now, events)
    assert not any(event.university_event_uid == 'unselected' for event in events)
    assert restarted.signature_matches(before, after)
    continuation = restarted.handle_text('101', '/weekly_preview')
    assert continuation['buttons'], continuation
    assert restarted.pending[3]['rows'] == pending['rows']
    assert restarted.pending[3]['signature'] == before
    assert 'обновлён и проверен' in restarted.handle_callback('101', confirmation(continuation))['text']
    final = operation(restarted)
    assert final.content_hash == before and not final.pending_plan_update
    assert {uid: row['data'] for uid, row in final.published_plan['rows'].items()} == {
        uid: row['data'] for uid, row in pending['rows'].items()}
    assert calendar.writes.count(('update', 'personal-university:pilot-lecture')) == 1
    writes = list(calendar.writes)
    assert 'Изменений и повторных записей нет' in restarted.handle_text('101', '/weekly_preview')['text']
    assert calendar.writes == writes


@pytest.mark.parametrize('change', ['raw_room', 'raw_title', 'raw_unselected', 'source_location',
                                   'profile', 'travel', 'attendance', 'horizon', 'homework'])
def test_expiration_compatibility_never_hides_other_hashed_changes(tmp_path, change):
    app, calendar, content, _, _ = expiring_update(tmp_path)
    before = app.pending[3]['signature']
    app.now = lambda: AFTER
    if change == 'raw_room':
        content['ics'] = content['ics'].replace(b'END:VEVENT', b'LOCATION:Changed room\r\nEND:VEVENT', 1)
    elif change == 'raw_title':
        content['ics'] = content['ics'].replace(b'SUMMARY:', b'SUMMARY:Changed ', 1)
    elif change == 'raw_unselected':
        content['ics'] = content['ics'].replace(b'SUMMARY:Unselected', b'LOCATION:Changed\r\nSUMMARY:Unselected')
    elif change == 'source_location':
        state = app.source.state_store.load(app.account.id)
        source = state.sources[state.active_source_id]
        state.sources[state.active_source_id] = replace(source, location=source.location + '?changed-source')
        app.source.state_store.save(state)
    elif change in {'profile', 'travel', 'horizon'}:
        settings = app.profile.load()
        field = {'profile': 'lecture_minutes', 'travel': 'travel_minutes_each_way',
                 'horizon': 'planning_horizon_weeks'}[change]
        settings.profile = replace(settings.profile, **{field: getattr(settings.profile, field) + 1})
        app.profile.save(settings)
    elif change == 'attendance':
        prefs = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
        prefs.subjects['Unselected'].lectures = True
        prefs.save()
    elif change == 'homework':
        lesson = next(row for row in app.learning.store.lessons() if row['source_uid'] == 'pilot-lecture')
        app.learning.store.add_material(lesson['id'], 'New homework in event description')
    writes = list(calendar.writes)
    try:
        current = app._inputs()[3]
    except (PlanConflict, ValueError):
        assert app._input_signature_context is None
    else:
        assert not app.signature_matches(before, current)
    assert calendar.writes == writes


def test_compatibility_context_is_fresh_and_cleared_on_failed_inputs(tmp_path):
    app, calendar, content, _, _ = expiring_update(tmp_path)
    before = app.pending[3]['signature']
    app.now = lambda: AFTER
    current = app._inputs()[3]
    assert app.signature_matches(before, current)
    assert not app.signature_matches(before, 'unrelated-current-hash')
    content['fail'] = True
    with pytest.raises(ValueError):
        app._inputs()
    assert not app.signature_matches(before, current)
    # Synthetic callers without the runtime proof helper retain strict equality.
    updates = PublishedPlanUpdates(SimpleNamespace())
    assert updates._signature_matches(before, before)
    assert not updates._signature_matches(before, current)


def test_previous_day_expiration_is_not_compatible(tmp_path):
    app, _, _, _, _ = expiring_update(tmp_path)
    before = app.pending[3]['signature']
    app.now = lambda: AFTER + timedelta(days=1)
    current = app._inputs()[3]
    assert before != current
    assert not app.signature_matches(before, current)


def test_compatible_signature_does_not_authorize_started_target(tmp_path):
    app, calendar, _, _, _ = expiring_update(tmp_path)
    payload = app.pending[3]
    before = payload['signature']
    started = decode(next(row for row in payload['rows'].values() if row['kind'] == 'class')['data']).dtstart
    app.now = lambda: AFTER
    _, _, _, current, busy = app._inputs()
    assert app.signature_matches(before, current)
    # The guard receives the real action time independently of the hash cutoff.
    writes = list(calendar.writes)
    with pytest.raises(PlanConflict, match='Время изменяемого события уже наступило'):
        app.updates.guard(operation(app), payload, started, busy)
    assert calendar.writes == writes
