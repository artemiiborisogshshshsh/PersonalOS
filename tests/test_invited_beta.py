"""Synthetic common entrypoint checks; no real credentials or providers."""
import os
import sys
from functools import partial
import shutil
import threading

import pytest

from services.closed_beta_runtime import ClosedBetaApplication, PilotSource
from services.invited_beta_runtime import InvitedBot, Invitations
from services.user_registry import UserRegistryStore
from services.user_state_backup import UserStateBackupService
from tests.test_closed_beta_runtime import Calendar, ICS, NOW, URL, confirmation


def envelope(number, chat='101', text='/start', data=None, sender=None, kind='private'):
    message = {'chat': {'id': chat, 'type': kind}, 'from': {'id': sender or chat}, 'text': text}
    if data is not None:
        return {'update_id': number, 'callback_query': {
            'id': str(number), 'from': {'id': sender or chat}, 'message': message, 'data': data}}
    return {'update_id': number, 'message': message}


def setup(tmp_path):
    calendars = {'101': Calendar(), '202': Calendar()}
    content = {'ics': ICS}
    def fetch(url, path, **kwargs):
        if content.get('fail'):
            raise ValueError('synthetic failure')
        path.write_bytes(content['ics'])
    factory = partial(ClosedBetaApplication, now=lambda: NOW,
                      source_factory=lambda store: PilotSource(store, fetcher=fetch))
    bot = InvitedBot('synthetic', tmp_path, calendars.__getitem__, factory)
    return bot, calendars, content


def onboard(bot, chat='101', start=1):
    sequence = iter(range(start, start + 100))
    def text(value):
        return bot.process_update(envelope(next(sequence), chat, text=value))
    def callback(value):
        return bot.process_update(envelope(next(sequence), chat, data=value))
    assert text('/start')['step'] == 'timezone'
    assert callback('ob:timezone:Asia/Tomsk')['step'] == 'source'
    assert text('/connect_tpu ' + URL)['step'] == 'attendance'
    response = text('/attendance')
    for _ in range(10):
        if response.get('attendance_complete'):
            break
        response = callback(response['buttons'][0][0]['callback_data'])
    assert response['attendance_complete']
    assert text('/sleep 23:00 06:40')['step'] == 'profile'
    assert text('/travel 30')['step'] == 'calendar'
    assert 'подключён' in text('/calendar_status')['text']


def test_unknown_spoofed_group_and_revoked_never_create_account(tmp_path):
    bot, calendars, _ = setup(tmp_path)
    assert bot.process_update(envelope(1)) is None
    assert not list(tmp_path.iterdir())
    bot.invites.set_access('101', True)
    for update in (envelope(2, sender='202'), envelope(3, kind='group'),
                   envelope(4, data='ob:timezone:Asia/Tomsk', sender='202')):
        assert bot.process_update(update) is None
    assert not (bot.invites.directory('101') / 'state').exists()
    bot.invites.set_access('101', False)
    assert bot.process_update(envelope(5)) is None
    assert calendars['101'].writes == []


def test_two_users_foreign_button_revoke_repeat_restart(tmp_path):
    bot, calendars, _ = setup(tmp_path)
    for chat in calendars:
        bot.invites.set_access(chat, True)
        onboard(bot, chat)
    previews = {chat: bot.process_update(envelope(101, chat, '/weekly_preview')) for chat in calendars}
    assert all(not calendar.writes for calendar in calendars.values())
    first = confirmation(previews['101'])
    assert not bot.process_update(envelope(102, '202', data=first))['buttons']
    assert not calendars['202'].writes
    bot.invites.set_access('101', False)
    assert bot.process_update(envelope(102, data=first)) is None
    bot.invites.set_access('101', True)
    assert not bot.process_update(envelope(103, data=first))['buttons']
    assert not calendars['101'].writes
    fresh = bot.process_update(envelope(104, text='/weekly_preview'))
    bot.process_update(envelope(105, data=confirmation(fresh)))
    count = len(calendars['101'].writes)
    assert count > 0
    assert bot.process_update(envelope(105, data=confirmation(fresh))) is None
    bot.process_update(envelope(106, data=confirmation(fresh)))
    bot.process_update(envelope(107, text='/update_all'))
    assert len(calendars['101'].writes) == count
    restarted = InvitedBot('synthetic', tmp_path, calendars.__getitem__, bot.application_factory)
    assert restarted.process_update(envelope(105, data=confirmation(fresh))) is None
    restarted.process_update(envelope(108, text='/update_all'))
    assert len(calendars['101'].writes) == count
    assert not calendars['202'].writes
    assert bot.applications['101'][1].directory != bot.applications['202'][1].directory


def test_failure_isolation_partial_restart_and_backup(tmp_path):
    bot, calendars, content = setup(tmp_path / 'beta')
    for chat in calendars:
        bot.invites.set_access(chat, True)
        onboard(bot, chat)
    calendars['101'].read_failure = True
    assert not bot.process_update(envelope(101, text='/weekly_preview'))['buttons']
    assert bot.process_update(envelope(101, '202', '/weekly_preview'))['buttons']
    calendars['101'].read_failure = False
    content['fail'] = True
    assert not bot.process_update(envelope(102, text='/weekly_preview'))['buttons']
    content['fail'] = False
    preview = bot.process_update(envelope(103, text='/weekly_preview'))
    calendars['101'].fail_write_number = 2
    response = bot.process_update(envelope(104, data=confirmation(preview)))
    assert 'не завершена' in response['text']
    app = bot.applications['101'][1]
    archive = tmp_path / 'backup.zip'
    UserStateBackupService(app.root).backup(app.account.id, archive)
    restored_beta = tmp_path / 'restored'
    restored_tenant = restored_beta / app.account.id
    restored_tenant.mkdir(parents=True)
    for name in ('access.json', 'last_update.json'):
        shutil.copyfile(bot.invites.directory('101') / name, restored_tenant / name)
    restored_root = restored_tenant / 'state'
    UserStateBackupService(restored_root).restore(app.account.id, archive)
    assert (restored_root / 'users' / app.account.id / 'draft_operations.json').exists()
    calendars['101'].fail_write_number = None
    restarted = InvitedBot('synthetic', restored_beta, calendars.__getitem__, bot.application_factory)
    assert restarted.process_update(envelope(104, data=confirmation(preview))) is None
    preview = restarted.process_update(envelope(105, text='/weekly_preview'))
    restarted.process_update(envelope(106, data=confirmation(preview)))
    uids = [event['iCalUID'] for event in calendars['101'].remote.values()]
    assert len(uids) == len(set(uids))
    assert len([row for row in calendars['101'].writes if row == ('insert', uids[0])]) == 1
    assert 'уже опубликован' in restarted.process_update(envelope(107, text='/weekly_preview'))['text']


def test_real_main_uses_individual_tokens_and_can_onboard_without_google(tmp_path, monkeypatch):
    from scripts import invited_beta_bot
    root = tmp_path / 'beta'
    google = tmp_path / 'google'
    env = {'PERSONAL_OS_BETA_DIR': str(root), 'PERSONAL_OS_GOOGLE_DIR': str(google),
           'TELEGRAM_BOT_TOKEN': 'synthetic', 'GOOGLE_CALENDAR_TOKEN_PATH': '/never/use/shared'}
    monkeypatch.setattr(os, 'environ', env)
    monkeypatch.setattr(sys, 'argv', ['invited_beta_bot', 'invite', '101'])
    previous = os.umask(0o077)
    try:
        assert invited_beta_bot.main() == 0
        def run(bot):
            assert bot.process_update(envelope(1))['step'] == 'timezone'
            adapter = bot.applications['101'][1].adapter
            assert adapter.token_path == str(google / (UserRegistryStore._user_id('101') + '.json'))
            assert adapter.config['allow_interactive_auth'] is False
            assert adapter.config['initialize_calendar'] is False
        monkeypatch.setattr(InvitedBot, 'run_forever', run)
        monkeypatch.setattr(sys, 'argv', ['invited_beta_bot', 'run'])
        assert invited_beta_bot.main() == 0
    finally:
        os.umask(previous)


@pytest.mark.parametrize('change', ['move', 'delete', 'source', 'stale'])
def test_common_entry_preserves_manual_changes_and_stale_inputs(tmp_path, change):
    bot, calendars, content = setup(tmp_path)
    bot.invites.set_access('101', True)
    onboard(bot)
    preview = bot.process_update(envelope(101, text='/weekly_preview'))
    if change == 'stale':
        content['ics'] = ICS.replace(b'030000Z', b'040000Z').replace(b'043000Z', b'053000Z')
        assert 'изменились' in bot.process_update(envelope(102, data=confirmation(preview)))['text']
        assert not calendars['101'].writes
        return
    bot.process_update(envelope(102, data=confirmation(preview)))
    writes = list(calendars['101'].writes)
    key = next(iter(calendars['101'].remote))
    if change == 'move':
        calendars['101'].remote[key]['start']['dateTime'] = '2026-09-24T20:00:00+07:00'
    elif change == 'delete':
        del calendars['101'].remote[key]
    else:
        content['ics'] = ICS.replace(b'030000Z', b'040000Z').replace(b'043000Z', b'053000Z')
    result = bot.process_update(envelope(103, text='/update_all'))
    assert not result['buttons']
    assert calendars['101'].writes == writes


def test_revoke_waits_for_operation_and_blocks_next_update(tmp_path):
    bot, _, _ = setup(tmp_path)
    bot.invites.set_access('101', True)
    entered, release, revoked = threading.Event(), threading.Event(), threading.Event()
    class BlockingApp:
        def handle_text(self, *args):
            entered.set()
            assert release.wait(5)
            return {'text': 'done'}
    bot.application_factory = lambda *args: BlockingApp()
    operation = threading.Thread(target=lambda: bot.process_update(envelope(1)))
    operation.start()
    assert entered.wait(5)
    def revoke():
        Invitations(tmp_path).set_access('101', False)
        revoked.set()
    revocation = threading.Thread(target=revoke)
    revocation.start()
    try:
        assert not revoked.wait(0.1)
    finally:
        release.set()
        operation.join(5)
        revocation.join(5)
    assert revoked.is_set()
    assert bot.process_update(envelope(2)) is None


def test_polling_routes_updates_and_continues_after_delivery_failure(tmp_path, monkeypatch):
    import requests
    from types import SimpleNamespace
    bot, _, _ = setup(tmp_path)
    for chat in ('101', '202'):
        bot.invites.set_access(chat, True)
    polls, sent = [], []
    def get(url, params, **kwargs):
        polls.append(params)
        if len(polls) > 1:
            raise KeyboardInterrupt()
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            'ok': True, 'result': [envelope(1), envelope(2, '202')]})
    def send(chat, *args):
        sent.append(chat)
        if chat == '101':
            raise requests.RequestException('synthetic')
    monkeypatch.setattr('services.invited_beta_runtime.requests.get', get)
    monkeypatch.setattr(bot, 'send_message', send)
    with pytest.raises(KeyboardInterrupt):
        bot.run_forever()
    assert sent == ['101', '202']
    assert polls[1]['offset'] == 3


def test_polling_does_not_replay_rejected_start_after_invitation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    bot, _, _ = setup(tmp_path)
    def poll_once(bot):
        calls = []
        def get(url, params, **kwargs):
            calls.append(params)
            if len(calls) > 1:
                raise KeyboardInterrupt()
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
                'ok': True, 'result': [envelope(7)]})
        monkeypatch.setattr('services.invited_beta_runtime.requests.get', get)
        monkeypatch.setattr(bot, 'send_message', lambda *args: None)
        with pytest.raises(KeyboardInterrupt):
            bot.run_forever()
        return calls
    poll_once(bot)
    assert not bot.invites.directory('101').exists()
    bot.invites.set_access('101', True)
    restarted, _, _ = setup(tmp_path)
    calls = poll_once(restarted)
    assert calls[0]['offset'] == 8
    assert not restarted.applications
    assert not (bot.invites.directory('101') / 'state').exists()
