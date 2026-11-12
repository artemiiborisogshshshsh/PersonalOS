"""Synthetic runtime regressions for source identity drift in frozen history."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from models import PersonalEventState
from services.published_plan_updates import decode
from services.closed_beta_runtime import _ended_unpublished_review_is_safe
from services.runtime_schedule import RuntimeSchedule
from tests.test_closed_beta_runtime import (
    ICS, NOW, _publish_class_only, confirmation,
)
from tests.test_published_plan_updates import setup as publish_with_updates


def changed_past_snapshot(app, *, include_future_class=True):
    """Model a source correction already persisted for a fully elapsed class."""
    state = app.snapshot._load()
    personal = state['personal_events'][0]
    personal.university_event_uid = 'corrected-source-identity'
    personal.start_time += timedelta(days=1)
    personal.end_time += timedelta(days=1)
    state['personal_events'][0] = personal

    corrected = state['source_events'][0]
    corrected.uid = personal.university_event_uid
    corrected.dtstart += timedelta(days=1)
    corrected.dtend += timedelta(days=1)
    app.snapshot._save(state['source_events'], state['personal_events'])

    current = [corrected]
    if include_future_class:
        future = deepcopy(corrected)
        future.uid = 'future-planning-class'
        future.dtstart = app.now() + timedelta(days=2)
        future.dtend = future.dtstart + timedelta(minutes=90)
        current.append(future)
    app._source_events = lambda _source: current
    return personal.id


def test_past_identity_correction_allows_future_update_without_rewriting_history(tmp_path):
    app, _, _ = _publish_class_only(tmp_path / 'tenant')
    app.now = lambda: NOW + timedelta(days=4)
    old_id = changed_past_snapshot(app)
    operation = next(iter(app.operations.load_all().values()))
    old_row = deepcopy(operation.published_plan['rows'][old_id])
    old_remote = deepcopy(app.adapter.get_event_by_uid(
        operation.published_plan['calendar_id'], old_id, strict=True,
    ))
    writes_before = list(app.adapter.writes)

    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert app.adapter.writes == writes_before
    applied = app.handle_callback('101', confirmation(preview))
    assert 'обновлён' in applied['text'].lower(), applied

    updated = next(iter(app.operations.load_all().values()))
    assert updated.published_plan['rows'][old_id] == old_row
    assert app.adapter.get_event_by_uid(
        updated.published_plan['calendar_id'], old_id, strict=True,
    ) == old_remote
    assert any(uid != old_id and row['kind'] == 'class'
               and decode(row['data']).system_source_event_id == 'future-planning-class'
               for uid, row in updated.published_plan['rows'].items())


def test_ongoing_published_class_identity_drift_still_blocks(tmp_path):
    app, _, _ = _publish_class_only(tmp_path / 'tenant')
    app.now = lambda: NOW + timedelta(days=2, hours=1, minutes=30)
    changed_past_snapshot(app)

    operation = next(iter(app.operations.load_all().values()))
    published = next(row for row in operation.published_plan['rows'].values() if row['kind'] == 'class')
    with pytest.raises(Exception, match='Исходная идентичность') as conflict:
        app._inputs(check_calendar=False)
    assert published['data']['summary'] in str(conflict.value)
    assert 'идентификаторы в сохранённом и опубликованном расписаниях различаются' in str(conflict.value)


def test_future_source_uid_reuse_does_not_use_past_drift_exception(tmp_path):
    app, _, _ = _publish_class_only(tmp_path / 'tenant')
    app.now = lambda: NOW + timedelta(days=4)
    changed_past_snapshot(app, include_future_class=False)
    current = list(app._source_events(None))
    current[0].dtstart += timedelta(days=10)
    current[0].dtend += timedelta(days=10)
    app._source_events = lambda _source: current

    with pytest.raises(Exception, match='Исходная идентичность'):
        app._inputs(check_calendar=False)


@pytest.mark.parametrize('ambiguity', ['old-uid', 'different-time', 'unrelated-title'])
def test_update_schedule_rejects_ambiguous_move_without_changing_history(tmp_path, ambiguity):
    app, _, _ = _publish_class_only(tmp_path / 'tenant')
    app.now = lambda: NOW + timedelta(days=3)
    old_id = changed_past_snapshot(app, include_future_class=False)
    operation = next(iter(app.operations.load_all().values()))
    published = operation.published_plan['rows'][old_id]['data']
    raw = app._source_events(None)
    if ambiguity == 'old-uid':
        raw.append(replace(raw[0], uid=published['system_source_event_id'],
                           dtstart=decode(published).dtstart, dtend=decode(published).dtend))
    elif ambiguity == 'different-time':
        raw[0] = replace(raw[0], dtend=raw[0].dtend + timedelta(minutes=5))
    elif ambiguity == 'unrelated-title':
        raw[0] = replace(raw[0], summary='Другая лекция')
    app._source_events = lambda _source: raw
    snapshot_before = app.snapshot.path.read_bytes()
    writes_before = list(app.adapter.writes)

    response = app.handle_text('101', '/update_schedule')

    assert 'Исходная идентичность' in response['text']
    assert 'Перенести:' not in response['text']
    assert not response['buttons']
    assert app.pending is None
    assert app.snapshot.path.read_bytes() == snapshot_before
    assert app.operations.load(operation.id).published_plan == operation.published_plan
    assert app.adapter.writes == writes_before


@pytest.mark.parametrize('kind', ['ЛК', 'ЛБ'])
def test_verified_tpu_move_updates_the_same_calendar_event_only_after_confirmation(tmp_path, kind):
    ics = ICS.replace('(ЛК)'.encode(), f'({kind})'.encode())
    app, calendar, _, _ = publish_with_updates(tmp_path, ics=ics)
    app.now = lambda: NOW + timedelta(days=3)
    uid = changed_past_snapshot(app, include_future_class=False)
    old = next(iter(app.operations.load_all().values()))
    old_row = old.published_plan['rows'][uid]
    writes = list(calendar.writes)

    preview = app.handle_text('101', '/update_schedule')
    assert '↔ Перенести:' in preview['text'], preview
    assert f'({kind})' in preview['text']
    assert '24.09 10:00' in preview['text'] and '25.09 10:00' in preview['text']
    assert calendar.writes == writes
    assert app.operations.load(old.id).published_plan == old.published_plan

    # Background polling does not silently confirm this preview.
    settings = app.profile.load()
    app.profile.save(replace(settings, maintenance_hours=[app.now().hour]))
    preview = app.handle_text('101', '/update_schedule')
    app.background_tick(lambda *_: None, lambda *_: None)
    assert not RuntimeSchedule(app.directory / 'runtime_schedule.json', [app.now().hour]).due_hours(app.now())
    assert calendar.writes == writes
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    assert calendar.writes[len(writes):] == [('update', uid)]
    updated = app.operations.load(old.id).published_plan
    assert updated['rows'][uid]['event_id'] == old_row['event_id']
    assert decode(updated['rows'][uid]['data']).dtstart == app.now() + timedelta(hours=1)
    assert set(updated['rows']) == set(old.published_plan['rows'])
    assert all(row == updated['rows'][key] for key, row in old.published_plan['rows'].items() if key != uid)
    assert 'Изменений и повторных записей нет' in app.handle_text('101', '/update_schedule')['text']


def test_source_change_after_tpu_move_preview_prevents_calendar_write(tmp_path):
    app, calendar, _, _ = publish_with_updates(tmp_path)
    app.now = lambda: NOW + timedelta(days=3)
    changed_past_snapshot(app, include_future_class=False)
    preview = app.handle_text('101', '/update_schedule')
    assert '↔ Перенести:' in preview['text'], preview
    writes = list(calendar.writes)
    app._source_events(None)[0].dtend += timedelta(minutes=5)
    assert 'обновлён' not in app.handle_callback('101', confirmation(preview))['text']
    assert calendar.writes == writes


def test_missing_preparation_warns_without_blocking_move_or_new_class(tmp_path, monkeypatch):
    from services.adaptive_preparation_service import AdaptivePreparationService

    app, calendar, _, _ = publish_with_updates(tmp_path)
    app.now = lambda: NOW + timedelta(days=3)
    changed_past_snapshot(app)
    build = AdaptivePreparationService.build_draft

    def with_unplaced_preparation(self, events, **kwargs):
        plan = build(self, events, **kwargs)
        unrelated = next(event for event in events if event.university_event_uid == 'future-planning-class')
        plan.blocks = [block for block in plan.blocks if block.source_event_id != unrelated.id]
        plan.no_slot_reasons[unrelated.id] = 'нет свободного промежутка'
        return plan

    monkeypatch.setattr(AdaptivePreparationService, 'build_draft', with_unplaced_preparation)
    writes = list(calendar.writes)
    response = app.handle_text('101', '/update_schedule')
    assert '↔ Перенести: ОС (ЛК), 24.09 10:00–11:30 → ОС (ЛК), 25.09 10:00–11:30' in response['text']
    assert 'Не удалось запланировать подготовку:' in response['text']
    assert 'нет свободного промежутка' in response['text']
    assert '+ Добавить: ОС (ЛК)' in response['text']
    assert 'Публикация заблокирована' not in response['text']
    assert calendar.writes == writes
    assert 'обновлён' in app.handle_callback('101', confirmation(response))['text']
    updated = next(iter(app.operations.load_all().values()))
    skipped_id, = updated.no_slot_reasons
    assert updated.published_plan['rows'][skipped_id]['kind'] == 'class'
    assert not any(block.source_event_id == skipped_id for block in updated.blocks)
    assert skipped_id not in updated.completed_source_event_ids


def test_missing_replacement_slot_keeps_already_published_preparation(tmp_path, monkeypatch):
    from services.adaptive_preparation_service import AdaptivePreparationService

    app, calendar, content, _ = publish_with_updates(tmp_path)
    original = next(iter(app.operations.load_all().values()))
    block = original.blocks[0]
    original_row = original.published_plan['rows'][block.id]
    new_class = ICS.split(b'BEGIN:VEVENT')[1].split(b'END:VEVENT')[0]
    content['ics'] = ICS.replace(b'END:VCALENDAR', b'BEGIN:VEVENT' + new_class.replace(
        b'pilot-lecture', b'other-lecture').replace(b'20260924', b'20260925') + b'END:VEVENT\r\nEND:VCALENDAR')
    build = AdaptivePreparationService.build_draft

    def without_replacement(self, events, **kwargs):
        plan = build(self, events, **kwargs)
        plan.blocks = [item for item in plan.blocks if item.source_event_id != block.source_event_id]
        plan.no_slot_reasons[block.source_event_id] = 'нет свободного промежутка'
        return plan

    monkeypatch.setattr(AdaptivePreparationService, 'build_draft', without_replacement)
    before = list(calendar.writes)
    preview = app.handle_text('101', '/update_schedule')
    assert 'Не удалось запланировать подготовку:' in preview['text']
    assert calendar.writes == before
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    updated = app.operations.load(original.id)
    assert updated.published_plan['rows'][block.id] == original_row
    assert not any(uid == block.id for _, uid in calendar.writes[len(before):])


@pytest.mark.parametrize('change_uid', [False, True])
def test_future_tpu_lab_move_retains_original_attendance_choice(tmp_path, change_uid):
    ics = ICS.replace('(ЛК)'.encode(), '(ЛБ)'.encode())
    app, calendar, content, _ = publish_with_updates(tmp_path, ics=ics)
    old = next(iter(app.operations.load_all().values()))
    uid, row = next((uid, row) for uid, row in old.published_plan['rows'].items() if row['kind'] == 'class')
    content['ics'] = ics.replace(b'030000Z', b'040000Z').replace(b'043000Z', b'053000Z')
    if change_uid:
        content['ics'] = content['ics'].replace(b'pilot-lecture', b'new-tpu-uid')
    writes = list(calendar.writes)
    preview = app.handle_text('101', '/update_schedule')
    assert '↔ Перенести:' in preview['text'], preview
    assert calendar.writes == writes
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    updated = app.operations.load(old.id).published_plan
    assert updated['rows'][uid]['event_id'] == row['event_id']
    assert decode(updated['rows'][uid]['data']).dtstart == decode(row['data']).dtstart + timedelta(hours=1)
    assert 'Изменений и повторных записей нет' in app.handle_text('101', '/update_schedule')['text']


def test_manual_resolution_without_identity_evidence_is_not_reinterpreted(tmp_path):
    app, _, _ = _publish_class_only(tmp_path / 'tenant')
    app.now = lambda: NOW + timedelta(days=4)
    old_id = changed_past_snapshot(app, include_future_class=False)
    operation = next(iter(app.operations.load_all().values()))
    operation.published_plan['manual_resolutions'] = {old_id: {'kind': 'moved'}}
    app.operations.save(operation)

    with pytest.raises(Exception, match='Исходная идентичность'):
        app._inputs(check_calendar=False)


@pytest.mark.parametrize('state,candidates', [
    (PersonalEventState.CANCELLED, []),
    (PersonalEventState.NEEDS_REVIEW, [{
        'uid': 'ended-unpublished-candidate',
        'start_time': (NOW - timedelta(days=3)).isoformat(),
        'end_time': (NOW - timedelta(days=3) + timedelta(minutes=90)).isoformat(),
    }]),
])
def test_ended_unpublished_review_stays_saved_but_does_not_block_future_preview(
    tmp_path, state, candidates,
):
    from tests.test_closed_beta_runtime import _publish_class_only

    app, _, _ = _publish_class_only(tmp_path / 'tenant')
    app.now = lambda: NOW + timedelta(days=4)
    changed_past_snapshot(app)
    real_reconcile = app.snapshot.reconcile
    injected = deepcopy(app.snapshot.personal_events()[0])
    injected.id = 'unpublished-historical-review'
    injected.university_event_uid = 'unpublished-historical-source'
    injected.start_time = NOW - timedelta(days=3)
    injected.end_time = injected.start_time + timedelta(minutes=90)
    injected.state = state
    injected.metadata = {
        'change_reason': ('missing_from_university_schedule' if state == PersonalEventState.CANCELLED
                          else 'ambiguous_replacement_requires_confirmation'),
        'replacement_candidates': deepcopy(candidates),
    }

    def reconcile_with_historical_review(*args, **kwargs):
        result = real_reconcile(*args, **kwargs)
        return replace(result, personal_events=[*result.personal_events, injected])

    app.snapshot.reconcile = reconcile_with_historical_review
    before_operation = deepcopy(next(iter(app.operations.load_all().values())))
    writes_before = list(app.adapter.writes)

    preview = app.handle_text('101', '/weekly_preview')

    assert preview['buttons'], preview
    assert 'разбери' not in preview['text'].lower()
    assert app.adapter.writes == writes_before
    after_operation = next(iter(app.operations.load_all().values()))
    assert after_operation.published_plan == before_operation.published_plan
    assert injected.state == state
    assert injected.metadata['replacement_candidates'] == candidates


@pytest.mark.parametrize('relation', [
    'future_candidate_time', 'published_source_uid', 'source_resolution_uid',
    'recovery_source_uid', 'future_raw_observation',
])
def test_active_or_published_replacement_association_keeps_preview_blocked(
    tmp_path, relation,
):
    from tests.test_closed_beta_runtime import _publish_class_only

    app, _, _ = _publish_class_only(tmp_path / 'tenant')
    app.now = lambda: NOW + timedelta(days=4)
    changed_past_snapshot(app)
    operation = next(iter(app.operations.load_all().values()))
    if relation == 'source_resolution_uid':
        operation.published_plan['source_resolutions'] = ['published-resolution-source']
        app.operations.save(operation)
    recovery_source_uid = None
    if relation == 'recovery_source_uid':
        recovered_id, recovered_row = next(iter(operation.published_plan['rows'].items()))
        recovery_source_uid = decode(recovered_row['data']).system_source_event_id
        operation.published_plan['rows'] = {}
        operation.published_plan['recovery_history'] = {
            recovered_id: {'row': deepcopy(recovered_row)},
        }
        app.operations.save(operation)
    real_reconcile = app.snapshot.reconcile
    injected = deepcopy(app.snapshot.personal_events()[0])
    injected.id = 'unpublished-ambiguous-review'
    injected.university_event_uid = 'unpublished-ambiguous-source'
    injected.start_time = NOW - timedelta(days=3)
    injected.end_time = injected.start_time + timedelta(minutes=90)
    injected.state = PersonalEventState.NEEDS_REVIEW
    if relation == 'published_source_uid':
        candidate_uid = decode(next(iter(operation.published_plan['rows'].values()))['data']).system_source_event_id
    else:
        candidate_uid = {
            'future_candidate_time': 'unpublished-new-candidate',
            'source_resolution_uid': 'published-resolution-source',
            'recovery_source_uid': recovery_source_uid,
            'future_raw_observation': 'future-planning-class',
        }[relation]
    candidate_end = (app.now() + timedelta(days=1) if relation == 'future_candidate_time'
                     else NOW - timedelta(days=2))
    injected.metadata = {
        'change_reason': 'ambiguous_replacement_requires_confirmation',
        'replacement_candidates': [{
            'uid': candidate_uid,
            'start_time': (candidate_end - timedelta(minutes=90)).isoformat(),
            'end_time': candidate_end.isoformat(),
        }],
    }

    def reconcile_with_active_review(*args, **kwargs):
        result = real_reconcile(*args, **kwargs)
        return replace(result, personal_events=[*result.personal_events, injected])

    app.snapshot.reconcile = reconcile_with_active_review
    before_plan = deepcopy(operation.published_plan)
    writes_before = list(app.adapter.writes)

    preview = app.handle_text('101', '/weekly_preview')

    assert not preview['buttons'], preview
    assert 'неоднозначная замена' in preview['text'].lower()
    assert app.adapter.writes == writes_before
    assert next(iter(app.operations.load_all().values())).published_plan == before_plan
