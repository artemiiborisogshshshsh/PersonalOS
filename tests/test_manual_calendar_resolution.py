"""Manual Calendar decisions use synthetic providers and require a separate replan."""
from copy import deepcopy
from datetime import timedelta

import pytest

from tests.test_closed_beta_runtime import confirmation
from tests.test_published_plan_deletions import OTHER, setup


def published(app):
    return next(iter(app.operations.load_all().values()))


def row_for(app, kind):
    return next((uid, row) for uid, row in published(app).published_plan['rows'].items()
                if row['kind'] == kind)


def change_time(calendar, row, start, end):
    remote = calendar.remote[("Personal University Schedule", row['event_id'])]
    remote['start']['dateTime'], remote['end']['dateTime'] = start, end
    remote['etag'] = 'manual-move'


@pytest.mark.parametrize('kind', ['class', 'preparation'])
def test_move_accepts_without_write_then_pins_across_replan(tmp_path, kind):
    app, calendar, content, factory = setup(tmp_path)
    uid, row = row_for(app, kind)
    old = deepcopy(calendar.remote[("Personal University Schedule", row['event_id'])])
    if kind == 'class':
        start, end = '2026-09-24T12:00:00+07:00', '2026-09-24T13:30:00+07:00'
    else:
        start, end = '2026-09-22T12:00:00+07:00', '2026-09-22T12:20:00+07:00'
    change_time(calendar, row, start, end)
    writes = list(calendar.writes)
    review = app.handle_text('101', '/review_calendar')
    assert 'Перенесена' in review['text'] and 'Принятие сохранит' in review['text'], review
    assert calendar.writes == writes
    accepted = app.handle_callback('101', confirmation(review))
    assert 'приняты и сохранены' in accepted['text'], accepted
    assert calendar.writes == writes
    decision = published(app).published_plan['manual_resolutions'][uid]
    assert decision['kind'] == 'moved'
    assert published(app).published_plan['rows'][uid]['etag'] == 'manual-move'
    assert calendar.remote[("Personal University Schedule", row['event_id'])] != old
    restarted = factory(tmp_path)
    preview = restarted.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert 'обновлён' in restarted.handle_callback('101', confirmation(preview))['text']
    assert calendar.remote[("Personal University Schedule", row['event_id'])]['etag'] == 'manual-move'
    assert ('update', uid) not in calendar.writes[len(writes):]
    writes = list(calendar.writes)
    assert 'повторных записей нет' in restarted.handle_text('101', '/weekly_preview')['text']
    assert calendar.writes == writes


@pytest.mark.parametrize('kind', ['class', 'preparation'])
def test_deletion_accepts_without_restore_or_new_preparation(tmp_path, kind):
    app, calendar, _, _ = setup(tmp_path)
    uid, row = row_for(app, kind)
    calendar.remote.pop(("Personal University Schedule", row['event_id']))
    writes = list(calendar.writes)
    review = app.handle_text('101', '/review_calendar')
    assert 'Удалена' in review['text'], review
    if kind == 'class':
        assert 'существующие связанные подготовки останутся' in review['text']
    assert 'приняты и сохранены' in app.handle_callback('101', confirmation(review))['text']
    assert calendar.writes == writes
    decision = published(app).published_plan['manual_resolutions'][uid]
    assert decision['kind'] == 'deleted'
    assert decision['observed'] == {'by_uid': None, 'by_id': None}
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    assert ("Personal University Schedule", row['event_id']) not in calendar.remote
    assert ('insert', uid) not in calendar.writes[len(writes):]
    assert uid in published(app).published_plan['rows']


def test_deleted_preparation_is_retired_when_its_source_class_is_removed(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    class_uid = 'personal-university:pilot-lecture'
    prep_uid, prep_row = next((uid, row) for uid, row in published(app).published_plan['rows'].items()
                              if row['kind'] == 'preparation'
                              and row['data']['system_source_event_id'] == class_uid)
    calendar.remote.pop(('Personal University Schedule', prep_row['event_id']))
    assert 'приняты и сохранены' in app.handle_callback(
        '101', confirmation(app.handle_text('101', '/review_calendar')))['text']
    preview = app.handle_text('101', '/weekly_preview')
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']

    content['ics'] = OTHER
    review = app.handle_text('101', '/review_missing')
    assert review['buttons'], review
    assert 'Удаление проверено' in app.handle_callback('101', confirmation(review))['text']
    operation = published(app)
    assert prep_uid not in operation.published_plan['rows']
    assert prep_uid not in operation.published_plan['manual_resolutions']
    writes = list(calendar.writes)
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    assert ('insert', prep_uid) not in calendar.writes[len(writes):]


@pytest.mark.parametrize('change', ['etag', 'tombstone_disappears'])
def test_tombstone_observation_is_bound_to_confirmation(tmp_path, change):
    app, calendar, _, _ = setup(tmp_path)
    uid, row = row_for(app, 'preparation')
    remote = calendar.remote[('Personal University Schedule', row['event_id'])]
    remote['status'] = 'cancelled'
    remote['etag'] = 'tombstone-v1'
    review = app.handle_text('101', '/review_calendar')
    assert review['buttons'], review
    writes = list(calendar.writes)
    if change == 'etag':
        remote['etag'] = 'tombstone-v2'
    else:
        calendar.remote.pop(('Personal University Schedule', row['event_id']))
    response = app.handle_callback('101', confirmation(review))
    assert 'приняты и сохранены' not in response['text']
    assert not published(app).published_plan.get('manual_resolutions')
    assert calendar.writes == writes


@pytest.mark.parametrize('change', ['source', 'other_remote', 'expiry', 'etag'])
def test_review_confirmation_rechecks_entire_source_and_calendar(tmp_path, change):
    app, calendar, content, _ = setup(tmp_path)
    uid, row = row_for(app, 'class')
    change_time(calendar, row, '2026-09-24T12:00:00+07:00', '2026-09-24T13:30:00+07:00')
    token = confirmation(app.handle_text('101', '/review_calendar'))
    writes = list(calendar.writes)
    if change == 'source':
        content['ics'] = content['ics'].replace(b'pilot-lecture', b'pilot-other')
    elif change == 'other_remote':
        other_uid, other_row = next((key, value) for key, value in published(app).published_plan['rows'].items()
                                    if key != uid and value['kind'] == 'class')
        calendar.remote[("Personal University Schedule", other_row['event_id'])]['etag'] = 'changed'
    elif change == 'expiry':
        from tests.test_closed_beta_runtime import NOW
        app.now = lambda: NOW + timedelta(minutes=11)
    else:
        calendar.remote[("Personal University Schedule", row['event_id'])]['etag'] = 'changed-again'
    result = app.handle_callback('101', token)
    assert 'приняты и сохранены' not in result['text']
    assert not published(app).published_plan.get('manual_resolutions')
    assert calendar.writes == writes
