"""Versioned publication updates, temporary state and synthetic providers only."""
from copy import deepcopy
from functools import partial
from types import SimpleNamespace

import pytest

from services.closed_beta_runtime import ClosedBetaApplication, PilotSource
from services.invited_beta_runtime import InvitedBot
from services.draft_operation_store import DraftOperationStore
from services.user_state_backup import UserStateBackupService
from tests.test_closed_beta_runtime import Calendar, ICS, NOW, onboard, confirmation
from tests.test_invited_beta import envelope


class ProcessCrash(BaseException):
    pass


class UpdateCalendar(Calendar):
    def __init__(self):
        super().__init__()
        self.crash_after_write = False
        self.fail_update = False
        self.concurrent_edit = False
        self.update_count = 0

    def _insert_event(self, event, calendar_id, **kwargs):
        identifier = super()._insert_event(event, calendar_id, **kwargs)
        self.remote[(calendar_id, identifier)].update(location=getattr(event, 'location', ''), etag='v1')
        return identifier

    def _update_event(self, calendar_id, event_id, data, *, strict=False, expected_etag=None):
        assert strict and expected_etag
        old = self.remote[(calendar_id, event_id)]
        if self.concurrent_edit:
            self.concurrent_edit = False
            old['summary'] = 'User edit during apply'
            old['etag'] = 'user-edit'
        if old['etag'] != expected_etag:
            raise ValueError('synthetic precondition failed')
        if self.fail_update:
            raise ValueError('synthetic provider failure')
        self.update_count += 1
        self.writes.append(('update', data.uid))
        old.update(summary=data.summary, description=data.description, location=data.location,
                   iCalUID=data.uid, start={'dateTime': data.dtstart.isoformat()},
                   end={'dateTime': data.dtend.isoformat()}, etag=f'updated-{self.update_count}',
                   extendedProperties={'private': {'personal_os_block_id': data.system_block_id,
                       'personal_os_operation_id': data.system_operation_id,
                       'personal_os_source_event_id': data.system_source_event_id}})
        if self.crash_after_write:
            self.crash_after_write = False
            raise ProcessCrash()
        return event_id


def setup(tmp_path):
    calendar = UpdateCalendar()
    content = {'ics': ICS}
    def fetch(url, path, **kwargs):
        if content.get('fail'):
            raise ValueError('synthetic secret source')
        path.write_bytes(content['ics'])
    factory = partial(ClosedBetaApplication, enable_plan_updates=True, now=lambda: NOW,
                      source_factory=lambda store: PilotSource(store, fetcher=fetch))
    app = factory(tmp_path, '101', calendar)
    onboard(app)
    result = app.handle_callback('101', confirmation(app.handle_text('101', '/weekly_preview')))
    assert 'опубликован' in result['text'], result
    operation = next(iter(app.operations.load_all().values()))
    assert operation.published_plan
    return app, calendar, content, factory


def move(content):
    content['ics'] = ICS.replace(b'030000Z', b'040000Z').replace(b'043000Z', b'053000Z')


def test_confirmed_move_preview_repeat_and_version(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    writes = list(calendar.writes)
    old_ids = set(calendar.remote)
    move(content)
    preview = app.handle_text('101', '/update_all')
    assert 'v1 → v2' in preview['text'] and '10:00' in preview['text'] and '11:00' in preview['text']
    assert 'Удалений: 0' in preview['text']
    assert calendar.writes == writes
    assert not next(iter(app.operations.load_all().values())).pending_plan_update
    token = confirmation(preview)
    assert 'v2 обновлён' in app.handle_callback('101', token)['text']
    assert set(calendar.remote) == old_ids
    assert calendar.update_count > 0
    writes = list(calendar.writes)
    assert 'устарело' in app.handle_callback('101', token)['text']
    assert 'Изменений и повторных записей нет' in app.handle_text('101', '/update_all')['text']
    assert calendar.writes == writes
    assert next(iter(app.operations.load_all().values())).version == 2


def test_addition_is_previewed_and_inserted_once(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    added = ICS.split(b'BEGIN:VEVENT')[1].split(b'END:VEVENT')[0]
    added = b'BEGIN:VEVENT' + added.replace(b'pilot-lecture', b'pilot-new').replace(b'20260924', b'20260925') + b'END:VEVENT\r\n'
    content['ics'] = ICS.replace(b'END:VCALENDAR', added + b'END:VCALENDAR')
    before = list(calendar.writes)
    preview = app.handle_text('101', '/weekly_preview')
    assert '+ Добавить:' in preview['text'], preview
    assert calendar.writes == before
    assert 'обновлён' in app.handle_callback('101', confirmation(preview))['text']
    uids = [row['iCalUID'] for row in calendar.remote.values()]
    assert len(uids) == len(set(uids))
    assert len(uids) == 4


@pytest.mark.parametrize('failure', ['source', 'calendar', 'stale', 'manual_move', 'manual_delete', 'manual_title', 'etag_race'])
def test_apply_preflight_and_conditional_write_preserve_user_changes(tmp_path, failure):
    app, calendar, content, _ = setup(tmp_path)
    move(content)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    before = list(calendar.writes)
    event = next(iter(calendar.remote.values()))
    if failure == 'source':
        content['fail'] = True
    elif failure == 'calendar':
        calendar.read_failure = True
    elif failure == 'stale':
        content['ics'] = content['ics'].replace(b'040000Z', b'050000Z').replace(b'053000Z', b'063000Z')
    elif failure == 'manual_move':
        event['start']['dateTime'] = '2026-09-24T19:00:00+07:00'
    elif failure == 'manual_delete':
        calendar.remote.pop(next(iter(calendar.remote)))
    elif failure == 'manual_title':
        event['summary'] = 'My title'
    else:
        calendar.concurrent_edit = True
    result = app.handle_callback('101', token)
    assert 'обновлён и проверен' not in result['text']
    assert calendar.writes == before
    assert 'secret' not in result['text']


def test_crash_after_remote_write_restore_and_resume_without_duplicate(tmp_path):
    app, calendar, content, factory = setup(tmp_path / 'state')
    move(content)
    preview = app.handle_text('101', '/weekly_preview')
    calendar.crash_after_write = True
    with pytest.raises(ProcessCrash):
        app.handle_callback('101', confirmation(preview))
    assert calendar.update_count == 1
    operation = next(iter(app.operations.load_all().values()))
    assert operation.pending_plan_update and operation.projection_pending
    archive = tmp_path / 'synthetic.zip'
    UserStateBackupService(app.root).backup(app.account.id, archive)
    restored_root = tmp_path / 'restored'
    UserStateBackupService(restored_root).restore(app.account.id, archive)
    restarted = factory(restored_root, '101', calendar)
    assert 'устарело' in restarted.handle_callback('101', confirmation(preview))['text']
    continuation = restarted.handle_text('101', '/weekly_preview')
    assert 'продолжение частичной записи' in continuation['text'], continuation
    assert 'обновлён' in restarted.handle_callback('101', confirmation(continuation))['text']
    classes = [row for row in calendar.writes if row == ('update', 'personal-university:pilot-lecture')]
    assert len(classes) == 1
    writes = list(calendar.writes)
    restarted.handle_text('101', '/weekly_preview')
    assert calendar.writes == writes


def test_missing_source_never_proposes_or_performs_delete(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    # A valid different snapshot leaves the original missing/ambiguous.
    content['ics'] = ICS.replace(b'pilot-lecture', b'unrelated').replace(b'20260924', b'20260926').replace(b'SUMMARY:', b'SUMMARY:New ')
    before = deepcopy(calendar.remote)
    for _ in range(3):
        assert not app.handle_text('101', '/weekly_preview')['buttons']
    assert calendar.remote == before


def test_legacy_publication_without_baseline_is_not_migrated(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    operation = next(iter(app.operations.load_all().values()))
    serialized = DraftOperationStore._serialize(operation)
    serialized.pop('published_plan')
    serialized.pop('pending_plan_update')
    restored = DraftOperationStore._deserialize(serialized)
    assert restored.published_plan == {} and restored.pending_plan_update == {}
    app.operations.save(restored)
    before = list(calendar.writes)
    move(content)
    result = app.handle_text('101', '/weekly_preview')
    assert 'миграции нет' in result['text']
    assert not result['buttons'] and calendar.writes == before


def test_invited_transport_foreign_and_revoked_update_confirmation(tmp_path):
    app, calendar, content, factory = setup(tmp_path / 'initial')
    bot = InvitedBot('synthetic', tmp_path / 'beta', lambda chat: calendar,
                     lambda root, chat, adapter: app if chat == '101' else factory(root, chat, UpdateCalendar()))
    bot.invites.set_access('101', True)
    bot.invites.set_access('202', True)
    move(content)
    preview = bot.process_update(envelope(1, text='/weekly_preview'))
    token = confirmation(preview)
    before = list(calendar.writes)
    bot.process_update(envelope(2, '202', data=token))
    bot.invites.set_access('101', False)
    assert bot.process_update(envelope(3, data=token)) is None
    assert calendar.writes == before


@pytest.mark.parametrize('delete_after_crash', [False, True])
def test_ambiguous_insert_resume_never_recreates_manual_deletion(tmp_path, delete_after_crash):
    app, calendar, content, factory = setup(tmp_path)
    addition = ICS.split(b'BEGIN:VEVENT')[1].split(b'END:VEVENT')[0]
    addition = b'BEGIN:VEVENT' + addition.replace(b'pilot-lecture', b'pilot-new').replace(b'20260924', b'20260925') + b'END:VEVENT\r\n'
    content['ics'] = ICS.replace(b'END:VCALENDAR', addition + b'END:VCALENDAR')
    preview = app.handle_text('101', '/weekly_preview')
    original = calendar._insert_event
    inserted = []
    def crash(data, calendar_id, **kwargs):
        identifier = original(data, calendar_id, **kwargs)
        inserted.append((calendar_id, identifier))
        raise ProcessCrash()
    calendar._insert_event = crash
    with pytest.raises(ProcessCrash):
        app.handle_callback('101', confirmation(preview))
    calendar._insert_event = original
    if delete_after_crash:
        calendar.remote.pop(inserted[0])
    writes = list(calendar.writes)
    restarted = factory(tmp_path, '101', calendar)
    next_preview = restarted.handle_text('101', '/weekly_preview')
    if delete_after_crash:
        assert 'неоднозначен' in next_preview['text']
        assert not next_preview['buttons']
        assert calendar.writes == writes
    else:
        assert 'обновлён' in restarted.handle_callback('101', confirmation(next_preview))['text']
        uids = [row['iCalUID'] for row in calendar.remote.values()]
        assert len(uids) == len(set(uids)) == 4


def test_missing_etag_and_changed_identity_fail_before_writes(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    move(content)
    first = next(iter(calendar.remote.values()))
    first.pop('etag')
    writes = list(calendar.writes)
    result = app.handle_text('101', '/weekly_preview')
    assert not result['buttons']
    assert 'версию события' in result['text']
    assert calendar.writes == writes


def test_stale_expired_cancelled_and_restarted_update_buttons(tmp_path):
    from datetime import timedelta
    app, calendar, content, factory = setup(tmp_path)
    move(content)
    before = list(calendar.writes)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    app.now = lambda: NOW + timedelta(minutes=11)
    assert 'устарело' in app.handle_callback('101', token)['text']
    app.now = lambda: NOW
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    app.handle_callback('101', 'pilot:cancel')
    assert 'устарело' in app.handle_callback('101', token)['text']
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    restarted = factory(tmp_path, '101', calendar)
    assert 'устарело' in restarted.handle_callback('101', token)['text']
    assert calendar.writes == before


def test_changed_source_after_partial_write_does_not_recalculate_pending_target(tmp_path):
    app, calendar, content, factory = setup(tmp_path)
    move(content)
    token = confirmation(app.handle_text('101', '/weekly_preview'))
    calendar.crash_after_write = True
    with pytest.raises(ProcessCrash):
        app.handle_callback('101', token)
    content['ics'] = content['ics'].replace(b'040000Z', b'050000Z').replace(b'053000Z', b'063000Z')
    restarted = factory(tmp_path, '101', calendar)
    before = list(calendar.writes)
    response = restarted.handle_text('101', '/weekly_preview')
    assert 'после частичной записи' in response['text']
    assert not response['buttons'] and calendar.writes == before


def test_real_entrypoint_onboarding_publish_and_confirmed_update(tmp_path, monkeypatch):
    import os
    import sys
    from scripts import invited_beta_bot
    from tests.test_invited_beta import onboard as onboard_transport
    calendar = UpdateCalendar()
    content = {'ics': ICS}
    monkeypatch.setattr(os, 'environ', {
        'TELEGRAM_BOT_TOKEN': 'synthetic',
        'PERSONAL_OS_BETA_DIR': str(tmp_path / 'beta'),
        'PERSONAL_OS_GOOGLE_DIR': str(tmp_path / 'google'),
    })
    monkeypatch.setattr('adapters.google_calendar_adapter.GoogleCalendarAdapter', lambda config: calendar)
    monkeypatch.setattr(PilotSource, '_read', lambda self, source: content['ics'])
    def run(bot):
        assert bot.process_update(envelope(0))['step'] == 'timezone'
        application = bot.applications['101'][1]
        assert application.updates is not None
        application.now = lambda: NOW
        onboard_transport(bot)
        assert not calendar.writes
        first = bot.process_update(envelope(101, text='/weekly_preview'))
        assert not calendar.writes
        assert 'опубликован' in bot.process_update(envelope(102, data=confirmation(first)))['text']
        move(content)
        before = list(calendar.writes)
        preview = bot.process_update(envelope(103, text='/update_all'))
        assert 'v1 → v2' in preview['text']
        assert calendar.writes == before
        assert 'v2 обновлён' in bot.process_update(envelope(104, data=confirmation(preview)))['text']
    monkeypatch.setattr(InvitedBot, 'run_forever', run)
    previous = os.umask(0o077)
    try:
        monkeypatch.setattr(sys, 'argv', ['invited_beta_bot', 'invite', '101'])
        assert invited_beta_bot.main() == 0
        monkeypatch.setattr(sys, 'argv', ['invited_beta_bot', 'run'])
        assert invited_beta_bot.main() == 0
    finally:
        os.umask(previous)


def test_guarded_preparation_update_never_falls_back_to_unguarded_adapter(tmp_path):
    app, calendar, content, _ = setup(tmp_path)
    operation = next(iter(app.operations.load_all().values()))
    block = operation.blocks[0]
    def unsafe_adapter(calendar_id, event_id, data):
        pytest.fail('An adapter without conditional-write support must not receive an unguarded retry')
    calendar._update_event = unsafe_adapter
    desired = app.projector._event_data(block, operation)
    with pytest.raises(TypeError):
        app.projector._write_verified(operation, block, operation.calendar_id, desired, 'update',
                                      operation.calendar_event_ids[block.id], app.operations.save,
                                      expected_etag='v1')
