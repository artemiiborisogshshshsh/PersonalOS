"""Replacement decisions remain local, fresh, and guarded at callback time."""
from copy import deepcopy

from models import PersonalEventState
from services.attendance_preferences import AttendancePreferenceStore
from tests.test_closed_beta_runtime import replacement_app


def _none_button(response):
    return next(row[0]['callback_data'] for row in response['buttons']
                if row[0]['callback_data'].endswith(':none'))


def _advance_source(content):
    from tests.test_closed_beta_runtime import ICS
    content['ics'] = (ICS.replace(b'pilot-lecture', b'future-class')
                      .replace(b'20260924', b'20260926'))


def test_fresh_no_replacement_survives_benign_reconcile_and_calendar_read_failure(tmp_path):
    app, content = replacement_app(tmp_path)
    _advance_source(content)
    review = app.handle_text('101', '/review_replacements')
    choice = _none_button(review)
    snapshot_before = app.snapshot.path.read_bytes()
    writes_before = list(app.adapter.writes)

    # A same-source planning read may reconcile locally, but must not make a
    # just-issued decision stale. A Calendar outage is unrelated to this choice.
    app._inputs(allow_source_review=True, allow_replacement_review=True)
    app.adapter.read_failure = True
    result = app.handle_callback('101', choice)

    assert 'Без замены' in result['text'], result
    assert 'незавершённая запись' not in result['text']
    rejected = next(event for event in app.snapshot.personal_events()
                    if event.id == 'personal-university:pilot-lecture')
    assert rejected.metadata['replacement_rejected_by_user'] is True
    assert app.adapter.writes == writes_before
    assert app.snapshot.path.read_bytes() != snapshot_before


def test_old_or_replayed_callback_does_not_consume_newer_form(tmp_path):
    app, content = replacement_app(tmp_path)
    _advance_source(content)
    form_a = app.handle_text('101', '/review_replacements')
    choice_a = _none_button(form_a)
    form_b = app.handle_text('101', '/review_replacements')
    choice_b = _none_button(form_b)
    token_b = app.pending[0]
    writes_before = list(app.adapter.writes)

    malformed = app.handle_callback('101', 'replacement:malformed')
    assert 'устарела' in malformed['text'] or 'не подтверждён' in malformed['text']
    assert app.pending[0] == token_b
    assert app.adapter.writes == writes_before

    stale = app.handle_callback('101', choice_a)
    assert 'устарела' in stale['text']
    assert app.pending[0] == token_b
    assert app.adapter.writes == writes_before

    # Replaying the same older callback also leaves the current form intact.
    replay = app.handle_callback('101', choice_a)
    assert 'устарела' in replay['text']
    assert app.pending[0] == token_b
    assert app.adapter.writes == writes_before

    accepted = app.handle_callback('101', choice_b)
    assert 'Выбрано «Без замены»' in accepted['text']
    assert app.adapter.writes == writes_before
    assert app.pending is None


def test_changed_attendance_choice_stales_replacement_decision(tmp_path):
    app, content = replacement_app(tmp_path)
    _advance_source(content)
    review = app.handle_text('101', '/review_replacements')
    choice = _none_button(review)
    prefs = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
    prefs.set_choice('ОС', 'lecture', False)
    prefs.save()

    result = app.handle_callback('101', choice)

    assert 'устар' in result['text'] or 'измен' in result['text']
    assert '(проверка: attendance)' in result['text']
    assert 'Без замены' not in result['text']
    assert not any(event.metadata.get('replacement_rejected_by_user')
                   for event in app.snapshot.personal_events())


def test_source_change_is_distinguished_from_failed_source_verification(tmp_path):
    app, content = replacement_app(tmp_path)
    _advance_source(content)
    response = app.handle_text('101', '/review_replacements')
    choice = _none_button(response)
    content['ics'] = content['ics'].replace('ОС (ЛК)'.encode(), 'Изменено'.encode())

    changed = app.handle_callback('101', choice)

    assert 'изменились после формы' in changed['text']
    assert '(проверка: source)' in changed['text']
    assert 'Изменено' not in changed['text']
    assert 'Выбрано «Без замены»' not in changed['text']
    assert not any(event.metadata.get('replacement_rejected_by_user')
                   for event in app.snapshot.personal_events())

    app, content = replacement_app(tmp_path / 'source-error')
    _advance_source(content)
    response = app.handle_text('101', '/review_replacements')
    choice = _none_button(response)
    content['fail'] = True

    failed = app.handle_callback('101', choice)

    assert 'Не удалось проверить актуальное расписание' in failed['text']
    assert 'устарела' not in failed['text']
    assert 'secret-source-error' not in failed['text']
    assert not any(event.metadata.get('replacement_rejected_by_user')
                   for event in app.snapshot.personal_events())


def test_candidate_metadata_order_does_not_stale_fresh_choice(tmp_path):
    app, _ = replacement_app(tmp_path)
    response = app.handle_text('101', '/review_replacements')
    choice = response['buttons'][0][0]['callback_data']
    target = next(event for event in app.snapshot.personal_events()
                  if event.id == 'personal-university:pilot-lecture')
    candidates = target.metadata['replacement_candidates']
    assert len(candidates) > 1
    target.metadata['replacement_candidates'] = list(reversed(candidates))
    state = app.snapshot._load()
    app.snapshot._save(state['source_events'], state['personal_events'])

    result = app.handle_callback('101', choice)

    assert 'Замена выбрана' in result['text'] or 'Без замены' in result['text']
    assert 'изменились после формы' not in result['text']


def test_target_state_change_stales_choice_before_applying_rejection(tmp_path):
    app, _ = replacement_app(tmp_path)
    response = app.handle_text('101', '/review_replacements')
    choice = _none_button(response)
    state = app.snapshot._load()
    target = next(event for event in state['personal_events']
                  if event.id == 'personal-university:pilot-lecture')
    target.state = PersonalEventState.CANCELLED
    app.snapshot._save(state['source_events'], state['personal_events'])

    result = app.handle_callback('101', choice)

    assert '(проверка: target)' in result['text']
    assert 'Выбрано «Без замены»' not in result['text']
    current_target = next(event for event in app.snapshot.personal_events()
                          if event.id == 'personal-university:pilot-lecture')
    assert current_target.state == PersonalEventState.CANCELLED
    assert not current_target.metadata.get('replacement_rejected_by_user')


def test_candidate_acceptance_survives_fresh_export_uid_regeneration(tmp_path):
    app, content = replacement_app(tmp_path)
    response = app.handle_text('101', '/review_replacements')
    choice = response['buttons'][0][0]['callback_data']
    old_uids = [event.uid for event in app.snapshot._load()['source_events']]
    assert set(old_uids) == {'physics', 'chemistry'}
    content['ics'] = (content['ics'].replace(b'chemistry', b'fresh-chemistry')
                      .replace(b'physics', b'fresh-physics'))
    writes_before = list(app.adapter.writes)

    result = app.handle_callback('101', choice)

    assert 'Замена выбрана' in result['text']
    saved = app.snapshot.personal_events()
    assert len(saved) == 1
    assert saved[0].id == 'personal-university:pilot-lecture'
    assert saved[0].university_event_uid == 'physics'
    assert app.adapter.writes == writes_before


def test_future_published_needs_review_candidate_can_be_accepted_locally(tmp_path):
    from services.adaptive_preparation_service import DraftOperation

    app, _ = replacement_app(tmp_path)
    event = app.snapshot.personal_events()[0]
    event.state = PersonalEventState.NEEDS_REVIEW
    state = app.snapshot._load()
    state['personal_events'][0] = event
    app.snapshot._save(state['source_events'], state['personal_events'])
    app.operations.save(DraftOperation(id='published', blocks=[], status='confirmed', published_plan={
        'rows': {event.id: {'kind': 'class', 'data': {
            'system_source_event_id': event.university_event_uid,
            'dtstart': event.start_time.isoformat(), 'dtend': event.end_time.isoformat(),
        }}},
    }))

    response = app.handle_text('101', '/review_replacements')
    assert any(row[0]['callback_data'].endswith(':0') for row in response['buttons'])
    assert any(row[0]['callback_data'].endswith(':none') for row in response['buttons'])
    writes_before = list(app.adapter.writes)
    result = app.handle_callback('101', response['buttons'][0][0]['callback_data'])

    assert 'Замена выбрана' in result['text']
    assert app.snapshot.personal_events()[0].university_event_uid == 'physics'
    assert app.adapter.writes == writes_before


def test_published_ordinary_missing_event_is_not_offered_for_local_rejection(tmp_path):
    from services.adaptive_preparation_service import DraftOperation

    app, content = replacement_app(tmp_path)
    _advance_source(content)
    app._inputs(allow_source_review=True, allow_replacement_review=True)
    event = app.snapshot.personal_events()[0]
    event.state = PersonalEventState.POSSIBLY_CANCELLED
    event.metadata['change_reason'] = 'missing_from_university_schedule'
    state = app.snapshot._load()
    state['personal_events'][0] = event
    app.snapshot._save(state['source_events'], state['personal_events'])
    app.operations.save(DraftOperation(id='published', blocks=[], status='confirmed', published_plan={
        'rows': {event.id: {'kind': 'class', 'data': {
            'system_source_event_id': event.university_event_uid,
            'dtstart': event.start_time.isoformat(), 'dtend': event.end_time.isoformat(),
        }}},
    }))

    response = app.handle_text('101', '/review_replacements')

    assert 'неопубликованных исчезнувших занятий нет' in response['text']
    assert response['buttons'] == []
    assert app.snapshot.personal_events()[0].metadata.get('replacement_rejected_by_user') is not True


def test_newly_claimed_candidate_stales_selection(tmp_path):
    app, _ = replacement_app(tmp_path)
    review = app.handle_text('101', '/review_replacements')
    choice = review['buttons'][0][0]['callback_data']
    candidate_uid = app.pending[3]['candidates'][0]
    state = app.snapshot._load()
    claimed = deepcopy(state['personal_events'][0])
    claimed.id = 'another-personal-event'
    claimed.university_event_uid = candidate_uid
    state['personal_events'].append(claimed)
    app.snapshot._save(state['source_events'], state['personal_events'])

    result = app.handle_callback('101', choice)

    assert 'Замена выбрана' not in result['text']
    assert 'claimed' in result['text']
    assert next(event for event in app.snapshot.personal_events()
                if event.id == 'personal-university:pilot-lecture').university_event_uid == 'pilot-lecture'
    assert 'another-personal-event' in {event.id for event in app.snapshot.personal_events()}


def test_pending_remote_write_blocks_callback_after_form_issue(tmp_path):
    app, content = replacement_app(tmp_path)
    _advance_source(content)
    review = app.handle_text('101', '/review_replacements')
    choice = _none_button(review)
    app.classes.projection_state.put('old', pending='insert')
    before = app.snapshot.path.read_bytes()

    result = app.handle_callback('101', choice)

    assert 'незавершённая запись' in result['text']
    assert app.snapshot.path.read_bytes() == before
    assert not any(event.metadata.get('replacement_rejected_by_user')
                   for event in app.snapshot.personal_events())
