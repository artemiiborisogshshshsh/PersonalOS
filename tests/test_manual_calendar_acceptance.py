"""Synthetic acceptance journeys for reviewing manual edits to a published calendar.

All provider state lives in DeleteCalendar; no credentials or network are used.
The tests exercise the public pilot commands and confirmation callbacks.
"""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from services.closed_beta_runtime import PilotSource
from services.invited_beta_runtime import InvitedBot
from services.user_state_backup import UserStateBackupService
from tests.test_closed_beta_runtime import NOW, confirmation
from tests.test_invited_beta import envelope, onboard as onboard_transport
from tests.test_published_plan_deletions import BOTH, DeleteCalendar, setup


def published(app):
    return next(iter(app.operations.load_all().values())).published_plan


def remote_row(app, calendar, *, kind, source='pilot-lecture'):
    rows = published(app)['rows']
    uid = next(uid for uid, row in rows.items()
               if row['kind'] == kind and source in row['data']['system_source_event_id'])
    return uid, next(key for key, event in calendar.remote.items() if event['iCalUID'] == uid)


def manual_move(calendar, key, hours):
    event = calendar.remote[key]
    before = (event['start']['dateTime'], event['end']['dateTime'])
    event['start']['dateTime'] = (datetime.fromisoformat(before[0]) + timedelta(hours=hours)).isoformat()
    event['end']['dateTime'] = (datetime.fromisoformat(before[1]) + timedelta(hours=hours)).isoformat()
    event['etag'] = 'user-moved'
    return before, (event['start']['dateTime'], event['end']['dateTime'])


def assert_review(app, before, after):
    review = app.handle_text('101', '/review_calendar')
    assert review['buttons'], review
    assert confirmation(review).startswith('pilot:apply:')
    assert any(button['callback_data'] == 'pilot:cancel' for row in review['buttons'] for button in row)
    for value in (before, after):
        assert datetime.fromisoformat(value).strftime('%H:%M') in review['text'], review
    return review


@pytest.mark.parametrize('kind', ['class', 'preparation'])
def test_manual_move_is_accepted_locally_then_requires_separate_replan(tmp_path, kind):
    app, calendar, _, _ = setup(tmp_path)
    uid, key = remote_row(app, calendar, kind=kind)
    before, after = manual_move(calendar, key, 3)
    remote_before = deepcopy(calendar.remote)
    writes = list(calendar.writes)

    first = assert_review(app, before[0], after[0])
    app.handle_callback('101', 'pilot:cancel')
    assert 'устарело' in app.handle_callback('101', confirmation(first))['text']
    token = confirmation(assert_review(app, before[0], after[0]))
    accepted = app.handle_callback('101', token)
    assert not accepted['buttons'] and calendar.writes == writes
    assert calendar.remote == remote_before
    assert 'устарело' in app.handle_callback('101', token)['text']

    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert calendar.writes == writes
    assert confirmation(preview) != token
    app.handle_callback('101', confirmation(preview))
    current = next(event for event in calendar.remote.values() if event['iCalUID'] == uid)
    assert (current['start']['dateTime'], current['end']['dateTime']) == after
    writes = list(calendar.writes)
    app.handle_text('101', '/weekly_preview')
    assert calendar.writes == writes


@pytest.mark.parametrize('kind', ['class', 'preparation'])
def test_manual_deletion_is_never_restored_and_class_keeps_existing_prep(tmp_path, kind):
    app, calendar, content, _ = setup(tmp_path)
    uid, key = remote_row(app, calendar, kind=kind)
    linked_prep_uid, linked_prep_key = remote_row(app, calendar, kind='preparation')
    linked_prep = deepcopy(calendar.remote[linked_prep_key])
    deleted = calendar.remote.pop(key)
    writes = list(calendar.writes)
    review = app.handle_text('101', '/review_calendar')
    assert review['buttons'] and deleted['summary'] in review['text'], review
    assert datetime.fromisoformat(deleted['start']['dateTime']).strftime('%H:%M') in review['text']
    if kind == 'class':
        assert linked_prep_uid in str(review) or 'подготов' in review['text'].lower()
    token = confirmation(review)
    accepted = app.handle_callback('101', token)
    assert not accepted['buttons'] and calendar.writes == writes
    assert key not in calendar.remote

    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert calendar.writes == writes
    app.handle_callback('101', confirmation(preview))
    assert all(event['iCalUID'] != uid for event in calendar.remote.values())
    if kind == 'class':
        assert calendar.remote[linked_prep_key] == linked_prep
    else:
        source = deleted['extendedProperties']['private']['personal_os_source_event_id']
        assert not any(event.get('extendedProperties', {}).get('private', {}).get(
            'personal_os_source_event_id') == source and event['iCalUID'].startswith('prep:')
            for event in calendar.remote.values())

    # Change the other class in TPU after the deletion was accepted and replanned.
    # The deleted UID must stay suppressed through a subsequent plan version.
    content['ics'] = content['ics'].replace(b'20260925T030000Z', b'20260925T040000Z').replace(
        b'20260925T043000Z', b'20260925T053000Z')
    later = app.handle_text('101', '/weekly_preview')
    assert later['buttons'], later
    app.handle_callback('101', confirmation(later))
    assert all(event['iCalUID'] != uid for event in calendar.remote.values())
    if kind == 'class':
        assert calendar.remote[linked_prep_key] == linked_prep


@pytest.mark.parametrize('change', [
    'owner', 'identity', 'title', 'description', 'location', 'etag_missing',
    'other_row', 'other_etag_missing', 'source_changed', 'source_error', 'calendar_error',
])
def test_confirmation_rechecks_entire_baseline_and_failures_leave_decision_unaccepted(tmp_path, change):
    app, calendar, content, _ = setup(tmp_path)
    _, key = remote_row(app, calendar, kind='class')
    manual_move(calendar, key, 3)
    token = confirmation(app.handle_text('101', '/review_calendar'))
    baseline = deepcopy(published(app))
    writes = list(calendar.writes)
    target = calendar.remote[key]
    if change == 'owner':
        target['extendedProperties']['private']['personal_os_operation_id'] = 'foreign'
    elif change == 'identity':
        replacement = calendar.remote.pop(key)
        replacement['id'] = 'replacement-id'
        calendar.remote[(key[0], 'replacement-id')] = replacement
    elif change == 'title':
        target['summary'] = 'User retitled this'
    elif change == 'description':
        target['description'] = 'User description'
    elif change == 'location':
        target['location'] = 'User location'
    elif change == 'etag_missing':
        target.pop('etag')
    elif change == 'other_row':
        _, other_key = remote_row(app, calendar, kind='class', source='other-class')
        calendar.remote[other_key]['summary'] = 'Other baseline changed'
        calendar.remote[other_key]['etag'] = 'other-change'
    elif change == 'other_etag_missing':
        _, other_key = remote_row(app, calendar, kind='class', source='other-class')
        calendar.remote[other_key].pop('etag')
    elif change == 'source_changed':
        content['ics'] = content['ics'].replace(b'20260925T030000Z', b'20260925T040000Z').replace(
            b'20260925T043000Z', b'20260925T053000Z')
    elif change == 'source_error':
        content['fail'] = True
    elif change == 'calendar_error':
        calendar.read_failure = True
    response = app.handle_callback('101', token)
    assert not response['buttons'] and 'secret' not in str(response), response
    assert calendar.writes == writes
    assert published(app) == baseline
    assert 'устарело' in app.handle_callback('101', token)['text']


def test_rerun_restart_expiry_foreign_user_and_revoke_invalidate_approval(tmp_path):
    app, calendar, _, factory = setup(tmp_path / 'state')
    _, key = remote_row(app, calendar, kind='class')
    manual_move(calendar, key, 3)
    token = confirmation(app.handle_text('101', '/review_calendar'))
    assert app.handle_callback('202', token) is None
    assert 'устарело' in factory(tmp_path / 'state').handle_callback('101', token)['text']
    fresh = confirmation(app.handle_text('101', '/review_calendar'))
    assert 'устарело' in app.handle_callback('101', token)['text']
    fresh = confirmation(app.handle_text('101', '/review_calendar'))
    app.now = lambda: NOW + timedelta(minutes=11)
    assert 'устарело' in app.handle_callback('101', fresh)['text']
    assert len([write for write in calendar.writes if write[0] in {'update', 'delete'}]) == 0

    bot = InvitedBot('synthetic', tmp_path / 'beta', lambda chat: calendar,
                     lambda root, chat, adapter: app if chat == '101' else factory(root, chat, adapter))
    for chat in ('101', '202'):
        bot.invites.set_access(chat, True)
    app.now = lambda: NOW
    button = confirmation(bot.process_update(envelope(1, text='/review_calendar')))
    assert not bot.process_update(envelope(2, '202', data=button))['buttons']
    bot.invites.set_access('101', False)
    assert bot.process_update(envelope(3, data=button)) is None
    assert len([write for write in calendar.writes if write[0] in {'update', 'delete'}]) == 0


def test_accepted_decision_survives_backup_restore_and_later_source_change(tmp_path):
    app, calendar, content, factory = setup(tmp_path / 'state')
    uid, key = remote_row(app, calendar, kind='class')
    _, after = manual_move(calendar, key, 3)
    writes = list(calendar.writes)
    app.handle_callback('101', confirmation(app.handle_text('101', '/review_calendar')))
    assert calendar.writes == writes

    archive = tmp_path / 'synthetic.zip'
    UserStateBackupService(app.root).backup(app.account.id, archive)
    restored_root = tmp_path / 'restored'
    UserStateBackupService(restored_root).restore(app.account.id, archive)
    restored = factory(restored_root)
    preview = restored.handle_text('101', '/weekly_preview')
    assert preview['buttons'] and calendar.writes == writes, preview
    restored.handle_callback('101', confirmation(preview))
    assert next(e for e in calendar.remote.values() if e['iCalUID'] == uid)['start']['dateTime'] == after[0]

    # A later source time change still cannot overwrite the accepted Calendar time.
    content['ics'] = content['ics'].replace(b'20260924T030000Z', b'20260924T040000Z').replace(
        b'20260924T043000Z', b'20260924T053000Z')
    later = restored.handle_text('101', '/weekly_preview')
    assert later['buttons'], later
    restored.handle_callback('101', confirmation(later))
    assert next(e for e in calendar.remote.values() if e['iCalUID'] == uid)['start']['dateTime'] == after[0]


def test_moved_preparation_reserves_its_exact_time_when_source_adds_a_class(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    uid, key = remote_row(app, calendar, kind='preparation')
    _, after = manual_move(calendar, key, 1)
    app.handle_callback('101', confirmation(app.handle_text('101', '/review_calendar')))

    third = BOTH.split(b'BEGIN:VEVENT')[1].split(b'END:VEVENT')[0]
    third = b'BEGIN:VEVENT' + third.replace(b'pilot-lecture', b'third-class').replace(
        b'20260924', b'20260926') + b'END:VEVENT\r\n'
    content['ics'] = BOTH.replace(b'END:VCALENDAR', third + b'END:VCALENDAR')
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    app.handle_callback('101', confirmation(preview))
    moved = next(event for event in calendar.remote.values() if event['iCalUID'] == uid)
    assert (moved['start']['dateTime'], moved['end']['dateTime']) == after
    moved_start, moved_end = map(datetime.fromisoformat, after)
    newly_planned = [event for event in calendar.remote.values()
                     if event['iCalUID'].startswith('prep:') and event['iCalUID'] != uid
                     and 'third-class' in event['extendedProperties']['private'].get(
                         'personal_os_source_event_id', '')]
    assert newly_planned
    for event in newly_planned:
        start = datetime.fromisoformat(event['start']['dateTime'])
        end = datetime.fromisoformat(event['end']['dateTime'])
        assert end <= moved_start or start >= moved_end


def test_pending_remote_update_blocks_manual_review(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    content['ics'] = content['ics'].replace(b'20260924T030000Z', b'20260924T040000Z').replace(
        b'20260924T043000Z', b'20260924T053000Z')
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    calendar.fail_update = True
    app.handle_callback('101', confirmation(preview))
    assert next(iter(app.operations.load_all().values())).pending_plan_update
    calendar.fail_update = False
    response = app.handle_text('101', '/review_calendar')
    assert not response['buttons'], response


def test_real_invited_main_manual_move_requires_two_confirmations(tmp_path, monkeypatch):
    import os
    import sys
    from scripts import invited_beta_bot

    calendar, content = DeleteCalendar(), {'ics': BOTH}
    monkeypatch.setattr(os, 'environ', {
        'TELEGRAM_BOT_TOKEN': 'synthetic', 'PERSONAL_OS_BETA_DIR': str(tmp_path / 'beta'),
        'PERSONAL_OS_GOOGLE_DIR': str(tmp_path / 'google'),
    })
    monkeypatch.setattr('adapters.google_calendar_adapter.GoogleCalendarAdapter', lambda config: calendar)
    monkeypatch.setattr(PilotSource, '_read', lambda self, source: content['ics'])

    def run(bot):
        bot.process_update(envelope(0))
        bot.applications['101'][1].now = lambda: NOW
        onboard_transport(bot)
        initial = bot.process_update(envelope(101, text='/weekly_preview'))
        assert 'опубликован' in bot.process_update(envelope(102, data=confirmation(initial)))['text']
        app = bot.applications['101'][1]
        _, key = remote_row(app, calendar, kind='class')
        _, after = manual_move(calendar, key, 3)
        writes = list(calendar.writes)
        review = bot.process_update(envelope(103, text='/review_calendar'))
        assert review['buttons'], review
        accepted = bot.process_update(envelope(104, data=confirmation(review)))
        assert not accepted['buttons'] and calendar.writes == writes
        replan = bot.process_update(envelope(105, text='/weekly_preview'))
        assert replan['buttons'] and calendar.writes == writes, replan
        bot.process_update(envelope(106, data=confirmation(replan)))
        assert calendar.remote[key]['start']['dateTime'] == after[0]
        writes = list(calendar.writes)
        assert bot.process_update(envelope(106, data=confirmation(replan))) is None
        bot.process_update(envelope(107, text='/weekly_preview'))
        assert calendar.writes == writes

    monkeypatch.setattr(InvitedBot, 'run_forever', run)
    previous = os.umask(0o077)
    try:
        monkeypatch.setattr(sys, 'argv', ['invited_beta_bot', 'invite', '101'])
        assert invited_beta_bot.main() == 0
        monkeypatch.setattr(sys, 'argv', ['invited_beta_bot', 'run'])
        assert invited_beta_bot.main() == 0
    finally:
        os.umask(previous)
