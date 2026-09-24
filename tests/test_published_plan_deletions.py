"""Explicit source review and guarded deletion, no live providers."""
from copy import deepcopy
from datetime import timedelta

import pytest

from services.closed_beta_runtime import ClosedBetaApplication, PilotSource
from services.invited_beta_runtime import InvitedBot
from services.user_state_backup import UserStateBackupService
from tests.test_closed_beta_runtime import ICS, NOW, onboard, confirmation
from tests.test_invited_beta import envelope
from tests.test_published_plan_updates import UpdateCalendar, ProcessCrash


class DeleteCalendar(UpdateCalendar):
    def __init__(self):
        super().__init__()
        self.crash_delete = False
        self.fail_delete = False
        self.race_delete = False
        self.false_success = False

    def get_event_by_id(self, calendar_id, event_id, *, strict=True):
        if self.read_failure:
            raise ValueError('synthetic read failure')
        return deepcopy(self.remote.get((calendar_id, event_id)))

    def _delete_event(self, calendar_id, event_id, *, strict=False, expected_etag=None):
        assert strict and expected_etag
        event = self.remote.get((calendar_id, event_id))
        if self.race_delete:
            self.race_delete = False
            event['summary'] = 'User changed this'
            event['etag'] = 'manual'
        if event and event['etag'] != expected_etag:
            raise ValueError('synthetic precondition failed')
        if self.fail_delete:
            raise ValueError('synthetic secret write failure')
        if self.false_success:
            return True
        self.writes.append(('delete', event_id))
        self.remote.pop((calendar_id, event_id), None)
        if self.crash_delete:
            self.crash_delete = False
            raise ProcessCrash()
        return True


OTHER = ICS.replace(b'pilot-lecture', b'other-class').replace(b'20260924', b'20260925')
BOTH = ICS.replace(b'END:VCALENDAR', b'BEGIN:VEVENT' + OTHER.split(b'BEGIN:VEVENT')[1])


def setup(tmp_path):
    calendar, content = DeleteCalendar(), {'ics': BOTH}
    def fetch(url, path, **kwargs):
        if content.get('fail'):
            raise ValueError('synthetic secret source')
        path.write_bytes(content['ics'])
    def factory(root, chat='101', adapter=None):
        return ClosedBetaApplication(root, chat, adapter or calendar, enable_plan_updates=True,
            source_factory=lambda store: PilotSource(store, fetcher=fetch), now=lambda: NOW)
    app = factory(tmp_path)
    onboard(app)
    result = app.handle_callback('101', confirmation(app.handle_text('101', '/weekly_preview')))
    assert 'опубликован' in result['text'], result
    assert len(calendar.remote) == 4
    return app, calendar, content, factory


def test_disappearance_requires_review_and_confirmation_then_repeat_is_inert(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    content['ics'] = OTHER
    before = deepcopy(calendar.remote)
    for _ in range(3):
        result = app.handle_text('101', '/weekly_preview')
        assert '/review_missing' in result['text'] and not result['buttons']
    review = app.handle_text('101', '/review_missing')
    assert '24.09' in review['text'] and 'Остальные события' in review['text']
    assert calendar.remote == before
    app.handle_callback('101', 'pilot:cancel')
    assert calendar.remote == before
    token = confirmation(app.handle_text('101', '/review_missing'))
    result = app.handle_callback('101', token)
    assert 'Удаление проверено' in result['text'], result
    assert len(calendar.remote) == 2
    writes = list(calendar.writes)
    assert 'устарело' in app.handle_callback('101', token)['text']
    assert not app.handle_text('101', '/review_missing')['buttons']
    assert calendar.writes == writes
    # Remaining source changes get their own approval, never implicit updates.
    next_preview = app.handle_text('101', '/weekly_preview')
    assert 'Обновление плана' in next_preview['text'], next_preview
    assert calendar.writes == writes
    assert 'обновлён' in app.handle_callback('101', confirmation(next_preview))['text']
    writes = list(calendar.writes)
    app.handle_text('101', '/weekly_preview')
    assert calendar.writes == writes


def test_explicit_source_cancellation_also_requires_review(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    content['ics'] = BOTH.replace(b'UID:pilot-lecture', b'UID:pilot-lecture\r\nSTATUS:CANCELLED')
    before = list(calendar.writes)
    assert not app.handle_text('101', '/weekly_preview')['buttons']
    token = confirmation(app.handle_text('101', '/review_missing'))
    assert calendar.writes == before
    assert 'Удаление проверено' in app.handle_callback('101', token)['text']


@pytest.mark.parametrize('failure', ['source', 'calendar', 'source_returned', 'settings', 'manual_move',
                                     'manual_delete', 'ownership', 'etag_race', 'expired', 'false_success'])
def test_changed_conditions_do_not_delete(tmp_path, failure):
    app, calendar, content, _ = setup(tmp_path)
    content['ics'] = OTHER
    token = confirmation(app.handle_text('101', '/review_missing'))
    before = list(calendar.writes)
    event = next(iter(calendar.remote.values()))
    if failure == 'source': content['fail'] = True
    elif failure == 'calendar': calendar.read_failure = True
    elif failure == 'source_returned': content['ics'] = BOTH
    elif failure == 'settings': app.handle_text('101', '/travel 45')
    elif failure == 'manual_move': event['start']['dateTime'] = '2026-09-24T19:00:00+07:00'
    elif failure == 'manual_delete': calendar.remote.pop(next(iter(calendar.remote)))
    elif failure == 'ownership': event['extendedProperties']['private']['personal_os_block_id'] = 'not-ours'
    elif failure == 'etag_race': calendar.race_delete = True
    elif failure == 'expired': app.now = lambda: NOW + timedelta(minutes=11)
    elif failure == 'false_success': calendar.false_success = True
    response = app.handle_callback('101', token)
    assert 'Удаление проверено' not in response['text']
    assert calendar.writes == before
    assert 'secret' not in response['text']


def test_partial_delete_crash_backup_restore_resume(tmp_path):
    app, calendar, content, factory = setup(tmp_path / 'state')
    content['ics'] = OTHER
    token = confirmation(app.handle_text('101', '/review_missing'))
    calendar.crash_delete = True
    with pytest.raises(ProcessCrash): app.handle_callback('101', token)
    assert len(calendar.remote) == 3
    archive = tmp_path / 'synthetic.zip'
    UserStateBackupService(app.root).backup(app.account.id, archive)
    restore = tmp_path / 'restored'
    UserStateBackupService(restore).restore(app.account.id, archive)
    restarted = factory(restore)
    assert 'устарело' in restarted.handle_callback('101', token)['text']
    preview = restarted.handle_text('101', '/review_missing')
    assert 'Продолжение частичного удаления' in preview['text'], preview
    assert 'Удаление проверено' in restarted.handle_callback('101', confirmation(preview))['text']
    assert len(calendar.remote) == 2
    deletions = [event for action, event in calendar.writes if action == 'delete']
    assert len(deletions) == len(set(deletions)) == 2


def test_source_reappearing_after_partial_delete_blocks_continuation(tmp_path):
    app, calendar, content, factory = setup(tmp_path)
    content['ics'] = OTHER
    token = confirmation(app.handle_text('101', '/review_missing'))
    calendar.crash_delete = True
    with pytest.raises(ProcessCrash): app.handle_callback('101', token)
    content['ics'] = BOTH
    restarted = factory(tmp_path)
    writes = list(calendar.writes)
    result = restarted.handle_text('101', '/review_missing')
    assert 'Начатое удаление остановлено' in result['text'], result
    assert not result['buttons'] and calendar.writes == writes


def test_reappearance_after_completed_delete_is_only_an_addition_preview(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    content['ics'] = OTHER
    token = confirmation(app.handle_text('101', '/review_missing'))
    assert 'Удаление проверено' in app.handle_callback('101', token)['text']
    content['ics'] = BOTH
    writes = list(calendar.writes)
    result = app.handle_text('101', '/weekly_preview')
    assert '+ Добавить:' in result['text'], result
    assert calendar.writes == writes


def test_invited_user_cannot_apply_another_users_deletion_and_revoke_blocks_it(tmp_path):
    app, calendar, content, factory = setup(tmp_path / 'state')
    content['ics'] = OTHER
    bot = InvitedBot('synthetic', tmp_path / 'beta', lambda chat: DeleteCalendar(),
                     lambda root, chat, adapter: app if chat == '101' else factory(root, chat, adapter))
    for chat in ('101', '202'): bot.invites.set_access(chat, True)
    token = confirmation(bot.process_update(envelope(1, text='/review_missing')))
    writes = list(calendar.writes)
    assert not bot.process_update(envelope(2, '202', data=token))['buttons']
    bot.invites.set_access('101', False)
    assert bot.process_update(envelope(3, data=token)) is None
    assert calendar.writes == writes


def test_guarded_delete_never_falls_back_without_etag(tmp_path):
    from services.calendar.projection_state import delete_owned_verified, CalendarProjectionError
    app, calendar, content, _ = setup(tmp_path)
    calendar_id, event_id = next(iter(calendar.remote))
    def unsafe_adapter(calendar_id, event_id):
        pytest.fail('No unguarded fallback is allowed')
    calendar._delete_event = unsafe_adapter
    with pytest.raises(CalendarProjectionError):
        delete_owned_verified(calendar, calendar_id, event_id, lambda event: True, expected_etag='v1')


def test_cancelled_state_without_current_source_evidence_cannot_authorize_deletion(tmp_path):
    from models import PersonalEventState
    from services.published_plan_updates import PlanConflict
    app, _, _, _ = setup(tmp_path)
    operation = next(iter(app.operations.load_all().values()))
    events = app.snapshot.personal_events()
    events[0].state = PersonalEventState.CANCELLED
    events[0].metadata['change_reason'] = 'explicit_source_cancellation'
    with pytest.raises(PlanConflict, match='Источник не подтверждает'):
        app.updates.deletion_targets(operation, events)


def test_real_invited_main_missing_review_confirm_and_repeat(tmp_path, monkeypatch):
    import os
    import sys
    from scripts import invited_beta_bot
    from tests.test_invited_beta import onboard as onboard_transport
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
        preview = bot.process_update(envelope(101, text='/weekly_preview'))
        assert not calendar.writes
        assert 'опубликован' in bot.process_update(envelope(102, data=confirmation(preview)))['text']
        content['ics'] = OTHER
        writes = list(calendar.writes)
        assert not bot.process_update(envelope(103, text='/update_all'))['buttons']
        review = bot.process_update(envelope(104, text='/review_missing'))
        assert calendar.writes == writes
        token = confirmation(review)
        assert 'Удаление проверено' in bot.process_update(envelope(105, data=token))['text']
        assert len(calendar.remote) == 2
        writes = list(calendar.writes)
        assert bot.process_update(envelope(105, data=token)) is None
        assert 'устарело' in bot.process_update(envelope(106, data=token))['text']
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


@pytest.mark.parametrize('replacement', [False, True])
def test_completed_delete_tombstone_resumes_without_second_delete(tmp_path, replacement):
    app, calendar, content, factory = setup(tmp_path)
    content['ics'] = OTHER
    token = confirmation(app.handle_text('101', '/review_missing'))
    before = deepcopy(calendar.remote)
    calendar.crash_delete = True
    with pytest.raises(ProcessCrash): app.handle_callback('101', token)
    removed = next(key for key in before if key not in calendar.remote)
    tombstone_key = (removed[0], 'replacement-id') if replacement else removed
    calendar.remote[tombstone_key] = {'id': tombstone_key[1], 'iCalUID': before[removed]['iCalUID'], 'status': 'cancelled'}
    restarted = factory(tmp_path)
    preview = restarted.handle_text('101', '/review_missing')
    if replacement:
        assert not preview['buttons']
        assert len([row for row in calendar.writes if row[0] == 'delete']) == 1
        return
    assert preview['buttons'], preview
    assert 'Удаление проверено' in restarted.handle_callback('101', confirmation(preview))['text']
    deletions = [identifier for action, identifier in calendar.writes if action == 'delete']
    assert len(deletions) == len(set(deletions)) == 2
