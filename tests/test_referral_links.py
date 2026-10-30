"""Personal referral links at the real, isolated Telegram application boundary."""
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_invited_beta import setup, envelope
from tests.test_closed_beta_runtime import application, NOW
from tests.test_access_runtime import renew


def bot_with_identity(tmp_path, *, opened=True):
    bot, _, _ = setup(tmp_path)
    bot.open_registration = opened
    identity = Mock(return_value={'username': 'ActualCalendar_bot'})
    bot._bot_identity_provider = identity
    return bot, identity


def inviter_code(bot):
    response = bot.process_update(envelope(1, text='/start'))
    assert response['step'] == 'source'
    return bot.applications['101'][1].access.store.referral_code(bot.applications['101'][1].account.id)


def test_link_share_actual_cached_username_and_legacy_code(tmp_path):
    bot, identity = bot_with_identity(tmp_path)
    code = inviter_code(bot)
    app = bot.applications['101'][1]
    for number in (2, 3):
        reply = bot.process_update(envelope(number, text='/referral'))
        assert f'Твой код: {code}' in reply['text']
        link = f'https://t.me/ActualCalendar_bot?start=ref_{code}'
        assert link in reply['text'] and f'/referral {code}' in reply['text']
        button = reply['buttons'][0][0]
        assert button['text'] == 'Поделиться ссылкой'
        query = parse_qs(urlsplit(button['url']).query)
        assert query['url'] == [link]
        assert 'Одногруппники' in query['text'][0]
        assert 'Приглашено студентов: 0' in reply['text']
        assert all(term in reply['text'] for term in ['25%', '10%', '50%', 'не настроена'])
    identity.assert_called_once_with()
    assert app.access.store.referral_code(app.account.id) == code


def test_open_deep_start_binds_and_continues_same_onboarding(tmp_path):
    bot, identity = bot_with_identity(tmp_path)
    code = inviter_code(bot)
    reply = bot.process_update(envelope(2, '202', '/start ref_' + code))
    assert reply['step'] == 'source' and 'Приглашение сохранено' in reply['text']
    assert reply['buttons'] == bot.applications['202'][1].handler.onboarding.start()['buttons']
    app = bot.applications['202'][1]
    assert app.access.store.status(app.account.id)['referral_bound']
    assert app.access.store.referral_statistics(bot.applications['101'][1].account.id) == {'invited': 1, 'published': 0}
    identity.assert_not_called()


@pytest.mark.parametrize('kind', ['bad', 'empty', 'self', 'repeat', 'published', 'paid'])
def test_invalid_binding_is_friendly_and_continues(tmp_path, kind):
    bot, _ = bot_with_identity(tmp_path)
    code = inviter_code(bot)
    bot.process_update(envelope(2, '202', '/start'))
    app = bot.applications['202'][1]
    chat = '202'
    if kind == 'bad':
        code = 'does-not-exist'
    elif kind == 'empty':
        code = ''
    elif kind == 'self':
        chat = '101'
    elif kind == 'repeat':
        app.access.store.bind_referral(app.account.id, code, now=NOW)
    elif kind == 'published':
        app.access.store.start_trial(app.account.id, now=NOW)
    elif kind == 'paid':
        renew(app)
    reply = bot.process_update(envelope(3, chat, '/start ref_' + code))
    assert 'Приглашение не принято' in reply['text']
    assert reply['step'] == 'source'
    assert 'Можно продолжить' in reply['text']


def test_closed_gate_never_admits_deep_start(tmp_path):
    bot, identity = bot_with_identity(tmp_path, opened=False)
    bot.invites.set_access('101', True)
    code = inviter_code(bot)
    assert bot.process_update(envelope(2, '202', '/start ref_' + code)) is None
    assert not bot.invites.directory('202').exists()
    identity.assert_not_called()


def test_standalone_unknown_name_manual_code_without_guessed_link(tmp_path):
    app = application(tmp_path)
    reply = app.handle_text('101', '/referral')
    assert 'Имя бота пока недоступно' in reply['text']
    assert '/referral ' in reply['text']
    assert 'https://t.me/' not in reply['text'] and not reply['buttons']


def test_getme_trusted_response_cached_across_calls(tmp_path, monkeypatch):
    bot, _ = bot_with_identity(tmp_path)
    bot.token = '12345:synthetic'
    bot._bot_identity_provider = bot._fetch_bot_identity
    response = Mock()
    response.json.return_value = {'ok': True, 'result': {'username': 'ProductionCalendar_bot'}}
    get = Mock(return_value=response)
    monkeypatch.setattr('services.invited_beta_runtime.requests.get', get)
    assert bot.bot_username() == 'ProductionCalendar_bot'
    assert bot.bot_username() == 'ProductionCalendar_bot'
    get.assert_called_once_with(bot.base_url + '/getMe', timeout=5)


def test_failed_getme_falls_back_once(tmp_path):
    bot, identity = bot_with_identity(tmp_path)
    identity.return_value = {'username': 'invalid/name'}
    inviter_code(bot)
    for number in (2, 3):
        reply = bot.process_update(envelope(number, text='/referral'))
        assert not reply['buttons'] and 'вручную' in reply['text']
    identity.assert_called_once_with()


def test_injected_provider_constructor_and_persisted_aggregate_reply(tmp_path):
    from services.invited_beta_runtime import InvitedBot
    from services.access_billing import AccessBillingStore
    base, _, _ = setup(tmp_path)
    identity = Mock(return_value={'username': 'RealUniversity_bot'})
    bot = InvitedBot('synthetic', tmp_path, base.adapter_factory, base.application_factory,
                     open_registration=True, bot_identity_provider=identity)
    code = inviter_code(bot)
    bot.process_update(envelope(2, '202', '/start ref_' + code))
    invitee = bot.applications['202'][1]
    for _ in range(2):
        invitee.access.store.start_trial(invitee.account.id, now=NOW)
    bot.process_update(envelope(3, '202', '/start ref_' + code))
    reply = bot.process_update(envelope(4, text='/referral'))
    assert 'Приглашено студентов: 1' in reply['text']
    assert 'Опубликовали первый план: 1' in reply['text']
    restarted = AccessBillingStore(tmp_path / 'access_billing.sqlite3')
    assert restarted.referral_statistics(bot.applications['101'][1].account.id) == {'invited': 1, 'published': 1}
    assert invitee.account.id not in reply['text']
    identity.assert_called_once_with()


def test_direct_default_runtime_never_needs_network_for_referral(tmp_path, monkeypatch):
    bot, _, _ = setup(tmp_path)
    bot.open_registration = True
    request = Mock(side_effect=AssertionError('direct use must remain offline'))
    monkeypatch.setattr('services.invited_beta_runtime.requests.get', request)
    inviter_code(bot)
    reply = bot.process_update(envelope(2, text='/referral'))
    assert not reply['buttons'] and 'Имя бота пока недоступно' in reply['text']
    request.assert_not_called()
