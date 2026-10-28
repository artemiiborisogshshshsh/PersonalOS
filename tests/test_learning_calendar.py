"""Published-calendar learning changes with synthetic providers and durable journals."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from services.draft_operation_store import DraftOperationStore
from services.published_plan_updates import PublishedPlanUpdates, PlanConflict, decode
from tests.test_closed_beta_runtime import confirmation, NOW
from tests.test_published_plan_deletions import setup as base_setup
from tests.test_published_plan_updates import ProcessCrash


def setup(tmp_path):
    app, calendar, content, factory = base_setup(tmp_path)
    original = calendar._insert_event
    def insert(event, calendar_id, **kwargs):
        # The legacy fake uses len(remote) as its ID, which can reuse a live
        # identity after a deletion. Google creates distinct event IDs.
        before = deepcopy(calendar.remote)
        identifier = original(event, calendar_id, **kwargs)
        stored = calendar.remote.pop((calendar_id, identifier))
        if (calendar_id, identifier) in before:
            calendar.remote[(calendar_id, identifier)] = before[(calendar_id, identifier)]
        unique = 'new-' + uuid4().hex
        stored['id'] = unique
        calendar.remote[(calendar_id, unique)] = stored
        return unique
    calendar._insert_event = insert
    def by_uid(calendar_id, uid, **kwargs):
        if calendar.read_failure:
            raise ValueError('synthetic read failure')
        return next((deepcopy(row) for (key, _), row in calendar.remote.items()
                     if key == calendar_id and row.get('iCalUID') == uid), None)
    calendar.get_event_by_uid = by_uid
    return app, calendar, content, factory


def operation(app):
    return next(iter(app.operations.load_all().values()))


def prepare_homework(app, *, minutes=60, deadline=None):
    app.learning.refresh()
    lesson = next(row for row in app.learning.store.lessons()
                  if row['source_personal_id'] == 'personal-university:pilot-lecture')
    app.learning.store.add_material(lesson['id'], 'Номера 15–20', due_at=deadline, file_id='private-photo')
    app.learning.store.set_homework_duration(lesson['id'], minutes)
    app.sync_learning()
    return lesson['id']


def remote_for(app, calendar, uid):
    row = operation(app).published_plan['rows'][uid]
    return calendar.remote[(operation(app).published_plan['calendar_id'], row['event_id'])]


def move_manually(app, calendar, uid, start, end):
    remote = remote_for(app, calendar, uid)
    remote.update(start={'dateTime': start.isoformat()}, end={'dateTime': end.isoformat()}, etag='manual-move')
    review = app.handle_text('101', '/review_calendar')
    assert review['buttons'], review
    accepted = app.handle_callback('101', confirmation(review))
    assert 'приняты и сохранены' in accepted['text'], accepted


def assert_no_prep_conflicts(app):
    base = operation(app).published_plan
    deleted = {uid for uid, decision in base.get('manual_resolutions', {}).items() if decision['kind'] == 'deleted'}
    PublishedPlanUpdates.check_preparation_conflicts(base['rows'], app.now(), deleted)


def test_source_move_replans_own_homework_and_preserves_materials_and_ids(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    deadline = NOW.replace(day=26, hour=20)
    lesson_id = prepare_homework(app, minutes=120, deadline=deadline)
    before = [block for block in operation(app).blocks if block.id.startswith('learning-prep:')]
    assert before
    # Move the next class into the existing homework preparation interval.
    moving_start = before[0].start + timedelta(minutes=30)
    moving_end = moving_start + timedelta(minutes=90)
    content['ics'] = content['ics'].replace(b'20260925T030000Z', moving_start.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ').encode()).replace(
        b'20260925T043000Z', moving_end.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ').encode())
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    applied = app.handle_callback('101', confirmation(preview))
    assert 'обновлён и проверен' in applied['text'], applied
    after = [block for block in operation(app).blocks if block.id.startswith('learning-prep:')]
    assert {block.id for block in before} == {block.id for block in after}
    assert [(block.start, block.end) for block in before] != [(block.start, block.end) for block in after]
    assert_no_prep_conflicts(app)
    assert app.learning.store.get_lesson(lesson_id)['materials'][0]['file_id'] == 'private-photo'


def test_material_sync_respects_accepted_manual_class_time_and_updates_its_notes(tmp_path):
    app, calendar, _, _ = setup(tmp_path)
    source_id = 'personal-university:pilot-lecture'
    start, end = NOW.replace(day=24, hour=14), NOW.replace(day=24, hour=15, minute=30)
    move_manually(app, calendar, source_id, start, end)
    prepare_homework(app)
    assert_no_prep_conflicts(app)
    remote = remote_for(app, calendar, source_id)
    assert remote['start']['dateTime'] == start.isoformat()
    assert remote['end']['dateTime'] == end.isoformat()
    assert 'Номера 15–20' in remote['description']
    protected = operation(app).published_plan['manual_resolutions'][source_id]['row']
    assert protected['data']['dtstart'] == start.isoformat()
    assert 'Номера 15–20' in protected['data']['description']
    writes = list(calendar.writes)
    app.sync_learning()
    assert calendar.writes == writes


def test_sparse_tombstone_after_attempted_delete_recovers_without_second_delete(tmp_path):
    app, calendar, _, factory = setup(tmp_path)
    lesson_id = prepare_homework(app)
    owned = next(block for block in operation(app).blocks if block.id.startswith('learning-prep:'))
    old_row = deepcopy(operation(app).published_plan['rows'][owned.id])
    original_delete = calendar._delete_event
    def delete_then_crash(calendar_id, event_id, **kwargs):
        result = original_delete(calendar_id, event_id, **kwargs)
        calendar.remote[(calendar_id, event_id)] = {'id': event_id, 'status': 'cancelled'}
        raise ProcessCrash()
    calendar._delete_event = delete_then_crash
    app.learning.store.record_preparation_result(lesson_id, True)
    with pytest.raises(ProcessCrash):
        app.sync_learning()
    pending = operation(app).pending_plan_update
    assert pending['kind'] == 'learning' and owned.id in pending['attempted']
    deletes = [item for item in calendar.writes if item == ('delete', old_row['event_id'])]
    assert len(deletes) == 1
    calendar._delete_event = original_delete
    restarted = factory(tmp_path)
    assert 'Google Calendar обновлён' in restarted.sync_learning()
    assert not operation(restarted).pending_plan_update
    assert owned.id not in operation(restarted).published_plan['rows']
    assert [item for item in calendar.writes if item == ('delete', old_row['event_id'])] == deletes


def test_sparse_tombstone_never_adopts_replacement_identity(tmp_path):
    app, calendar, _, _ = setup(tmp_path)
    base = operation(app).published_plan
    uid, row = next(iter(base['rows'].items()))
    with pytest.raises(PlanConflict, match='другого события'):
        app.updates._remote(base['calendar_id'], uid, row,
                            pair=({'id': 'replacement', 'status': 'cancelled'}, None))


def test_future_homework_is_not_frozen_by_completed_origin_class(tmp_path):
    app, _, _, _ = setup(tmp_path)
    prepare_homework(app)
    current = operation(app)
    now = NOW.replace(day=24, hour=12)
    frozen = app.updates.frozen_rows(current.published_plan, now)
    assert 'personal-university:pilot-lecture' in frozen
    assert all(block.id not in frozen for block in current.blocks if block.id.startswith('learning-prep:'))


def test_manual_deleted_preparation_is_not_regenerated_by_material_sync(tmp_path):
    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app)
    block = next(block for block in operation(app).blocks if block.id.startswith('learning-prep:'))
    row = operation(app).published_plan['rows'][block.id]
    calendar.remote.pop((operation(app).published_plan['calendar_id'], row['event_id']))
    review = app.handle_text('101', '/review_calendar')
    assert review['buttons'], review
    assert 'приняты и сохранены' in app.handle_callback('101', confirmation(review))['text']
    writes = list(calendar.writes)
    app.learning.store.set_homework_duration(lesson_id, 120)
    app.sync_learning()
    assert all(item.get('iCalUID') != block.id for item in calendar.remote.values())
    assert not any(item[0] == 'insert' for item in calendar.writes[len(writes):])


def add_personal_meeting(calendar, start, end):
    calendar.calendars['Личное'] = 'Личное'
    calendar.remote[('Личное', 'meeting')] = {
        'id': 'meeting', 'iCalUID': 'personal-meeting', 'etag': 'personal-v1',
        'summary': 'Личная встреча', 'description': '',
        'start': {'dateTime': start.isoformat()}, 'end': {'dateTime': end.isoformat()},
    }


def test_learning_journal_retry_rechecks_new_personal_meeting_before_writes(tmp_path):
    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app)
    app.learning.store.set_homework_duration(lesson_id, 120)
    save = app.operations.save
    def persist_then_crash(current):
        save(current)
        if current.pending_plan_update.get('kind') == 'learning':
            raise ProcessCrash()
    app.operations.save = persist_then_crash
    with pytest.raises(ProcessCrash):
        app.sync_learning()
    app.operations.save = save
    payload = operation(app).pending_plan_update
    target = next(decode(row['data']) for uid, row in payload['rows'].items()
                  if uid not in operation(app).published_plan['rows'] and row['kind'] == 'preparation')
    add_personal_meeting(calendar, target.dtstart, target.dtend)
    writes = list(calendar.writes)
    with pytest.raises(PlanConflict, match='личным событием'):
        app.sync_learning()
    assert calendar.writes == writes
    assert operation(app).pending_plan_update == payload


def test_previously_stable_homework_moves_around_new_personal_meeting(tmp_path):
    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app)
    first = next(block for block in operation(app).blocks if block.id.startswith('learning-prep:'))
    add_personal_meeting(calendar, first.start, first.end)
    app.learning.store.add_material(lesson_id, 'Тема: процессы', kind='note')
    app.sync_learning()
    after = [block for block in operation(app).blocks if block.id.startswith('learning-prep:')]
    assert after and all(not (block.start < first.end and block.end > first.start) for block in after)
    assert calendar.remote[('Личное', 'meeting')]['etag'] == 'personal-v1'
    assert_no_prep_conflicts(app)


def test_explicit_future_source_cancellation_removes_only_owned_future_rows_once(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    app.learning.refresh()
    lesson = next(row for row in app.learning.store.lessons() if row['source_uid'] == 'pilot-lecture')
    app.learning.store.add_material(lesson['id'], 'Сохранённый конспект', kind='note', file_id='private')
    add_personal_meeting(calendar, NOW.replace(hour=18), NOW.replace(hour=19))
    personal = deepcopy(calendar.remote[('Личное', 'meeting')])
    content['ics'] = content['ics'].replace(b'UID:pilot-lecture', b'UID:pilot-lecture\r\nSTATUS:CANCELLED')
    result = app.updates.sync_source_cancellations()
    assert result.startswith('Источник подтвердил отмену:')
    assert 'personal-university:pilot-lecture' not in operation(app).published_plan['rows']
    assert 'personal-university:other-class' in operation(app).published_plan['rows']
    assert calendar.remote[('Личное', 'meeting')] == personal
    assert app.learning.store.get_lesson(lesson['id'])['materials'][0]['file_id'] == 'private'
    assert not app.learning.store.get_lesson(lesson['id'])['active']
    writes = list(calendar.writes)
    assert app.updates.sync_source_cancellations() == ''
    assert calendar.writes == writes


def test_explicit_cancellation_preserves_started_prep_and_ambiguous_loss_requires_review(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    first_prep = next(block for block in operation(app).blocks if block.source_event_id == 'personal-university:pilot-lecture')
    app.now = lambda: NOW.replace(day=24, hour=8)
    content['ics'] = content['ics'].replace(b'UID:pilot-lecture', b'UID:pilot-lecture\r\nSTATUS:CANCELLED')
    assert app.updates.sync_source_cancellations()
    assert first_prep.id in operation(app).published_plan['rows']
    assert first_prep.id in {block.id for block in operation(app).blocks}
    # Absence of the remaining source event is ambiguous, never automatic deletion.
    content['ics'] = content['ics'].split(b'BEGIN:VEVENT')[0] + b'END:VCALENDAR\r\n'
    writes = list(calendar.writes)
    try:
        result = app.updates.sync_source_cancellations()
    except (ValueError, PlanConflict):
        result = ''  # An empty feed may also fail source validation before reconciliation.
    assert result == '' and calendar.writes == writes


def test_second_pass_moves_unstarted_generic_prep_to_fit_homework(tmp_path):
    app, calendar, _, _ = setup(tmp_path)
    current = operation(app)
    generic = next(block for block in current.blocks if block.source_event_id == 'personal-university:other-class')
    origin = next(event for event in app.snapshot.personal_events() if event.id == 'personal-university:pilot-lecture')
    profile = app.profile.load().profile
    start = origin.end_time + timedelta(minutes=profile.travel_minutes_each_way + profile.recovery_minutes_after_university)
    from dataclasses import replace
    moved = replace(generic, start=start, end=start + timedelta(minutes=generic.minutes))
    current.blocks = [moved if block.id == generic.id else block for block in current.blocks]
    row = current.published_plan['rows'][generic.id]
    data = app.updates.rows(current, app.snapshot.personal_events())[generic.id]['data']
    calendar._update_event(current.published_plan['calendar_id'], row['event_id'], decode(data),
                           strict=True, expected_etag=row['etag'])
    row.update(data=data, etag=calendar.remote[(current.published_plan['calendar_id'], row['event_id'])]['etag'])
    app.operations.save(current)
    prepare_homework(app, minutes=generic.minutes, deadline=moved.end)
    after = operation(app)
    homework = next(block for block in after.blocks if block.id.startswith('learning-prep:'))
    generic_after = next(block for block in after.blocks if block.id == generic.id)
    assert homework.start == moved.start and homework.end == moved.end
    assert (generic_after.start, generic_after.end) != (moved.start, moved.end)
    assert_no_prep_conflicts(app)


def test_same_remaining_time_replans_cached_continuation_that_violates_pause(tmp_path):
    from dataclasses import replace
    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app, minutes=10)
    finished = next(block for block in operation(app).blocks if block.id.startswith('learning-prep:'))
    app.now = lambda: finished.end
    app.learning.store.record_preparation_result(lesson_id, False, 'Осталось решить номер 21', 10)
    app.sync_learning()
    current = operation(app)
    continuation = next(block for block in current.blocks if block.id.startswith('learning-prep:')
                        and block.start > app.now())
    # Represent an already published continuation produced before this fix.
    # It is still app-owned, not an accepted manual Calendar resolution.
    incorrect = replace(continuation, start=finished.end + timedelta(minutes=5),
                        end=finished.end + timedelta(minutes=15))
    current.blocks = [incorrect if block.id == continuation.id else block for block in current.blocks]
    row = current.published_plan['rows'][continuation.id]
    data = app.updates.rows(current, app.snapshot.personal_events())[continuation.id]['data']
    calendar._update_event(current.published_plan['calendar_id'], row['event_id'], decode(data),
                           strict=True, expected_etag=row['etag'])
    row.update(data=data, etag=calendar.remote[(current.published_plan['calendar_id'], row['event_id'])]['etag'])
    app.operations.save(current)
    app.learning.store.record_preparation_result(lesson_id, False, 'Осталось решить номер 21', 10)
    app.sync_learning()
    future = [block for block in operation(app).blocks if block.id.startswith('learning-prep:')
              and block.start > app.now()]
    assert len(future) == 1 and future[0].minutes == 10
    assert future[0].start - finished.end >= timedelta(minutes=app.profile.load().profile.preparation_break_minutes)
    assert any(block.id == finished.id and block.start == finished.start and block.end == finished.end
               for block in operation(app).blocks)
