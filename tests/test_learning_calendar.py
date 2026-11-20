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


def test_confirmed_remainder_keeps_other_rows_and_recovers_after_remote_insert(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from services.learning_lifecycle import REMAINDER_PREFIX
    from tests.test_telegram_learning_flow import deliver_feedback_prompt, feedback_button

    app, calendar, _, factory = setup(tmp_path)
    lesson_id = prepare_homework(app, deadline=NOW.replace(day=26, hour=20))
    block = next(row for row in operation(app).blocks if row.id.startswith('learning-prep:'))
    prompt = deliver_feedback_prompt(app, block)
    prior = deepcopy(operation(app).published_plan['rows'])
    partial = feedback_button(prompt, 'partial')
    form = app.handle_callback('101', partial)
    app.learning.bind_message(6001, lesson_id, form['_learning_kind'])
    reply = {'message_id': 6002, 'reply_to_message': {'message_id': 6001}}
    original_writes = list(calendar.writes)
    assert 'от 1 до 5' in app.handle_text('101', '20 6; 37; номер 21', message=reply)['text']
    assert 'ответь на сообщение' in app.handle_text('101', '20 3; 37; номер 21')['text']
    receipt = app.handle_text('101', '20 3; 37; номер 21', message=reply)
    assert 'Осталось 37 мин' in receipt['text'] and '26.09 20:00' in receipt['text']
    assert not app.learning.store.has_pending_calendar_sync()
    assert app.sync_learning() == ''
    app.handle_text('101', '/update_schedule')
    assert calendar.writes == original_writes
    assert operation(app).published_plan['rows'] == prior

    insert = calendar._insert_event

    def crash(*args, **kwargs):
        insert(*args, **kwargs)
        raise ProcessCrash

    monkeypatch.setattr(calendar, '_insert_event', crash)
    approve = feedback_button(receipt, 'remainder_accept')
    with pytest.raises(ProcessCrash):
        app.handle_callback('101', approve)
    assert operation(app).pending_plan_update['feedback_token'] == partial.split(':')[2]
    expected_writes = list(calendar.writes)
    assert len(expected_writes) == len(original_writes) + 1
    monkeypatch.setattr(calendar, '_insert_event', insert)
    restarted = factory(tmp_path)
    restarted.now = app.now
    restarted.background_tick(Mock(return_value={'message_id': 6003}), Mock())
    result = operation(restarted)
    continuation = [row for row in result.blocks if row.id.startswith(REMAINDER_PREFIX)]
    assert len(continuation) == 1 and continuation[0].minutes == 37
    assert continuation[0].start - block.end >= timedelta(minutes=restarted.profile.load().profile.preparation_break_minutes)
    assert continuation[0].end <= NOW.replace(day=26, hour=20)
    assert {uid: result.published_plan['rows'][uid] for uid in prior} == prior
    assert not restarted.learning.store.pending_feedback_remainders()
    assert 'уже использована' in restarted.handle_callback('101', approve)['text']
    restarted.sync_learning()
    restarted.handle_text('101', '/update_schedule')
    assert calendar.writes == expected_writes


def test_confirmed_remainder_explains_missing_contiguous_window_without_writes(tmp_path):
    from tests.test_telegram_learning_flow import deliver_feedback_prompt, feedback_button

    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app, deadline=NOW.replace(day=26, hour=20))
    block = next(row for row in operation(app).blocks if row.id.startswith('learning-prep:'))
    prompt = deliver_feedback_prompt(app, block)
    form = app.handle_callback('101', feedback_button(prompt, 'skipped'))
    app.learning.bind_message(7001, lesson_id, form['_learning_kind'])
    result = app.handle_text('101', '0 3; 55; номер 21', message={'reply_to_message': {'message_id': 7001}})
    add_personal_meeting(calendar, app.now() + timedelta(minutes=4), NOW.replace(day=26, hour=20))
    writes = list(calendar.writes)
    answer = app.handle_callback('101', feedback_button(result, 'remainder_accept'))
    assert 'Нужно 55 мин подряд' in answer['text'] and '26.09 20:00' in answer['text']
    assert calendar.writes == writes and not operation(app).pending_plan_update
    assert not app.learning.store.pending_feedback_remainders()


def test_accepted_duration_applies_to_next_new_class_without_retiming_published_preparation(tmp_path):
    from tests.test_published_plan_deletions import BOTH, OTHER
    from tests.test_telegram_learning_flow import deliver_feedback_prompt, feedback_button

    app, calendar, content, _ = setup(tmp_path)
    first, existing = sorted(operation(app).blocks, key=lambda block: block.start)
    initial_row = deepcopy(operation(app).published_plan['rows'][existing.id])
    prompt = deliver_feedback_prompt(app, first)
    form = app.handle_callback('101', feedback_button(prompt, 'done'))
    app.learning.bind_message(7101, form['_learning_lesson_id'], form['_learning_kind'])
    writes = list(calendar.writes)
    result = app.handle_text('101', '90 4', message={'reply_to_message': {'message_id': 7101}})
    action = feedback_button(result, 'estimate_accept')
    assert '75 мин' in result['text']
    assert '75 мин' in app.handle_callback('101', action)['text']
    assert 'уже использована' in app.handle_callback('101', action)['text']
    assert calendar.writes == writes
    added = b'BEGIN:VEVENT' + OTHER.split(b'BEGIN:VEVENT')[1].split(b'END:VCALENDAR')[0]
    added = added.replace(b'other-class', b'future-course').replace(b'20260925', b'20260926')
    content['ics'] = BOTH.replace(b'END:VCALENDAR', added + b'END:VCALENDAR')
    preview = app.handle_text('101', '/update_schedule')
    assert calendar.writes == writes
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    current = operation(app)
    new = next(block for block in current.blocks if block.source_event_id.endswith(':future-course'))
    assert new.minutes == 75
    assert current.published_plan['rows'][existing.id] == initial_row


def test_passive_deadline_change_cannot_reopen_unconfirmed_homework(tmp_path, monkeypatch):
    from dataclasses import replace
    from tests.test_telegram_learning_flow import deliver_feedback_prompt, feedback_button

    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app)
    first = next(row for row in operation(app).blocks if row.id.startswith('learning-prep:'))
    prompt = deliver_feedback_prompt(app, first)
    form = app.handle_callback('101', feedback_button(prompt, 'partial'))
    app.learning.bind_message(7201, lesson_id, form['_learning_kind'])
    receipt = app.handle_text('101', '20 3; 40; номер 21', message={'reply_to_message': {'message_id': 7201}})
    before = list(calendar.writes)
    previous = app.learning.store.get_lesson(lesson_id)['due_at']
    events = app.snapshot.personal_events()
    next_lesson = next(event for event in events if event.id.endswith(':other-class'))
    shifted = replace(next_lesson, start_time=next_lesson.start_time + timedelta(hours=1),
                      end_time=next_lesson.end_time + timedelta(hours=1))
    monkeypatch.setattr(app.snapshot, 'personal_events', lambda: [event for event in events if event.id != shifted.id] + [shifted])
    app.learning.refresh()
    assert app.learning.store.get_lesson(lesson_id)['due_at'] != previous
    assert app.sync_learning() == ''
    assert calendar.writes == before
    assert 'устарела' in app.handle_callback('101', feedback_button(receipt, 'remainder_accept'))['text']
    assert calendar.writes == before


def test_verified_unchanged_homework_receipt_and_deadline_description(tmp_path):
    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app)
    writes = list(calendar.writes)
    message = app.sync_learning()
    assert message == ''
    assert calendar.writes == writes
    receipt, _ = app.learning._homework_schedule_receipt(lesson_id, message)
    assert 'Интервалы из подтверждённого плана' in receipt
    assert 'пока не подтверждена' not in receipt

    lesson = app.learning.store.get_lesson(lesson_id)
    uid = next(block.id for block in operation(app).blocks
               if block.id.startswith('learning-prep:')
               and block.source_event_id == lesson['source_personal_id'])
    before = deepcopy(remote_for(app, calendar, uid))
    new_deadline = datetime.fromisoformat(lesson['due_at']) + timedelta(days=1)
    app.learning.store.set_deadline(lesson_id, new_deadline)
    assert 'обновлён' in app.sync_learning()
    after = remote_for(app, calendar, uid)
    assert after['start'] == before['start'] and after['end'] == before['end']
    assert f'Срок сдачи ДЗ: {new_deadline:%d.%m.%Y %H:%M}.' in after['description']


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
