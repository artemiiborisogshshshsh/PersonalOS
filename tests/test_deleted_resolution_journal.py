"""Legacy confirmed deletions remain durable even if their old row was retired."""
from copy import deepcopy
import pytest

from tests.test_closed_beta_runtime import confirmation
from tests.test_published_plan_deletions import setup


def published(app):
    return next(iter(app.operations.load_all().values()))


def test_orphaned_confirmed_deletion_stays_excluded_across_preview_apply_restart(tmp_path):
    app, calendar, _, factory = setup(tmp_path)
    operation = published(app)
    uid, row = next((uid, row) for uid, row in operation.published_plan['rows'].items()
                    if row['kind'] == 'class')

    calendar.remote.pop((operation.published_plan['calendar_id'], row['event_id']))
    review = app.handle_text('101', '/review_calendar')
    assert 'приняты и сохранены' in app.handle_callback('101', confirmation(review))['text']

    operation = published(app)
    journal = deepcopy(operation.published_plan['manual_resolutions'][uid])
    # The legacy archived form predates source_identity on accepted decisions.
    journal.pop('source_identity', None)
    operation.published_plan['manual_resolutions'][uid] = deepcopy(journal)
    operation.published_plan['rows'].pop(uid)
    app.operations.save(operation)
    writes = list(calendar.writes)

    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert calendar.writes == writes
    assert uid not in app.pending[3]['rows']

    result = app.handle_callback('101', confirmation(preview))
    assert 'обновлён и проверен' in result['text'].lower(), result
    assert ('insert', uid) not in calendar.writes[len(writes):]
    updated = published(app)
    assert uid not in updated.published_plan['rows']
    assert updated.published_plan['manual_resolutions'][uid] == journal
    assert (updated.published_plan['calendar_id'], row['event_id']) not in calendar.remote

    restarted = factory(tmp_path)
    writes = list(calendar.writes)
    repeated = restarted.handle_text('101', '/weekly_preview')
    assert 'повторных записей нет' in repeated['text']
    assert calendar.writes == writes
    assert published(restarted).published_plan['manual_resolutions'][uid] == journal


def test_orphaned_deleted_identity_reappearance_conflicts_before_writes(tmp_path):
    app, calendar, _, _ = setup(tmp_path)
    operation = published(app)
    uid, row = next((uid, row) for uid, row in operation.published_plan['rows'].items()
                    if row['kind'] == 'class')
    calendar.remote.pop((operation.published_plan['calendar_id'], row['event_id']))
    review = app.handle_text('101', '/review_calendar')
    app.handle_callback('101', confirmation(review))
    operation = published(app)
    operation.published_plan['rows'].pop(uid)
    app.operations.save(operation)
    writes = list(calendar.writes)

    # A replacement under the same UID is ambiguous because the orphaned
    # journal no longer carries an event ID to compare.
    replacement = deepcopy(row)
    calendar.remote[(operation.published_plan['calendar_id'], 'replacement-id')] = {
        'id': 'replacement-id', 'iCalUID': uid, 'etag': 'replacement-v1',
        'status': 'confirmed', 'extendedProperties': {'private': {}},
    }
    preview = app.handle_text('101', '/weekly_preview')
    assert not preview['buttons']
    assert 'идентичность' in preview['text'].lower() or 'удал' in preview['text'].lower()
    assert calendar.writes == writes


def test_orphaned_deleted_malformed_journal_is_actionable_conflict(tmp_path):
    app, _, _, _ = setup(tmp_path)
    operation = published(app)
    uid = next(uid for uid, row in operation.published_plan['rows'].items() if row['kind'] == 'class')
    operation.published_plan['rows'].pop(uid)
    operation.published_plan.setdefault('manual_resolutions', {})[uid] = {'kind': 'deleted'}
    app.operations.save(operation)

    preview = app.handle_text('101', '/weekly_preview')
    assert not preview['buttons']
    assert 'журнал' in preview['text'].lower() or 'неоднозначно' in preview['text'].lower()


def test_orphaned_moved_journal_without_saved_row_is_actionable_conflict(tmp_path):
    app, _, _, _ = setup(tmp_path)
    operation = published(app)
    uid = next(uid for uid, row in operation.published_plan['rows'].items() if row['kind'] == 'class')
    operation.published_plan['rows'].pop(uid)
    operation.published_plan.setdefault('manual_resolutions', {})[uid] = {'kind': 'moved'}
    app.operations.save(operation)

    preview = app.handle_text('101', '/weekly_preview')
    assert not preview['buttons']
    assert 'перенос' in preview['text'].lower() or 'журнал' in preview['text'].lower()


@pytest.mark.parametrize('invalid', ['mixed_timezones', 'naive_times', 'wrong_uid', 'wrong_time_type'])
def test_orphaned_moved_invalid_projection_fails_closed(tmp_path, invalid):
    app, calendar, _, _ = setup(tmp_path)
    operation = published(app)
    uid, original = next((uid, row) for uid, row in operation.published_plan['rows'].items()
                         if row['kind'] == 'class')
    row = deepcopy(original)
    if invalid == 'mixed_timezones':
        row['data']['dtstart'] = '2026-09-24T10:00:00'
        row['data']['dtend'] = '2026-09-24T11:30:00+07:00'
    elif invalid == 'naive_times':
        row['data']['dtstart'] = '2026-09-24T10:00:00'
        row['data']['dtend'] = '2026-09-24T11:30:00'
    elif invalid == 'wrong_uid':
        row['data']['uid'] = 'another-identity'
    else:
        row['data']['dtstart'] = 123
    operation.published_plan['rows'].pop(uid)
    operation.published_plan.setdefault('manual_resolutions', {})[uid] = {'kind': 'moved', 'row': row}
    app.operations.save(operation)
    writes = list(calendar.writes)
    preview = app.handle_text('101', '/weekly_preview')
    assert not preview['buttons']
    assert 'перенос' in preview['text'].lower() or 'журнал' in preview['text'].lower()
    assert calendar.writes == writes
