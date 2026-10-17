"""Handler-level acceptance path for the closed beta's source review and publication."""
from copy import deepcopy

from models import EventType
from services.attendance_preferences import AttendancePreferenceStore
from services.published_plan_updates import PublishedPlanUpdates
from tests.test_closed_beta_runtime import NOW, Calendar, application, onboard, confirmation


def _ics(rows):
    events = []
    for uid, start, end, title in rows:
        events.append(
            'BEGIN:VEVENT\r\n'
            f'UID:{uid}\r\n'
            f'DTSTART:20260924T{start}Z\r\n'
            f'DTEND:20260924T{end}Z\r\n'
            f'SUMMARY:{title}\r\n'
            'END:VEVENT\r\n'
        )
    return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n' + ''.join(events) +
            'END:VCALENDAR\r\n').encode()


def test_closed_beta_source_review_to_publication_and_manual_change(tmp_path):
    original = _ics([
        ('old-os', '030000', '040000', 'ОС'),
        ('old-physics', '040000', '050000', 'Физика (ЛК)'),
        ('old-chemistry', '050000', '060000', 'Химия (ЛК)'),
        ('stable-algebra', '060000', '070000', 'Алгебра (ЛК)'),
    ])
    replacements = _ics([
        ('os-a', '030000', '040000', 'ОС (ПР)'),
        ('os-b', '030000', '040000', 'ОС (СЕМИНАР)'),
        ('physics-a', '040000', '050000', 'Физика (ПР)'),
        ('physics-b', '040000', '050000', 'Физика (СЕМИНАР)'),
        ('chemistry-a', '050000', '060000', 'Химия (ПР)'),
        ('chemistry-b', '050000', '060000', 'Химия (СЕМИНАР)'),
        ('stable-algebra', '060000', '070000', 'Алгебра (ЛК)'),
    ])
    content = {'ics': original}
    calendar = Calendar()
    app = application(tmp_path / 'tenant', calendar, content)
    app.updates = PublishedPlanUpdates(app)
    onboard(app)
    source_state = app.source.state_store.load(app.account.id)
    active_source = source_state.sources[source_state.active_source_id]
    assert next(event for event in app._source_events(active_source)
                if event.uid == 'old-os').event_type is EventType.UNKNOWN

    # Save a verified old snapshot, then let the source regenerate conflicting UIDs.
    app.handle_text('101', '/weekly_preview')
    content['ics'] = replacements
    writes_before_review = list(calendar.writes)
    conflict = app.handle_text('101', '/weekly_preview')
    assert 'Неоднозначная замена' in conflict['text']
    assert not conflict['buttons']
    assert calendar.writes == writes_before_review

    # ОС is an unknown-type whole subject. Excluding it removes its ambiguity
    # from review and keeps both old and candidate ОС occurrences out of the plan.
    excluded = app.handle_text('101', '/exclude_subject ОС')
    assert 'не посещаю предмет' in excluded['text']
    # The selected replacement is practical, so its attendance choice must be
    # explicit before the review token is issued and revalidated.
    preferences_path = app.directory / 'attendance_preferences.json'
    preferences = AttendancePreferenceStore.load(preferences_path)
    preferences.set_choice('Физика', 'practical', True)
    preferences.save()
    remaining_review = app.handle_text('101', '/review_replacements')
    assert 'Физика (ЛК)' in remaining_review['text']
    assert 'ОС (ЛК)' not in remaining_review['text']

    # Resolve the remaining source conflicts in order: select Physics, then
    # explicitly reject the vanished Chemistry occurrence without a replacement.
    review = app.handle_text('101', '/review_replacements')
    assert 'Физика (ЛК)' in review['text']
    physics_choice = review['buttons'][0][0]['callback_data']
    selected = app.handle_callback('101', physics_choice)
    assert 'Замена выбрана' in selected['text']
    next_review = app.handle_callback('101', 'replacement:next')
    assert 'Химия (ЛК)' in next_review['text']
    assert any(row[0]['callback_data'].endswith(':none') for row in next_review['buttons'])
    no_replacement = next(row[0]['callback_data'] for row in next_review['buttons']
                          if row[0]['callback_data'].endswith(':none'))
    no_replacement_reply = app.handle_callback('101', no_replacement)
    assert 'Без замены' in no_replacement_reply['text']
    assert 'Неоднозначных замен нет' in app.handle_text('101', '/review_replacements')['text']

    preview = app.handle_text('101', '/weekly_preview')
    assert 'Алгебра (ЛК)' in preview['text']
    assert 'ОС (ЛК)' not in preview['text']
    assert 'Calendar пока не изменён' in preview['text']
    assert calendar.writes == writes_before_review
    published = app.handle_callback('101', confirmation(preview))
    assert 'опубликован' in published['text']

    operation = next(iter(app.operations.load_all().values()))
    class_ids = {uid for uid, row in operation.published_plan['rows'].items()
                 if row['kind'] == 'class'}
    assert class_ids == {'personal-university:old-physics',
                         'personal-university:stable-algebra'}
    personal = {event.university_event_uid: event for event in app.snapshot.personal_events()}
    assert personal['physics-a'].id == 'personal-university:old-physics'
    assert personal['old-chemistry'].metadata['replacement_rejected_by_user'] is True
    published_ids = set(operation.published_plan['rows'])
    published_uids = [event['iCalUID'] for event in calendar.remote.values()]
    assert set(published_uids) == published_ids
    write_count = len(calendar.writes)

    repeated = app.handle_text('101', '/weekly_preview')
    assert 'уже опубликован' in repeated['text']
    assert len(calendar.writes) == write_count

    # A human edit is surfaced for review and left untouched by repeat preview.
    manual_event = next(event for event in calendar.remote.values()
                        if event['iCalUID'] == 'personal-university:stable-algebra')
    manual_event['summary'] = 'Ручное изменение пользователя'
    before_manual_review = deepcopy(calendar.remote)
    review_result = app.handle_text('101', '/weekly_preview')
    assert 'открой /review_calendar' in review_result['text']
    calendar_review = app.handle_text('101', '/review_calendar')
    assert 'нужен оператор' in calendar_review['text']
    assert calendar.remote == before_manual_review
    assert len(calendar.writes) == write_count
