from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from services.adaptive_preparation_service import AdaptivePreparationService, DraftOperation, DraftPreparationBlock
from services.closed_beta_runtime import PilotBot
from tests.test_closed_beta_runtime import NOW, application, onboard, confirmation
from tests.test_invited_beta import envelope, setup


def callbacks(reply):
    return [button['callback_data'] for row in reply['buttons'] for button in row]


def choose(reply, prefix):
    return next(data for data in callbacks(reply) if data.startswith(prefix))


def test_initial_no_slot_shows_subject_reason_recovery_and_cannot_apply(tmp_path, monkeypatch):
    app = application(tmp_path)
    onboard(app)
    _, _, events, _, _ = app._inputs()
    operation = DraftOperation('blocked', [], no_slot_reasons={events[0].id: 'допустимое окно подготовки уже прошло'})
    monkeypatch.setattr(AdaptivePreparationService, 'build_draft', lambda *args, **kwargs: operation)
    reply = app.handle_text('101', '/weekly_preview')
    assert 'ОС' in reply['text'] and 'окно подготовки уже прошло' in reply['text']
    assert 'Публикация заблокирована' in reply['text']
    assert {'planpref:open:duration', 'planpref:open:limits', 'main:attendance', 'main:plan'} <= set(callbacks(reply))
    assert not any(data.startswith('pilot:apply:') for data in callbacks(reply))
    assert app.pending is None
    assert 'устарело' in app.handle_callback('101', 'pilot:apply:invented')['text']
    assert app.adapter.writes == []


def test_manual_conflict_has_reason_and_no_publication(tmp_path):
    app = application(tmp_path)
    block = DraftPreparationBlock('b', 'e', 'Алгебра', NOW, NOW + timedelta(hours=1), 60, '', manual_conflict=True)
    reply = app.plan_ui.render(DraftOperation('x', [block]), [])
    assert 'Алгебра' in reply['text'] and 'конфликт времени' in reply['text']
    assert not any(data.startswith('pilot:apply:') for data in callbacks(reply))
    assert app.adapter.writes == []


def test_preview_pages_bounded_keep_approval_and_expire(tmp_path):
    app = application(tmp_path)
    events = [SimpleNamespace(id=str(i), title='Длинный предмет ' * 30,
        start_time=NOW + timedelta(hours=i), end_time=NOW + timedelta(hours=i + 1)) for i in range(150)]
    reply = app.plan_ui.render(DraftOperation('x', []), events, approval='approval')
    assert len(reply['text'].encode()) < 4096
    page = app.handle_callback('101', choose(reply, 'planui:'))
    count = 0
    while True:
        count += 1
        assert len(page['text'].encode()) < 4096
        assert 'pilot:apply:approval' in callbacks(page)
        assert page['edit_message']
        next_data = next((b['callback_data'] for row in page['buttons'] for b in row if b['text'] == 'Далее →'), None)
        if next_data is None:
            break
        page = app.handle_callback('101', next_data)
    assert count > 2
    app.now = lambda: NOW + timedelta(minutes=11)
    assert 'устарел' in app.handle_callback('101', choose(reply, 'planui:'))['text']
    assert app.handle_callback('202', choose(reply, 'planui:')) is None
    assert app.adapter.writes == []


def test_duration_draft_save_invalidates_approval_and_requires_new_preview(tmp_path):
    app = application(tmp_path)
    onboard(app)
    approval = confirmation(app.handle_text('101', '/weekly_preview'))
    old = app.profile.load().profile.lecture_minutes
    menu = app.handle_callback('101', 'planpref:open:duration')
    choices = app.handle_callback('101', choose(menu, 'planpref:'))
    selected = app.handle_callback('101', next(data for data in callbacks(choices) if data.endswith(':30')))
    assert app.profile.load().profile.lecture_minutes == old
    saved = app.handle_callback('101', choose(selected, 'planpref:').split(':field:')[0] + ':save')
    assert app.profile.load().profile.lecture_minutes == 30
    assert 'Пересчитай план' in saved['text']
    assert 'устарело' in app.handle_callback('101', approval)['text']
    assert app.adapter.writes == []
    fresh = app.handle_callback('101', 'main:plan')
    assert any(data.startswith('pilot:apply:') for data in callbacks(fresh))
    assert app.adapter.writes == []


def test_settings_draft_stale_profile_and_zero_rejected(tmp_path):
    app = application(tmp_path)
    menu = app.handle_callback('101', 'planpref:open:limits')
    prefix = choose(menu, 'planpref:').split(':field:')[0]
    app.handle_callback('101', prefix + ':set:0:0')
    assert app.profile.load().profile.preparation_window_days == 7
    settings = app.profile.load()
    settings.profile = replace(settings.profile, travel_minutes_each_way=35)
    app.profile.save(settings)
    assert 'уже изменились' in app.handle_callback('101', prefix + ':save')['text']
    assert app.profile.load().profile.travel_minutes_each_way == 35


def test_menu_settings_show_current_values_and_source_link_has_priority(tmp_path):
    app = application(tmp_path)
    onboard(app)
    reply = app.handle_callback('101', 'main:settings')
    assert '23:00–06:40' in reply['text'] and '30 мин' in reply['text']
    app.learning.handle_text = Mock(side_effect=AssertionError('source intercepted by learning'))
    reply = app.handle_text('101', 'https://ro-rasp.tpu.ru/gruppa_999/2026/1/view.html')
    assert reply['text'] == 'Открой /start и продолжи настройку.'
    app.learning.handle_text.assert_not_called()


def test_google_button_reuses_authenticated_oauth_and_deduplicates(tmp_path):
    bot, calendars, _ = setup(tmp_path)
    bot.invites.set_access('101', True)
    bot.google_oauth = Mock()
    bot.google_oauth.create_link.return_value = {'text': 'secure link', 'buttons': []}
    update = envelope(1, data='main:connect_google')
    assert bot.process_update(update)['text'] == 'secure link'
    bot.google_oauth.create_link.assert_called_once_with('101', bot.invites.read('101')['generation'])
    assert bot.process_update(update) is None
    assert bot.process_update(envelope(2, data='main:connect_google', sender='202')) is None
    assert calendars['101'].writes == []


@pytest.mark.parametrize('outcome', ['success', 'not_modified', 'failure'])
def test_callback_edit_transport_falls_back_without_duplicate_actions(tmp_path, monkeypatch, outcome):
    app = application(tmp_path)
    bot = PilotBot('synthetic', '101', app)
    message = {'message_id': 42, 'chat': {'id': '101'}}
    response = Mock(status_code=400 if outcome != 'success' else 200)
    response.json.return_value = ({'ok': True, 'result': message} if outcome == 'success' else
        {'ok': False, 'description': 'Bad Request: message is not modified' if outcome == 'not_modified' else 'cannot edit'})
    if outcome == 'failure':
        response.raise_for_status.side_effect = requests.HTTPError('synthetic')
    post = Mock(return_value=response)
    monkeypatch.setattr('services.telegram_schedule_bot.requests.post', post)
    bot.send_message = Mock(return_value={'message_id': 43})
    reply = {'text': 'updated', 'buttons': [], 'edit_message': True}
    receipt = bot.deliver_reply('101', reply, message)
    assert post.call_args.args[0].endswith('/editMessageText')
    assert receipt['message_id'] == (43 if outcome == 'failure' else 42)
    assert bot.send_message.call_count == (1 if outcome == 'failure' else 0)
    post.reset_mock()
    bot.deliver_reply('101', reply, {'message_id': 42, 'chat': {'id': '202'}})
    post.assert_not_called()


def same_day_app(tmp_path):
    from tests.test_closed_beta_runtime import ICS
    from services.published_plan_updates import PublishedPlanUpdates
    content = {'ics': ICS.replace(b'20260924', b'20260922')}
    app = application(tmp_path, content=content)
    app.updates = PublishedPlanUpdates(app)
    onboard(app)
    return app, content


def acknowledge_omissions(app, reply):
    review_data = choose(reply, 'planomit:')
    review = app.handle_callback('101', review_data)
    return app.handle_callback('101', choose(review, 'planomit:'))


def test_expired_window_ack_then_separate_publication_preserves_class_and_history(tmp_path):
    from services.draft_operation_store import DraftOperationStore
    from services.published_plan_updates import PublishedPlanUpdates
    app, content = same_day_app(tmp_path)
    first = app.handle_text('101', '/weekly_preview')
    assert 'окно подготовки уже прошло' in first['text']
    assert not any(data.startswith('pilot:apply:') for data in callbacks(first))
    assert not app.operations.load_all()
    ready = acknowledge_omissions(app, first)
    assert 'С твоего согласия пропущено подготовок: 1' in ready['text']
    assert app.adapter.writes == [] and not app.operations.load_all()
    operation = app.pending[2]
    assert operation.no_slot_reasons
    assert not operation.completed_source_event_ids
    assert len(operation.omitted_preparations) == 1
    published = app.handle_callback('101', confirmation(ready))
    assert 'опубликован' in published['text']
    assert len(app.adapter.remote) == 1
    assert not operation.blocks
    assert all('Подготовка' not in row['summary'] for row in app.adapter.remote.values())
    saved = next(iter(app.operations.load_all().values()))
    assert saved.omitted_preparations == operation.omitted_preparations
    assert DraftOperationStore._deserialize(DraftOperationStore._serialize(saved)).omitted_preparations
    assert saved.no_slot_reasons and not saved.completed_source_event_ids
    restarted = application(tmp_path, app.adapter, content)
    restarted.updates = PublishedPlanUpdates(restarted)
    before = list(app.adapter.writes)
    assert 'уже опубликован' in restarted.handle_text('101', '/weekly_preview')['text']
    assert restarted.adapter.writes == before
    # A benign profile change rebuilds the published candidate while keeping exact omission history.
    settings = restarted.profile.load()
    settings.profile = replace(settings.profile, travel_minutes_each_way=40)
    restarted.profile.save(settings)
    update = restarted.handle_text('101', '/weekly_preview')
    assert any(data.startswith('pilot:apply:') for data in callbacks(update)), update
    candidate = restarted.pending[3]['operation']
    assert candidate['omitted_preparations'] == saved.omitted_preparations
    assert candidate['no_slot_reasons'] == saved.no_slot_reasons
    assert not candidate['completed_source_event_ids']
    assert restarted.adapter.writes == before


@pytest.mark.parametrize('mutation', ['profile', 'source', 'time', 'forged_ack', 'replay'])
def test_expired_omission_staleness_or_forgery_never_writes(tmp_path, mutation):
    app, content = same_day_app(tmp_path)
    first = app.handle_text('101', '/weekly_preview')
    review_data = choose(first, 'planomit:')
    if mutation == 'forged_ack':
        result = app.handle_callback('101', review_data.replace(':review', ':ack'))
        assert 'Сначала проверь' in result['text']
    else:
        review = app.handle_callback('101', review_data)
        ack_data = choose(review, 'planomit:')
        if mutation == 'profile':
            settings = app.profile.load()
            settings.profile = replace(settings.profile, travel_minutes_each_way=45)
            app.profile.save(settings)
        elif mutation == 'source':
            content['ics'] = content['ics'].replace(b'20260922T030000Z', b'20260922T040000Z').replace(b'20260922T043000Z', b'20260922T053000Z')
        elif mutation == 'time':
            app.now = lambda: NOW + timedelta(hours=2)
        elif mutation == 'replay':
            ready = app.handle_callback('101', ack_data)
            assert any(data.startswith('pilot:apply:') for data in callbacks(ready))
        result = app.handle_callback('101', ack_data)
        assert not any(data.startswith('pilot:apply:') for data in callbacks(result))
    assert app.adapter.writes == [] and not app.operations.load_all()


def test_mixed_no_slot_and_manual_conflicts_never_offer_omission(tmp_path, monkeypatch):
    app, _ = same_day_app(tmp_path)
    _, _, events, _, _ = app._inputs()
    operation = DraftOperation('bad', [], no_slot_reasons={events[0].id: 'допустимое окно подготовки уже прошло',
                                                        'other': 'нет свободного слота'})
    monkeypatch.setattr(AdaptivePreparationService, 'build_draft', lambda *args, **kwargs: operation)
    first = app.handle_text('101', '/weekly_preview')
    assert not any(data.startswith(('planomit:', 'pilot:apply:')) for data in callbacks(first))
    operation.no_slot_reasons.pop('other')
    operation.blocks.append(DraftPreparationBlock('b', events[0].id, 'ОС', NOW + timedelta(minutes=20),
        NOW + timedelta(minutes=50), 30, '', manual_conflict=True))
    first = app.handle_text('101', '/weekly_preview')
    assert not any(data.startswith(('planomit:', 'pilot:apply:')) for data in callbacks(first))
    assert app.adapter.writes == []


def test_expired_publication_crash_resume_preserves_omissions(tmp_path):
    from services.published_plan_updates import PublishedPlanUpdates
    app, content = same_day_app(tmp_path)
    ready = acknowledge_omissions(app, app.handle_text('101', '/weekly_preview'))
    original_sync = app.classes.sync_personal_event_to_calendar
    def interrupted_after_class(*args, **kwargs):
        original_sync(*args, **kwargs)
        return False
    app.classes.sync_personal_event_to_calendar = interrupted_after_class
    failed = app.handle_callback('101', confirmation(ready))
    assert 'не завершена' in failed['text']
    saved = next(iter(app.operations.load_all().values()))
    assert saved.projection_pending and saved.omitted_preparations and saved.no_slot_reasons
    restarted = application(tmp_path, app.adapter, content)
    restarted.updates = PublishedPlanUpdates(restarted)
    preview = restarted.handle_text('101', '/weekly_preview')
    done = restarted.handle_callback('101', confirmation(preview))
    assert 'опубликован' in done['text']
    assert len(app.adapter.remote) == 1
    assert next(iter(restarted.operations.load_all().values())).omitted_preparations


def test_ongoing_class_not_selected_cannot_block_new_future_plan(tmp_path):
    from tests.test_closed_beta_runtime import ICS
    ongoing = ICS.replace(b'20260924T030000Z', b'20260922T010000Z').replace(b'20260924T043000Z', b'20260922T033000Z')
    app = application(tmp_path, content={'ics': ongoing})
    onboard(app)
    reply = app.handle_text('101', '/weekly_preview')
    assert 'Нет выбранных занятий' in reply['text']
    assert 'окно подготовки уже прошло' not in reply['text']
    assert app.adapter.writes == []


def test_unfinished_journal_rejects_recovery_setting_save(tmp_path):
    app = application(tmp_path)
    onboard(app)
    menu = app.handle_callback('101', 'planpref:open:duration')
    save = choose(menu, 'planpref:').split(':field:')[0] + ':save'
    app.operations.save(DraftOperation('partial', [], projection_pending=True))
    old = app.profile.load()
    reply = app.handle_callback('101', save)
    assert 'незавершённую публикацию' in reply['text']
    assert app.profile.load() == old
    assert app.operations.load('partial').projection_pending
    assert app.adapter.writes == []


@pytest.mark.parametrize('mutation', ['profile', 'source', 'time', 'omission_record'])
def test_omission_final_approval_rechecks_data_and_exact_omission(tmp_path, mutation):
    app, content = same_day_app(tmp_path)
    ready = acknowledge_omissions(app, app.handle_text('101', '/weekly_preview'))
    if mutation == 'profile':
        settings = app.profile.load()
        settings.profile = replace(settings.profile, lecture_minutes=30)
        app.profile.save(settings)
    elif mutation == 'source':
        content['ics'] = content['ics'].replace(b'20260922T030000Z', b'20260922T040000Z').replace(b'20260922T043000Z', b'20260922T053000Z')
    elif mutation == 'time':
        app.now = lambda: NOW + timedelta(hours=2)
    else:
        next(iter(app.pending[2].omitted_preparations.values()))['start'] = NOW.isoformat()
    reply = app.handle_callback('101', confirmation(ready))
    assert 'опубликован' not in reply['text']
    assert app.adapter.writes == []


@pytest.mark.parametrize('edit_fails', [False, True])
def test_durable_edit_delivers_once_and_binds_actual_message_context(tmp_path, monkeypatch, edit_fails):
    from services.invited_beta_runtime import DurablePolling, InvitedBot
    handled = []
    learning = Mock()
    class App:
        def __init__(self, *args):
            self.learning = learning
        def handle_callback(self, chat, data):
            handled.append(data)
            return {'text': 'card', 'buttons': [], 'edit_message': True,
                    '_learning_lesson_id': 'lesson', '_learning_kind': 'material'}
    bot = InvitedBot('synthetic', tmp_path, lambda chat: None, application_factory=App)
    bot.invites.set_access('101', True)
    message = {'message_id': 42, 'chat': {'id': '101', 'type': 'private'}}
    response = Mock(status_code=200)
    response.json.return_value = {'ok': True, 'result': message}
    post = Mock(side_effect=requests.ConnectionError('synthetic') if edit_fails else None, return_value=response)
    monkeypatch.setattr('services.telegram_schedule_bot.requests.post', post)
    bot.send_message = Mock(return_value={'message_id': 43})
    update = envelope(1, data='learn:card')
    update['callback_query']['message']['message_id'] = 42
    polling = DurablePolling(bot, workers=1, max_pending=2)
    polling.start()
    try:
        assert polling.accept(update)
    finally:
        polling.close()
    assert handled == ['learn:card']
    assert bot.process_update(update) is None
    assert bot.send_message.call_count == int(edit_fails)
    learning.bind_message.assert_called_once_with(43 if edit_fails else 42, 'lesson', 'material')
