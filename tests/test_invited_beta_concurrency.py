"""Deterministic local transport recovery/concurrency; no real providers."""
import json
import threading
from types import SimpleNamespace

import pytest

from services.invited_beta_runtime import DurablePolling, InvitedBot, atomic_json
from tests.test_invited_beta import envelope, setup, onboard
from tests.test_closed_beta_runtime import confirmation


def harness(tmp_path, handler=None, workers=2, max_pending=8):
    calls, sent = [], []
    def factory(directory, chat, adapter):
        def text(_, value):
            calls.append((chat, value))
            return handler(chat, value) if handler else {'text': value}
        return SimpleNamespace(handle_text=text, handle_callback=text)
    bot = InvitedBot('synthetic', tmp_path, lambda chat: chat, factory)
    for chat in ('101', '202'):
        bot.invites.set_access(chat, True)
    bot.send_message = lambda chat, text, buttons=None: sent.append((chat, text))
    polling = DurablePolling(bot, workers=workers, max_pending=max_pending)
    return bot, polling, calls, sent


def wait(event):
    assert event.wait(5), 'worker did not reach controlled signal'


def test_blocked_user_fifo_independent_user_and_duplicate(tmp_path):
    entered, release, independent = (threading.Event() for _ in range(3))
    def handler(chat, value):
        if value == 'blocked':
            entered.set()
            wait(release)
        if chat == '202':
            independent.set()
        return {'text': value}
    bot, polling, calls, sent = harness(tmp_path, handler)
    polling.start()
    try:
        polling.accept(envelope(1, text='blocked'))
        wait(entered)
        polling.accept(envelope(2, text='second'))
        polling.accept(envelope(3, text='third'))
        polling.accept(envelope(4, '202', 'independent'))
        assert not polling.accept(envelope(4, '202', 'independent'))
        wait(independent)
        assert calls == [('101', 'blocked'), ('202', 'independent')]
        assert len(polling.threads) == 2
    finally:
        release.set()
        polling.close()
    assert [value for chat, value in calls if chat == '101'] == ['blocked', 'second', 'third']
    assert len(sent) == 4


def test_bounded_backlog_backpressures_acceptance_without_acknowledging(tmp_path):
    entered, release, accepted = (threading.Event() for _ in range(3))
    def handler(chat, value):
        if value == 'blocked':
            entered.set()
            wait(release)
        return {'text': value}
    bot, polling, calls, _ = harness(tmp_path, handler, max_pending=2)
    polling.start()
    polling.accept(envelope(1, text='blocked'))
    wait(entered)
    polling.accept(envelope(2, text='queued'))
    accepting = threading.Thread(target=lambda: (polling.accept(envelope(3, '202')), accepted.set()))
    accepting.start()
    try:
        assert not accepted.wait(0.05)
        assert polling.offset == 3
        assert len(json.loads(polling.path.read_text())['entries']) == 2
    finally:
        release.set()
        accepting.join(5)
        polling.close()
    assert accepted.is_set()


def test_queued_restart_and_denied_offset_are_durable(tmp_path):
    bot, polling, calls, sent = harness(tmp_path)
    assert polling.accept(envelope(1))
    assert not polling.accept(envelope(2, '303'))
    restarted = DurablePolling(bot, workers=2, max_pending=8)
    assert restarted.offset == 3
    assert not restarted.accept(envelope(1))
    restarted.start()
    restarted.close()
    assert calls == [('101', '/start')]
    assert not bot.invites.directory('303').exists()
    assert json.loads(restarted.path.read_text())['entries'] == []


@pytest.mark.parametrize('status', ['started', 'completed'])
def test_uncertain_restart_does_not_replay_and_notice_survives_failed_delivery(tmp_path, status):
    bot, polling, calls, sent = harness(tmp_path)
    polling.accept(envelope(1, data='confirm:old'))
    row = polling.state['entries'][0]
    row['status'] = status
    row['reply'] = {'text': 'old result', 'buttons': [[{'callback_data': 'old'}]]}
    atomic_json(polling.path, polling.state)
    deliveries = []
    def failure(chat, text, buttons=None):
        deliveries.append((text, buttons))
        raise RuntimeError('synthetic delivery failure')
    bot.send_message = failure
    restarted = DurablePolling(bot)
    restarted.start()
    restarted.close()
    assert calls == []
    assert deliveries == [(bot.RECOVERY, [])]
    notice = bot.invites.directory('101') / 'interrupted_update.json'
    assert notice.exists()
    # A new invitation must not remove visibility of uncertain old work.
    bot.invites.set_access('101', False)
    bot.invites.set_access('101', True)
    restarted = DurablePolling(bot)
    bot.send_message = lambda chat, text, buttons=None: sent.append((chat, text))
    restarted.accept(envelope(2, text='/weekly_preview'))
    restarted.start()
    restarted.close()
    assert calls == [('101', '/weekly_preview')]
    assert bot.RECOVERY in sent[0][1]
    assert not notice.exists()


def test_started_crash_before_last_update_never_replays_callback(tmp_path):
    bot, polling, calls, sent = harness(tmp_path)
    polling.accept(envelope(1, data='confirm:old'))
    polling.state['entries'][0]['status'] = 'started'
    atomic_json(polling.path, polling.state)
    assert not (bot.invites.directory('101') / 'last_update.json').exists()
    restarted = DurablePolling(bot)
    restarted.start()
    restarted.close()
    assert calls == []
    assert bot.RECOVERY in sent[0][1]
    assert not DurablePolling(bot).accept(envelope(1, data='confirm:old'))


def test_queued_generation_cannot_cross_reinvite_and_foreign_updates_are_denied(tmp_path):
    bot, polling, calls, _ = harness(tmp_path)
    polling.accept(envelope(1))
    bot.invites.set_access('101', False)
    bot.invites.set_access('101', True)
    for update in (envelope(2, sender='202'), envelope(3, kind='group'),
                   envelope(4, data='foreign', sender='202')):
        assert not polling.accept(update)
    polling.accept(envelope(5, '202', 'allowed'))
    polling.start()
    polling.close()
    assert calls == [('202', 'allowed')]
    assert not (bot.invites.directory('101') / 'last_update.json').exists()


def test_handler_and_delivery_errors_do_not_stop_other_users(tmp_path):
    def handler(chat, value):
        if chat == '101':
            raise ValueError('synthetic app failure')
        return {'text': 'success'}
    bot, polling, calls, sent = harness(tmp_path, handler)
    polling.accept(envelope(1))
    polling.accept(envelope(2, '202'))
    polling.start()
    polling.close()
    assert len(calls) == len(sent) == 2
    assert ('101', bot.FAILURE) in sent
    assert ('202', 'success') in sent


def test_revoke_and_shutdown_wait_for_active_write(tmp_path):
    from services.invited_beta_runtime import Invitations
    from scripts.closed_beta_bot import instance_lock
    entered, release, revoked, closed = (threading.Event() for _ in range(4))
    def handler(chat, value):
        entered.set()
        wait(release)
        return {'text': 'written'}
    bot, polling, calls, _ = harness(tmp_path, handler)
    def run():
        with instance_lock(tmp_path):
            polling.start()
            polling.accept(envelope(1))
            wait(entered)
            polling.close()
        closed.set()
    owner = threading.Thread(target=run)
    owner.start()
    wait(entered)
    revoker = threading.Thread(target=lambda: (Invitations(tmp_path).set_access('101', False), revoked.set()))
    revoker.start()
    try:
        assert not revoked.wait(0.05)
        assert not closed.is_set()
        with pytest.raises(Exception):
            with instance_lock(tmp_path):
                pytest.fail('instance lock was released during a write')
    finally:
        release.set()
        owner.join(5)
        revoker.join(5)
    assert closed.is_set() and revoked.is_set()
    assert bot.process_update(envelope(2)) is None


def test_acceptance_is_atomic_with_offset_and_save_failure_dispatches_nothing(tmp_path, monkeypatch):
    bot, polling, calls, _ = harness(tmp_path)
    original = polling.path.read_text()
    def failure(path, state):
        raise OSError('synthetic disk failure before replace')
    monkeypatch.setattr('services.invited_beta_runtime.atomic_json', failure)
    with pytest.raises(OSError):
        polling.accept(envelope(1))
    assert polling.path.read_text() == original
    assert calls == []
    assert polling.offset is None
    assert polling.state['entries'] == []


def test_partial_calendar_write_restart_requires_fresh_preview_and_has_no_duplicates(tmp_path):
    bot, calendars, _ = setup(tmp_path)
    bot.invites.set_access('101', True)
    onboard(bot)
    preview = bot.process_update(envelope(101, text='/weekly_preview'))
    calendars['101'].fail_write_number = 2
    polling = DurablePolling(bot)
    update = envelope(102, data=confirmation(preview))
    polling.accept(update)
    polling.state['entries'][0]['status'] = 'started'
    atomic_json(polling.path, polling.state)
    # Domain writes partially, then process disappears before transport completion.
    assert 'не завершена' in bot.process_update(update)['text']
    writes = list(calendars['101'].writes)
    restarted = InvitedBot('synthetic', tmp_path, calendars.__getitem__, bot.application_factory)
    sent = []
    restarted.send_message = lambda *args: sent.append(args)
    transport = DurablePolling(restarted)
    transport.start()
    transport.close()
    assert calendars['101'].writes == writes
    assert restarted.RECOVERY in sent[0][1]
    calendars['101'].fail_write_number = None
    fresh = restarted.process_update(envelope(103, text='/weekly_preview'))
    restarted.process_update(envelope(104, data=confirmation(fresh)))
    uids = [event['iCalUID'] for event in calendars['101'].remote.values()]
    assert len(uids) == len(set(uids))
    assert len([row for row in calendars['101'].writes if row == ('insert', uids[0])]) == 1


def test_polling_accepts_second_page_while_first_user_blocked(tmp_path, monkeypatch):
    entered, release, independent = (threading.Event() for _ in range(3))
    def handler(chat, value):
        if chat == '101':
            entered.set()
            wait(release)
        else:
            independent.set()
        return {'text': value}
    bot, _, calls, _ = harness(tmp_path, handler)
    requests = []
    def get(url, params, **kwargs):
        requests.append(params)
        if len(requests) == 1:
            result = [envelope(1)]
        elif len(requests) == 2:
            wait(entered)
            result = [envelope(2, '202')]
        else:
            wait(independent)
            release.set()
            raise KeyboardInterrupt()
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'ok': True, 'result': result})
    monkeypatch.setattr('services.invited_beta_runtime.requests.get', get)
    try:
        with pytest.raises(KeyboardInterrupt):
            bot.run_forever(workers=2, max_pending=4)
    finally:
        release.set()
    assert len(calls) == 2
    assert requests[1]['offset'] == 2
    assert all(1 <= row['limit'] <= 4 for row in requests)


@pytest.mark.parametrize('update', [None, [], {'update_id': 1, 'callback_query': 'bad'},
    {'update_id': 2, 'message': {'chat': 'bad'}},
    {'update_id': 3, 'message': {'chat': {'id': '101', 'type': 'private'}, 'from': []}}])
def test_malformed_updates_cannot_kill_polling(tmp_path, update):
    bot, polling, calls, _ = harness(tmp_path)
    assert bot.process_update(update) is None
    assert not polling.accept(update)
    polling.accept(envelope(4, '202'))
    polling.start()
    polling.close()
    assert calls == [('202', '/start')]


def test_worker_base_exception_retains_started_entry_and_stops_dispatch(tmp_path):
    crashed = threading.Event()
    def handler(chat, value):
        crashed.set()
        raise SystemExit('synthetic process interruption')
    bot, polling, calls, sent = harness(tmp_path, handler, workers=1)
    polling.accept(envelope(1))
    polling.accept(envelope(2, '202'))
    polling.start()
    wait(crashed)
    with pytest.raises(RuntimeError):
        polling.close()
    assert calls == [('101', '/start')]
    assert json.loads(polling.path.read_text())['entries'][0]['status'] == 'started'
    bot.application_factory = lambda *args: SimpleNamespace(handle_text=lambda *args: {'text': 'recovered'})
    restarted = DurablePolling(bot)
    restarted.start()
    restarted.close()
    assert any(bot.RECOVERY in row[1] for row in sent)


def test_second_interrupt_during_close_does_not_claim_worker_finished(tmp_path, monkeypatch):
    entered, release, closed = (threading.Event() for _ in range(3))
    def handler(chat, value):
        entered.set()
        wait(release)
        return {'text': 'done'}
    bot, polling, _, _ = harness(tmp_path, handler, workers=1)
    polling.start()
    polling.accept(envelope(1))
    wait(entered)
    real_wait = polling.finished[0].wait
    interrupted = threading.Event()
    def interrupt_once(timeout=None):
        if not interrupted.is_set():
            interrupted.set()
            raise KeyboardInterrupt()
        return real_wait(timeout)
    monkeypatch.setattr(polling.finished[0], 'wait', interrupt_once)
    closer = threading.Thread(target=lambda: (polling.close(), closed.set()))
    closer.start()
    wait(interrupted)
    try:
        assert not closed.wait(0.05)
    finally:
        release.set()
        closer.join(5)
    assert closed.is_set()


def test_concurrent_transport_duplicate_callback_cannot_repeat_calendar_writes(tmp_path):
    bot, calendars, _ = setup(tmp_path)
    bot.invites.set_access('101', True)
    onboard(bot)
    preview = bot.process_update(envelope(101, text='/weekly_preview'))
    sent = []
    bot.send_message = lambda *args: sent.append(args)
    polling = DurablePolling(bot)
    first = envelope(102, data=confirmation(preview))
    polling.accept(first)
    assert not polling.accept(first)
    polling.accept(envelope(103, data=confirmation(preview)))
    polling.start()
    polling.close()
    uids = [event['iCalUID'] for event in calendars['101'].remote.values()]
    assert uids and len(uids) == len(set(uids))
    assert all(calendars['101'].writes.count(('insert', uid)) == 1 for uid in uids)
    assert len(sent) == 2
    assert calendars['202'].writes == []


def test_interrupt_after_thread_start_holds_instance_lock_until_worker_finishes(tmp_path, monkeypatch):
    from scripts.closed_beta_bot import instance_lock
    entered, release, closed = (threading.Event() for _ in range(3))
    def handler(chat, value):
        entered.set()
        wait(release)
        return {'text': 'done'}
    bot, polling, calls, _ = harness(tmp_path, handler, workers=1)
    polling.accept(envelope(1))
    original_start = threading.Thread.start
    def interrupt_after_start(thread):
        original_start(thread)
        if thread.name.startswith('invited-beta-'):
            wait(entered)
            raise KeyboardInterrupt()
    monkeypatch.setattr(threading.Thread, 'start', interrupt_after_start)
    def run():
        with instance_lock(tmp_path):
            with pytest.raises(KeyboardInterrupt):
                bot.run_forever(workers=1)
        closed.set()
    owner = threading.Thread(target=run)
    owner.start()
    wait(entered)
    try:
        assert not closed.wait(0.05)
        with pytest.raises(ValueError, match='Another process'):
            with instance_lock(tmp_path):
                pytest.fail('lock released while started worker can still write')
    finally:
        release.set()
        owner.join(5)
    assert closed.is_set()
    assert calls == [('101', '/start')]


def test_failure_before_native_start_does_not_wait_for_nonexistent_worker(tmp_path, monkeypatch):
    bot, polling, _, _ = harness(tmp_path, workers=1)
    polling.accept(envelope(1))
    def fail_before_start(thread):
        raise RuntimeError('synthetic native startup failure')
    monkeypatch.setattr(threading.Thread, 'start', fail_before_start)
    with pytest.raises(RuntimeError, match='native startup failure'):
        polling.start()
    assert polling.finished == []
    assert polling.threads == []
    polling.close()
    assert json.loads(polling.path.read_text())['entries'][0]['status'] == 'queued'
