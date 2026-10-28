"""Public registration is an explicit /start admission, never a revocation bypass."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from services.invited_beta_runtime import DurablePolling, InvitedBot
from tests.test_invited_beta import envelope


def public_bot(tmp_path, *, enabled=True):
    calls = []
    def factory(directory, chat, adapter):
        def handle(_, text):
            calls.append((chat, text))
            return {'text': 'Начнём настройку'}
        return SimpleNamespace(handle_text=handle, handle_callback=handle)
    bot = InvitedBot('synthetic', tmp_path, lambda chat: chat, factory,
                     open_registration=enabled)
    bot.send_message = lambda *args, **kwargs: None
    return bot, calls


def test_start_registers_once_isolated_and_survives_restart(tmp_path):
    bot, calls = public_bot(tmp_path)
    assert bot.process_update(envelope(1))['text'] == 'Начнём настройку'
    first = bot.invites.read('101')
    assert first['enabled'] is True and first['generation']
    assert bot.process_update(envelope(1)) is None
    bot.process_update(envelope(2, text='/start referral_payload'))
    assert bot.invites.read('101') == first
    bot.process_update(envelope(3, chat='202'))
    assert bot.invites.read('202')['generation'] != first['generation']
    assert bot.applications['101'][1] is not bot.applications['202'][1]
    restarted, new_calls = public_bot(tmp_path)
    assert restarted.process_update(envelope(2)) is None
    assert restarted.invites.read('101') == first
    assert new_calls == []
    assert calls == [('101', '/start'), ('101', '/start referral_payload'), ('202', '/start')]


@pytest.mark.parametrize('update', [
    envelope(1, text='/weekly_preview'), envelope(1, text='/connect_google'),
    envelope(1, text='/starter'), envelope(1, text=''),
    envelope(1, data='/start'), envelope(1, data='confirm:any'),
    envelope(1, sender='202'), envelope(1, kind='group'),
    {'update_id': True, 'message': envelope(1)['message']},
    {'update_id': -1, 'message': envelope(1)['message']},
])
def test_only_valid_private_start_creates_account(tmp_path, update):
    bot, calls = public_bot(tmp_path)
    assert bot.process_update(update) is None
    assert not list(tmp_path.iterdir())
    assert calls == []


def test_default_closed_mode_stays_closed(tmp_path):
    bot = InvitedBot('synthetic', tmp_path, lambda chat: pytest.fail('No provider'))
    assert bot.process_update(envelope(1)) is None
    assert not list(tmp_path.iterdir())


def test_revoked_start_never_reopens_account(tmp_path):
    bot, calls = public_bot(tmp_path)
    bot.process_update(envelope(1))
    bot.invites.set_access('101', False)
    revoked = bot.invites.read('101')
    assert bot.process_update(envelope(2)) is None
    assert bot.invites.read('101') == revoked
    assert calls == [('101', '/start')]


def test_simultaneous_first_starts_share_one_generation(tmp_path):
    bot, calls = public_bot(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as workers:
        replies = list(workers.map(bot.process_update, [envelope(1), envelope(1)]))
    assert sum(reply is not None for reply in replies) == 1
    assert calls == [('101', '/start')]


def test_durable_queue_registers_before_generation_binding_and_replays_once(tmp_path):
    bot, calls = public_bot(tmp_path)
    polling = DurablePolling(bot)
    assert polling.accept(envelope(1))
    generation = bot.invites.read('101')['generation']
    assert polling.state['entries'][0]['generation'] == generation
    assert calls == []
    restarted = DurablePolling(bot)
    assert not restarted.accept(envelope(1))
    restarted.start()
    restarted.close()
    assert calls == [('101', '/start')]
    assert bot.invites.read('101')['generation'] == generation


def test_durable_queue_rejects_stale_unknown_and_nonstart_without_registration(tmp_path):
    bot, calls = public_bot(tmp_path)
    polling = DurablePolling(bot)
    assert not polling.accept(envelope(5, text='/help'))
    assert not polling.accept(envelope(4, chat='202'))
    assert not bot.invites.directory('101').exists()
    assert not bot.invites.directory('202').exists()
    assert calls == []


def test_queued_start_revoked_before_execution_cannot_register_again(tmp_path):
    bot, calls = public_bot(tmp_path)
    polling = DurablePolling(bot)
    assert polling.accept(envelope(1))
    bot.invites.set_access('101', False)
    revoked = bot.invites.read('101')
    assert not polling.accept(envelope(2))
    restarted = DurablePolling(bot)
    restarted.start()
    restarted.close()
    assert calls == []
    assert bot.invites.read('101') == revoked
