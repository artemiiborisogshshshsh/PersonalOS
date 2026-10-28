"""Real Telegram access boundaries, synthetic Google/Telegram only."""
from datetime import datetime, timedelta
import json
from unittest.mock import Mock

import pytest

from services.access_billing import AccessBillingStore
from services.closed_beta_runtime import PilotBot
from services.published_plan_updates import PlanConflict
from tests.test_closed_beta_runtime import application, onboard, confirmation, NOW
from tests.test_invited_beta import setup as invited_setup, envelope
from tests.test_published_plan_deletions import setup as published_setup


def publish(app):
    onboard(app)
    result = app.handle_callback('101', confirmation(app.handle_text('101', '/weekly_preview')))
    assert result['text'].startswith('План опубликован.')


def expire(app):
    app.now = lambda: NOW + timedelta(days=21)
    assert not app.access.allowed()


def renew(app):
    quote = app.access.store.quote(app.account.id, 10000, 'RUB', 30, now=app.now())
    app.access.store.record_verified_payment(app.account.id, provider='test-trusted', payment_id='paid-once',
        quote_id=quote['quote_id'], amount_minor=quote['amount_minor'], currency='RUB', now=app.now())


def test_status_does_not_start_trial_and_first_success_does(tmp_path):
    app = application(tmp_path)
    status = app.handle_text('101', '/subscription')['text']
    assert '21 день' in status and 'Пока не начат' in status
    assert not app.access.store.status(app.account.id)['trial_started']
    publish(app)
    status = app.access.store.status(app.account.id, now=NOW)
    assert datetime.fromisoformat(status['trial_started_at']) == NOW
    assert datetime.fromisoformat(status['trial_ends_at']) == NOW + timedelta(days=21)
    app.now = lambda: NOW + timedelta(days=20, hours=23, minutes=59)
    assert app.access.allowed()
    expire(app)
    assert not app.access.allowed()


def test_failed_google_publication_does_not_start_trial(tmp_path):
    app = application(tmp_path)
    onboard(app)
    preview = app.handle_text('101', '/weekly_preview')
    app.adapter.fail_write_number = 1
    result = app.handle_callback('101', confirmation(preview))
    assert 'не завершена' in result['text']
    assert not app.access.store.status(app.account.id, now=NOW)['trial_started']
    assert any(op.projection_pending for op in app.operations.load_all().values())


def test_existing_beta_gets_full_trial_on_first_observation_not_old_publication(tmp_path):
    app = application(tmp_path)
    publish(app)
    # Import a legacy plan without its newly introduced billing ledger.
    fresh = AccessBillingStore(tmp_path / 'imported.sqlite3')
    app.configure_access(fresh)
    app.now = lambda: NOW + timedelta(days=100)
    status = app.access.status()
    assert status['access_allowed']
    assert datetime.fromisoformat(status['trial_started_at']) == app.now()
    assert datetime.fromisoformat(status['trial_ends_at']) == app.now() + timedelta(days=21)


def test_confirmed_local_draft_without_remote_checkpoints_is_not_publication(tmp_path):
    app = application(tmp_path)
    onboard(app)
    app.handle_text('101', '/weekly_preview')
    operation = app.pending[2]
    operation.status = 'confirmed'
    operation.projection_pending = False
    app.operations.save(operation)
    assert not app.access.status()['trial_started']


def test_expired_all_mutations_stop_before_sources_providers_or_learning(tmp_path, monkeypatch):
    app, calendar, _, _ = published_setup(tmp_path)
    expire(app)
    journal = app.operations.path.read_bytes()
    provider = Mock(side_effect=AssertionError('provider must not be touched'))
    monkeypatch.setattr(app, '_inputs', provider)
    monkeypatch.setattr(app.image_reader, 'extract', provider)
    monkeypatch.setattr(app.learning, 'handle_attachment', provider)
    monkeypatch.setattr(app.learning, 'handle_callback', provider)
    for text in ['/weekly_preview', '/task new', '/done old', '/sleep 22:00 06:00',
                 '/stop_image_repeats', '/exclude_subject ОС', '/import_schedule', 'задали номера 1–5']:
        assert 'Данные сохранены' in app.handle_text('101', text)['text']
    for data in ['pilot:apply:stale', 'learn:old', 'img:confirm:old', 'nt:done:old', 'att:reset']:
        assert 'Данные сохранены' in app.handle_callback('101', data)['text']
    assert 'Данные сохранены' in app.handle_image('101', b'private-photo')['text']
    assert 'Данные сохранены' in app.handle_attachment('101', {'photo': []})['text']
    assert 'Данные сохранены' in app.apply('stale')['text']
    assert 'Данные сохранены' in app.sync_learning()['text']
    for invoke in [app.updates.sync_learning, app.updates.sync_source_cancellations,
                   lambda: app.updates.apply((None,)), lambda: app.updates.apply_deletion((None,)),
                   lambda: app.updates.accept_calendar((None,))]:
        with pytest.raises(PlanConflict):
            invoke()
    assert app.operations.path.read_bytes() == journal
    provider.assert_not_called()


def test_expired_saved_views_and_safe_export_are_local_and_complete(tmp_path, monkeypatch):
    app, _, _, _ = published_setup(tmp_path)
    app.learning.refresh()
    lesson = app.learning.store.lessons()[0]
    app.learning.store.add_material(lesson['id'], text='Номера 15–20', file_id='private-file', file_type='document')
    raw = json.loads(app.learning.store.path.read_text())
    secret = raw['secret']
    expire(app)
    monkeypatch.setattr(app, '_connected', Mock(side_effect=AssertionError('no provider')))
    for command in ['/saved_schedule', '/schedule', '/tasks', '/materials', '/image_status', '/help', '/privacy']:
        result = app.handle_text('101', command)
        assert result['text'] and not result['buttons']
    response = app.handle_text('101', '/export_data')['text']
    payload = json.loads(response.partition('\n')[2])
    assert secret not in response
    assert payload['files']['learning_lifecycle.json']['lessons'][lesson['id']]['materials'][0]['file_id'] == 'private-file'
    assert 'planning_profile.json' in payload['files']
    assert 'draft_operations.json' in payload['files']
    assert 'access_runtime.json' in payload['files']
    assert json.loads(app.learning.store.path.read_text())['secret'] == secret
    assert app.handle_text('202', '/export_data') is None


def test_invited_attachment_blocks_before_telegram_download_and_pilot_too(tmp_path, monkeypatch):
    bot, calendars, _ = invited_setup(tmp_path)
    bot.invites.set_access('101', True)
    bot.process_update(envelope(1))
    app = bot.applications['101'][1]
    app.access.store.start_trial(app.account.id, now=NOW)
    expire(app)
    download = Mock(side_effect=AssertionError('must not download'))
    monkeypatch.setattr('services.invited_beta_runtime.download_telegram_image', download)
    monkeypatch.setattr('services.closed_beta_runtime.download_telegram_image', download)
    update = envelope(2)
    update['message'].update(photo=[{'file_id': 'private'}], caption='расписание')
    assert 'Данные сохранены' in bot.process_update(update)['text']
    assert 'Данные сохранены' in PilotBot('synthetic', '101', app).handle_image('101', update['message'])['text']
    download.assert_not_called()
    assert not calendars['101'].writes


def test_referrals_shared_and_invitation_gate_remains_independent(tmp_path):
    bot, _, _ = invited_setup(tmp_path)
    bot.invites.set_access('101', True)
    code_reply = bot.process_update(envelope(1, text='/referral'))['text']
    code = code_reply.split('Твой код: ', 1)[1].splitlines()[0]
    assert '25%' in code_reply and '10%' in code_reply and '50%' in code_reply
    assert 'оператора' in code_reply
    assert bot.process_update(envelope(2, '202', '/start ref_' + code)) is None
    bot.invites.set_access('202', True)
    assert 'сохранено' in bot.process_update(envelope(3, '202', '/start ref_' + code))['text']
    app = bot.applications['202'][1]
    assert app.access.store.status(app.account.id)['referral_bound']
    assert not app.access.store.status(app.account.id)['trial_started']
    assert bot.applications['101'][1].access.store is app.access.store
    assert 'не принято' in bot.process_update(envelope(4, '202', '/referral ' + code))['text']
    assert 'не принято' in bot.process_update(envelope(5, '101', '/referral ' + code))['text']


def test_warnings_durable_deduplicated_and_failed_send_retries(tmp_path):
    app = application(tmp_path)
    publish(app)
    send = Mock()
    for day in [18, 20, 21]:
        app.now = lambda day=day: NOW + timedelta(days=day)
        app.access.background_allowed(send)
        app.access.background_allowed(send)
    assert send.call_count == 3
    restarted = application(tmp_path, adapter=app.adapter)
    restarted.now = app.now
    restarted.access.background_allowed(send)
    assert send.call_count == 3
    restarted.access._save({})
    send.side_effect = RuntimeError('delivery failed')
    restarted.access.background_allowed(send)
    send.side_effect = None
    restarted.access.background_allowed(send)
    assert send.call_count == 5


def test_expired_background_keeps_pending_journal_and_does_no_provider_work(tmp_path, monkeypatch):
    app, _, _, _ = published_setup(tmp_path)
    operation = next(iter(app.operations.load_all().values()))
    operation.pending_plan_update = {'kind': 'learning', 'checkpoint': 'keep-me'}
    operation.projection_pending = True
    app.operations.save(operation)
    before = app.operations.path.read_bytes()
    expire(app)
    refresh = Mock(side_effect=AssertionError('no work'))
    monkeypatch.setattr(app.learning, 'refresh', refresh)
    assert app.background_tick(Mock(), Mock(), Mock()) == []
    assert before == app.operations.path.read_bytes()
    refresh.assert_not_called()


def test_renewal_requires_fresh_confirm_even_unchanged_and_suppresses_overdue(tmp_path, monkeypatch):
    app, _, _, _ = published_setup(tmp_path)
    expire(app)
    app.learning.refresh()
    lesson = app.learning.store.lessons()[0]
    now = app.now()
    app.learning.store.register_preparation_blocks([{'id': 'overdue', 'lesson_id': lesson['id'],
        'start': (now - timedelta(hours=2)).isoformat(), 'end': (now - timedelta(hours=1)).isoformat()}])
    renew(app)
    assert app.access.allowed()
    assert not app.access.background_allowed(Mock())
    assert 'новый /weekly_preview' in app.apply('stale')['text']
    monkeypatch.setattr(app, '_preview_permitted', lambda: app.reply('Этот план уже опубликован. Изменений и повторных записей нет.'))
    fresh = app.preview()
    assert fresh['buttons']
    assert not app.access.background_allowed(Mock())
    token = confirmation(fresh).removeprefix('pilot:apply:')
    assert 'возобновлены' in app.apply(token)['text']
    assert app.access.background_allowed(Mock())
    due = app.learning.store.due_notifications(now)
    assert not any(row['kind'] == 'preparation_end' for row in due)
    restarted = application(tmp_path, adapter=app.adapter)
    restarted.now = app.now
    assert restarted.access.background_allowed(Mock())


def test_real_pending_learning_journal_resume_requires_fresh_preview_and_confirm(tmp_path):
    from tests.test_learning_calendar import setup, prepare_homework, operation
    from tests.test_published_plan_updates import ProcessCrash
    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app)
    app.learning.store.set_homework_duration(lesson_id, 120)
    save = app.operations.save
    def persist_then_crash(current):
        save(current)
        if current.pending_plan_update.get('kind') == 'learning':
            raise ProcessCrash()
    app.operations.save = persist_then_crash
    with pytest.raises(ProcessCrash):
        app.sync_learning()
    app.operations.save = save
    payload = operation(app).pending_plan_update
    app.configure_access(AccessBillingStore(tmp_path / 'trial-import.sqlite3'))
    app.access.store.start_trial(app.account.id, now=NOW - timedelta(days=21))
    assert not app.access.allowed()
    writes = list(calendar.writes)
    before = app.operations.path.read_bytes()
    renew(app)
    assert app.background_tick(Mock(), Mock(), Mock()) == []
    assert app.operations.path.read_bytes() == before
    preview = app.handle_text('101', '/weekly_preview')
    assert 'Сохранённое обновление материалов' in preview['text']
    assert preview['buttons']
    assert calendar.writes == writes
    assert operation(app).pending_plan_update == payload
    result = app.handle_callback('101', confirmation(preview))
    assert result['text'].startswith('Сохранённое обновление материалов проверено.'), result
    assert not operation(app).pending_plan_update
    assert not operation(app).projection_pending
    assert not app.access._state().get('resume_required')
    assert len(calendar.writes) > len(writes)


def test_pending_learning_confirmation_cannot_reinterpret_changed_journal(tmp_path, monkeypatch):
    from tests.test_learning_calendar import setup, prepare_homework, operation
    from tests.test_published_plan_updates import ProcessCrash
    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app)
    app.learning.store.set_homework_duration(lesson_id, 120)
    save = app.operations.save
    def persist_then_crash(current):
        save(current)
        if current.pending_plan_update.get('kind') == 'learning':
            raise ProcessCrash()
    monkeypatch.setattr(app.operations, 'save', persist_then_crash)
    with pytest.raises(ProcessCrash):
        app.sync_learning()
    monkeypatch.setattr(app.operations, 'save', save)
    app.configure_access(AccessBillingStore(tmp_path / 'trial-import.sqlite3'))
    app.access.store.start_trial(app.account.id, now=NOW - timedelta(days=21))
    assert not app.access.allowed()
    renew(app)
    preview = app.handle_text('101', '/weekly_preview')
    current = operation(app)
    current.pending_plan_update['planning_issue'] = 'operator edited journal'
    app.operations.save(current)
    writes = list(calendar.writes)
    result = app.handle_callback('101', confirmation(preview))
    assert 'журнал изменился' in result['text']
    assert app.access._state()['resume_required']
    assert calendar.writes == writes


def test_renewal_allows_explicit_missing_review_then_requires_final_plan_confirmation(tmp_path):
    from tests.test_published_plan_deletions import OTHER
    app, calendar, content, _ = published_setup(tmp_path)
    app.configure_access(AccessBillingStore(tmp_path / 'trial-import.sqlite3'))
    app.access.store.start_trial(app.account.id, now=NOW - timedelta(days=21))
    assert not app.access.allowed()
    renew(app)
    content['ics'] = OTHER
    app.handle_text('101', '/weekly_preview')
    review = app.handle_text('101', '/review_missing')
    assert review['buttons']
    result = app.handle_callback('101', confirmation(review))
    assert result['text'].startswith('Удаление проверено.'), result
    assert app.access._state()['resume_required']
    assert not app.access.background_allowed(Mock())
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    result = app.handle_callback('101', confirmation(preview))
    assert 'обновлён и проверен' in result['text'] or 'возобновлены' in result['text'], result
    assert not app.access._state().get('resume_required')


def test_invited_export_is_delivered_full_and_sanitized_through_durable_transport(tmp_path, monkeypatch):
    from services.invited_beta_runtime import DurablePolling
    bot, _, _ = invited_setup(tmp_path)
    bot.invites.set_access('101', True)
    bot.process_update(envelope(1))
    app = bot.applications['101'][1]
    (app.directory / 'shared_preparation.json').write_text(json.dumps({
        'notes': 'private-material-' * 1500, 'secret': 'never-export-this'}))
    payloads = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'ok': True, 'result': {'message_id': len(payloads)}}
    def post(url, **kwargs):
        payloads.append(kwargs['json'])
        return Response()
    monkeypatch.setattr('services.telegram_schedule_bot.requests.post', post)
    polling = DurablePolling(bot, workers=1, max_pending=2)
    assert polling.accept(envelope(2, text='/export_data'))
    polling.start()
    polling.close()
    assert len(payloads) > 1
    assert all(row['chat_id'] == '101' and len(row['text']) <= 4096 for row in payloads)
    exported = ''.join(row['text'] for row in payloads)
    assert 'never-export-this' not in exported
    document = json.loads(exported.partition('\n')[2])
    assert document['files']['shared_preparation.json']['notes'] == 'private-material-' * 1500
    assert not DurablePolling(bot).state['entries']


def test_claimed_overdue_reminder_is_suppressed_after_lease_expires_on_resumed_access(tmp_path, monkeypatch):
    app, _, _, _ = published_setup(tmp_path)
    expire(app)
    app.learning.refresh()
    lesson = app.learning.store.lessons()[0]
    now = app.now()
    app.learning.store.register_preparation_blocks([{'id': 'claimed-overdue', 'lesson_id': lesson['id'],
        'start': (now - timedelta(hours=2)).isoformat(), 'end': (now - timedelta(hours=1)).isoformat()}])
    due = app.learning.store.due_notifications(now)
    old = next(row for row in due if row['kind'] == 'preparation_end')
    assert app.learning.store.claim_notification(old['id'], now)
    renew(app)
    monkeypatch.setattr(app, '_preview_permitted', lambda: app.reply('Этот план уже опубликован.'))
    token = confirmation(app.preview()).removeprefix('pilot:apply:')
    assert 'возобновлены' in app.apply(token)['text']
    # The active lease hid the old candidate during the one-time cleanup.
    app.now = lambda: now + timedelta(minutes=6)
    monkeypatch.setattr(app.learning, 'refresh', lambda: None)
    send = Mock()
    assert app.learning.deliver_due(send, Mock(), Mock()) == []
    send.assert_not_called()
    assert not app.learning.store.due_notifications(app.now())


def test_expired_invited_preview_never_claims_to_check_google(tmp_path):
    bot, _, _ = invited_setup(tmp_path)
    bot.invites.set_access('101', True)
    bot.process_update(envelope(1))
    app = bot.applications['101'][1]
    app.access.store.start_trial(app.account.id, now=NOW)
    expire(app)
    send = Mock()
    bot.send_message = send
    assert 'Данные сохранены' in bot.process_update(envelope(2, text='/weekly_preview'))['text']
    assert 'Данные сохранены' in bot.process_update(envelope(3, data='nt:plan'))['text']
    send.assert_not_called()
