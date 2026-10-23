"""Update 1 acceptance through the active transport and synthetic providers."""
from copy import deepcopy
from functools import partial
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from adapters.google_calendar_adapter import GoogleCalendarAdapter, CalendarReadUnavailable
from services.closed_beta_runtime import ClosedBetaApplication, PilotSource
from services.invited_beta_runtime import InvitedBot
from services.image_schedule import ImageScheduleError, ImageScheduleReader
from services.natural_text_application import PerUserNaturalCommandExecutor
from tests.test_closed_beta_runtime import Calendar, ICS, NOW, confirmation, application
from tests.test_image_schedule import image_preview
from tests.test_invited_beta import envelope, onboard
from tests.test_published_plan_updates import setup as published_setup, move
from tests.test_published_plan_updates import ProcessCrash


def test_invited_text_tasks_confirm_restart_isolation_and_preview(tmp_path, monkeypatch):
    calendars = {'101': Calendar(), '202': Calendar()}
    def fetch(url, path, **kwargs):
        path.write_bytes(ICS)
    factory = partial(ClosedBetaApplication, enable_plan_updates=True, now=lambda: NOW,
                      source_factory=lambda store: PilotSource(store, fetcher=fetch))
    def make_bot():
        bot = InvitedBot('synthetic', tmp_path, calendars.__getitem__, factory)
        monkeypatch.setattr(bot, 'send_message', Mock())
        return bot
    bot = make_bot()
    for chat in calendars:
        bot.invites.set_access(chat, True)
        onboard(bot, chat)
    preview = bot.process_update(envelope(101, text='Добавь задачу купить учебник'))
    assert 'Создать задачу: купить учебник' in preview['text']
    first_app = bot.applications['101'][1]
    executor = PerUserNaturalCommandExecutor(first_app.directory / 'natural_commands.json')
    assert executor.snapshot()['tasks'] == []
    button = confirmation(preview)
    assert 'уже не актуально' in bot.process_update(envelope(101, '202', data=button))['text']
    bot = make_bot()
    assert '/tasks' in bot.process_update(envelope(102, data=button))['text']
    assert len(executor.snapshot()['tasks']) == 1
    assert bot.process_update(envelope(102, data=button)) is None
    assert 'уже не актуально' in bot.process_update(envelope(103, data=button))['text']
    assert 'купить учебник' in bot.process_update(envelope(104, text='Покажи мои задачи'))['text']
    assert 'Пока пусто' in bot.process_update(envelope(102, '202', text='/tasks'))['text']
    assert 'не поддерживаются' in bot.process_update(envelope(105, text='Перенеси встречу завтра'))['text']
    plan = bot.process_update(envelope(106, text='Покажи план!'))
    assert plan['buttons'] and 'Calendar' in plan['text']
    bot.send_message.assert_called_once_with('101', bot.PREVIEW_ACK)
    assert all(not calendar.writes for calendar in calendars.values())


def test_new_invalid_text_supersedes_old_task_buttons(tmp_path):
    app, calendar, _, _ = published_setup(tmp_path)
    old = app.handle_text('101', 'Создай задачу купить учебник')
    assert not app.handle_text('101', 'Создай задачу')['buttons']
    assert 'уже не актуально' in app.handle_callback('101', confirmation(old))['text']
    assert not PerUserNaturalCommandExecutor(app.directory / 'natural_commands.json').snapshot()['tasks']


def test_image_correction_is_inert_and_replaces_old_confirmation(tmp_path):
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    preview = image_preview(app)
    edit = next(button['callback_data'] for row in preview['buttons'] for button in row
                if button['callback_data'].startswith('img:edit:'))
    assert app.handle_callback('202', edit) is None
    assert 'через |' in app.handle_callback('101', edit)['text']
    assert 'не применено' in app.handle_text('101', '1 | Физика | неверно | неверно |')['text']
    corrected = app.handle_text('101', '1 | Физика | 25.09.2026 12:00 | 25.09.2026 13:30 | Ауд. 5')
    assert 'Физика' in corrected['text'] and '25.09.2026 12:00' in corrected['text']
    assert not app.image_path.exists() and app.adapter.writes == []
    assert 'устарело' in app.handle_callback('101', confirmation(preview))['text']
    assert app.image_pending is not None
    assert 'добавлено' in app.handle_callback('101', confirmation(corrected))['text']
    state = app.source.state_store.load(app.account.id)
    events = app._source_events(state.sources[state.active_source_id])
    assert len(events) == 1 and events[0].summary == 'Физика'
    assert events[0].dtstart.day == 25 and app.adapter.writes == []


def test_image_correction_cannot_outlive_original_preview(tmp_path):
    from datetime import timedelta
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    preview = image_preview(app)
    app.handle_callback('101', preview['buttons'][-1][0]['callback_data'])
    app.now = lambda: NOW + timedelta(minutes=11)
    assert 'устарело' in app.handle_text('101', '1 | Физика | 25.09.2026 12:00 | 25.09.2026 13:30 |')['text']
    assert not app.image_path.exists()


def test_cancelling_image_correction_returns_to_normal_text_routing(tmp_path):
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    preview = image_preview(app)
    app.handle_callback('101', preview['buttons'][-1][0]['callback_data'])
    app.handle_callback('101', preview['buttons'][0][1]['callback_data'])
    assert app.image_editing is False
    assert 'устарело' not in app.handle_text('101', 'обычный текст')['text']


class BatchService:
    def __init__(self):
        self.batches = 0
        self.fault = None

    def events(self):
        return self

    def list(self, **kwargs):
        return ('uid', kwargs['iCalUID'])

    def get(self, **kwargs):
        return ('id', kwargs['eventId'])

    def new_batch_http_request(self):
        service = self
        class Batch:
            def __init__(self):
                self.entries = []
            def add(self, request, *, request_id, callback):
                self.entries.append((request, request_id, callback))
            def execute(self):
                service.batches += 1
                for (kind, value), request_id, callback in reversed(self.entries):
                    error = None
                    event = {'id': value, 'iCalUID': value}
                    result = {'items': [event]} if kind == 'uid' else event
                    if service.fault == 'duplicate' and kind == 'uid':
                        result = {'items': [event, event]}
                    if service.fault == 'pagination' and kind == 'uid':
                        result = {'items': [event], 'nextPageToken': 'more'}
                    if service.fault == 'missing_id' and kind == 'id':
                        error = HttpError(Response({'status': '404'}), b'secret')
                    if service.fault == 'denied' and kind == 'uid':
                        error = HttpError(Response({'status': '404'}), b'secret')
                    if service.fault == 'partial' and kind == 'id':
                        error = HttpError(Response({'status': '503'}), b'secret')
                    if service.fault != 'lost_callback':
                        callback(request_id, result, error)
        return Batch()


def test_calendar_batches_60_identity_pairs_into_two_http_roundtrips():
    adapter = GoogleCalendarAdapter()
    adapter.service = BatchService()
    identities = {f'event-{index}': f'event-{index}' for index in range(60)}
    pairs = adapter.get_event_pairs('calendar', identities)
    assert len(pairs) == 60 and adapter.service.batches == 2
    assert all(uid_read == id_read for uid_read, id_read in pairs.values())


@pytest.mark.parametrize('fault', ['duplicate', 'pagination', 'denied', 'partial', 'lost_callback'])
def test_batch_read_rejects_ambiguous_or_partial_results(fault):
    adapter = GoogleCalendarAdapter()
    adapter.service = BatchService()
    adapter.service.fault = fault
    with pytest.raises((RuntimeError, HttpError, KeyError)):
        adapter.get_event_pairs('calendar', {'uid': 'id'})


def test_batch_missing_id_needs_successful_collection_probe():
    adapter = GoogleCalendarAdapter()
    adapter.service = BatchService()
    adapter.service.fault = 'missing_id'
    assert adapter.get_event_pairs('calendar', {'uid': 'id'})['uid'][1] is None
    adapter.service.fault = 'denied'
    with pytest.raises(CalendarReadUnavailable):
        adapter.get_event_pairs('calendar', {'uid': 'id'})


def test_published_apply_uses_fresh_batches_and_still_detects_manual_edits(tmp_path, monkeypatch):
    app, calendar, content, _ = published_setup(tmp_path)
    calls = []
    def read_pairs(calendar_id, identities):
        calls.append(dict(identities))
        rows = {event['iCalUID']: event for (cal, _), event in calendar.remote.items() if cal == calendar_id}
        return {uid: (deepcopy(rows.get(uid)), deepcopy(calendar.remote.get((calendar_id, event_id))))
                for uid, event_id in identities.items()}
    monkeypatch.setattr(calendar, 'get_event_pairs', read_pairs, raising=False)
    move(content)
    preview = app.handle_text('101', '/weekly_preview')
    writes = list(calendar.writes)
    event = next(iter(calendar.remote.values()))
    event['summary'] = 'Manual edit'
    assert 'Ручное изменение' in app.handle_callback('101', confirmation(preview))['text']
    assert calendar.writes == writes and calls


@pytest.mark.parametrize('crash', [False, True])
def test_batched_apply_and_restart_keep_ids_without_duplicate_writes(tmp_path, monkeypatch, crash):
    app, calendar, content, factory = published_setup(tmp_path)
    calls = []
    def read_pairs(calendar_id, identities):
        calls.append(dict(identities))
        rows = {event['iCalUID']: event for (cal, _), event in calendar.remote.items() if cal == calendar_id}
        return {uid: (deepcopy(rows.get(uid)), deepcopy(calendar.remote.get((calendar_id, event_id))))
                for uid, event_id in identities.items()}
    monkeypatch.setattr(calendar, 'get_event_pairs', read_pairs, raising=False)
    old_ids = set(calendar.remote)
    move(content)
    preview = app.handle_text('101', '/weekly_preview')
    if crash:
        calendar.crash_after_write = True
        with pytest.raises(ProcessCrash):
            app.handle_callback('101', confirmation(preview))
        app = factory(tmp_path, '101', calendar)
        preview = app.handle_text('101', '/weekly_preview')
    assert 'обновлён и проверен' in app.handle_callback('101', confirmation(preview))['text']
    assert set(calendar.remote) == old_ids
    assert calendar.update_count == 2  # Class + preparation, exactly once each.
    writes = list(calendar.writes)
    assert 'повторных записей нет' in app.handle_text('101', '/weekly_preview')['text']
    assert calendar.writes == writes and len(calls) >= 5


@pytest.mark.parametrize('fault', ['id_mismatch', 'etag_mismatch', 'ownership'])
def test_batch_identity_validation_is_as_strict_as_individual_reads(tmp_path, monkeypatch, fault):
    app, calendar, content, _ = published_setup(tmp_path)
    def read_pairs(calendar_id, identities):
        rows = {event['iCalUID']: event for (cal, _), event in calendar.remote.items() if cal == calendar_id}
        result = {uid: (deepcopy(rows.get(uid)), deepcopy(calendar.remote.get((calendar_id, event_id))))
                  for uid, event_id in identities.items()}
        first = next(iter(result.values()))[1]
        if fault == 'id_mismatch':
            first['id'] = 'different'
        elif fault == 'etag_mismatch':
            first['etag'] = 'different'
        else:
            first['extendedProperties'] = {}
        return result
    monkeypatch.setattr(calendar, 'get_event_pairs', read_pairs, raising=False)
    writes = list(calendar.writes)
    move(content)
    assert not app.handle_text('101', '/weekly_preview')['buttons']
    assert calendar.writes == writes


@pytest.mark.parametrize('status, expected', [(402, 'Платные'), (404, 'бесплатная модель'), (413, 'меньший фрагмент')])
def test_photo_failures_explain_operator_or_user_action(monkeypatch, status, expected):
    response = Mock(status_code=status)
    response.json.return_value = {'error': {'message': 'secret provider body'}}
    monkeypatch.setattr('services.image_schedule.requests.post', Mock(return_value=response))
    with pytest.raises(ImageScheduleError, match=expected) as caught:
        ImageScheduleReader(api_key='synthetic').extract(b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')
    assert 'secret' not in str(caught.value)


def test_calendar_limit_during_apply_returns_actionable_hint_without_secrets(tmp_path):
    app, calendar, content, _ = published_setup(tmp_path)
    move(content)
    preview = app.handle_text('101', '/weekly_preview')
    before = list(calendar.writes)
    def fail(*args, **kwargs):
        raise HttpError(Response({'status': '429'}), b'secret provider body')
    calendar.list_visible_calendars = fail
    result = app.handle_callback('101', confirmation(preview))
    assert 'Подожди несколько минут' in result['text']
    assert 'secret' not in result['text'] and calendar.writes == before


def test_initial_publication_timeout_explains_partial_state(tmp_path):
    import requests
    from tests.test_closed_beta_runtime import onboard as onboard_app
    app = application(tmp_path)
    onboard_app(app)
    preview = app.handle_text('101', '/weekly_preview')
    def fail(*args, **kwargs):
        raise requests.Timeout('secret provider body')
    app.adapter._get_or_create_calendar = fail
    result = app.handle_callback('101', confirmation(preview))
    assert 'часть событий могла сохраниться' in result['text']
    assert 'не ответил вовремя' in result['text'] and 'secret' not in result['text']
