from copy import deepcopy

from tests.test_closed_beta_runtime import confirmation
from tests.test_published_plan_updates import setup


def _setup_many(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    additions = []
    for index, day in enumerate(range(25, 30), start=1):
        additions.append(
            f'BEGIN:VEVENT\r\nUID:extra-lecture-{index}\r\n'
            f'DTSTART:202609{day}T030000Z\r\nDTEND:202609{day}T043000Z\r\n'
            'SUMMARY:ОС (ЛК)\r\nEND:VEVENT\r\n'
        )
    content['ics'] = content['ics'].replace(
        b'END:VCALENDAR', ''.join(additions).encode() + b'END:VCALENDAR')
    add_preview = app.handle_text('101', '/weekly_preview')
    assert add_preview['buttons'], add_preview
    assert 'обновлён' in app.handle_callback('101', confirmation(add_preview))['text']
    return app, calendar


def _accept_deleted_preparation_and_preview(app, calendar):
    operation = next(iter(app.operations.load_all().values()))
    uid = next(uid for uid, row in operation.published_plan['rows'].items()
               if row['kind'] == 'preparation')
    row = operation.published_plan['rows'][uid]
    key = next(key for key, event in calendar.remote.items() if event['iCalUID'] == uid)
    deleted_event = deepcopy(calendar.remote.pop(key))

    review = app.handle_text('101', '/review_calendar')
    assert review['buttons'] and deleted_event['summary'] in review['text']
    accepted = app.handle_callback('101', confirmation(review))
    assert 'Ручные изменения приняты' in accepted['text']
    assert key not in calendar.remote

    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    return key, deleted_event, preview


def test_apply_revalidates_full_plan_only_before_actual_writes(tmp_path):
    app, calendar = _setup_many(tmp_path)
    deleted_key, _, preview = _accept_deleted_preparation_and_preview(app, calendar)
    assert deleted_key not in calendar.remote
    payload = app.pending[3]
    assert len(payload['rows']) > 10

    validations = 0
    original_validate = app.updates.validate

    def counted_validate(*args, **kwargs):
        nonlocal validations
        validations += 1
        return original_validate(*args, **kwargs)

    app.updates.validate = counted_validate
    writes_before = len(calendar.writes)
    result = app.handle_callback('101', confirmation(preview))
    writes = len(calendar.writes) - writes_before

    assert 'обновлён и проверен' in result['text']
    assert writes > 1
    assert validations == 1 + writes  # Initial guard, then one full refresh per write.
    assert validations < len(payload['rows'])
    assert deleted_key not in calendar.remote
    saved = next(iter(app.operations.load_all().values()))
    assert not saved.pending_plan_update and not saved.projection_pending


def test_reappeared_manual_tombstone_blocks_second_write(tmp_path):
    app, calendar = _setup_many(tmp_path)
    deleted_key, deleted_event, preview = _accept_deleted_preparation_and_preview(app, calendar)
    payload = app.pending[3]
    operation = next(iter(app.operations.load_all().values()))
    base_rows = operation.published_plan['rows']
    changed = [uid for uid, row in payload['rows'].items()
               if row['data'] != base_rows[uid]['data']]
    assert len(changed) >= 2

    validations = 0
    original_validate = app.updates.validate

    def counted_validate(*args, **kwargs):
        nonlocal validations
        validations += 1
        return original_validate(*args, **kwargs)

    app.updates.validate = counted_validate
    original_update = calendar._update_event
    reappeared = False

    def update_then_reappear_deleted(calendar_id, event_id, data, **kwargs):
        nonlocal reappeared
        result = original_update(calendar_id, event_id, data, **kwargs)
        if not reappeared:
            calendar.remote[deleted_key] = deepcopy(deleted_event)
            reappeared = True
        return result

    calendar._update_event = update_then_reappear_deleted
    writes_before = len(calendar.writes)
    result = app.handle_callback('101', confirmation(preview))

    assert reappeared
    assert 'обновлён и проверен' not in result['text']
    assert len(calendar.writes) - writes_before == 1
    assert validations == 3  # Preflight, first write, then full refresh blocks the second.
    saved = app.operations.load(operation.id)
    assert saved.pending_plan_update and saved.projection_pending
    assert deleted_key in calendar.remote
