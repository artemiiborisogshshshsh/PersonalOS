from datetime import timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from services.invited_beta_runtime import InvitedBot
from services.telegram_personal_plans import parse_personal_plan
from services.telegram_voice_transcription import ENDPOINT, transcribe_voice
from tests.test_closed_beta_runtime import Calendar, NOW, application, onboard
from tests.test_invited_beta import envelope


class PersonalCalendar(Calendar):
    def __init__(self):
        super().__init__()
        self.calendars['primary'] = 'Основной'

    def get_event_by_id(self, calendar_id, event_id, *, strict):
        assert calendar_id == 'primary' and strict
        return self.remote.get((calendar_id, event_id))

    def insert_personal_plan(self, event_id, title, start, end):
        assert ('primary', event_id) not in self.remote
        self.writes.append(event_id)
        self.remote['primary', event_id] = {
            'id': event_id, 'summary': title, 'start': {'dateTime': start.isoformat()},
            'end': {'dateTime': end.isoformat()},
        }
        if self.fail_insert_after_write:
            raise RuntimeError('response lost after insert')


def save_button(reply):
    return next(b['callback_data'] for row in reply['buttons'] for b in row
                if b['callback_data'].startswith('personal:save:'))


@pytest.mark.parametrize('phrase, hour, minute, length', [
    ('Тренировка завтра в 18:30 на 45 минут', 18, 30, 45),
    ('Тренировка завтра в восемнадцать тридцать на сорок пять минут', 18, 30, 45),
    ('Завтра в шесть вечера, Тренировка на полчаса.', 18, 0, 30),
    ('Добавь личное событие: Тренировка 23 сентября в 18 на час', 18, 0, 60),
    ('Тренировка 23.09.2026 в 18:30', 18, 30, 60),
])
def test_text_and_spoken_dates(phrase, hour, minute, length):
    plan = parse_personal_plan(phrase, NOW, ZoneInfo('Asia/Tomsk'))
    assert plan.title == 'Тренировка'
    assert plan.start == NOW.replace(day=23, hour=hour, minute=minute)
    assert plan.end - plan.start == timedelta(minutes=length)


@pytest.mark.parametrize('phrase', [
    'Тренировка завтра', 'Тренировка завтра в 25:99', 'Тренировка 31.02 в 18',
    'Тренировка сегодня в 8', 'Тренировка завтра в 18 на 0 минут',
    'Тренировка завтра в 18 и встреча послезавтра в 20',
])
def test_no_guessed_date_or_time(phrase):
    with pytest.raises(ValueError):
        parse_personal_plan(phrase, NOW, ZoneInfo('Asia/Tomsk'))


def test_menu_confirm_checks_ownership_and_retries_same_google_event(tmp_path):
    calendar = PersonalCalendar()
    app = application(tmp_path, calendar)
    onboard(app)
    app.handle_callback('101', 'personal:new')
    draft = app.handle_text('101', 'Тренировка завтра в 18:30 на 45 минут')
    token = save_button(draft)
    assert '23.09.2026 18:30–19:15' in draft['text']
    assert not calendar.writes
    assert app.handle_callback('202', token) is None
    calendar.fail_insert_after_write = True
    assert 'не подтвердил' in app.handle_callback('101', token)['text']
    assert 'записано' in app.handle_callback('101', token)['text']
    assert len(calendar.writes) == 1
    assert 'устарел' in app.handle_callback('101', token)['text']
    assert len(calendar.writes) == 1


def test_calendar_conflict_blocks_write_and_old_confirmation(tmp_path):
    calendar = PersonalCalendar()
    app = application(tmp_path, calendar)
    onboard(app)
    token = save_button(app.handle_text('101', 'Добавь личный план Тренировка завтра в 18'))
    when = NOW.replace(day=23, hour=18)
    calendar.calendars['Personal University Schedule'] = 'Учёба'
    calendar.remote['Personal University Schedule', 'lesson'] = {
        'id': 'lesson', 'summary': 'Пара',
        'start': {'dateTime': when.isoformat()},
        'end': {'dateTime': (when + timedelta(hours=1)).isoformat()},
        'description': 'Стабильный ID личного события: synthetic',
    }
    assert 'Пара' in app.handle_callback('101', token)['text']
    assert not calendar.writes
    app.handle_callback('101', 'main:open')
    assert 'устарел' in app.handle_callback('101', token)['text']
    assert not calendar.writes


def test_voice_route_requires_opt_in_and_never_writes_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv('Groq_API_KEY', 'mock-key')
    monkeypatch.setenv('PERSONAL_OS_GROQ_FREE_TIER', '1')
    calendar = PersonalCalendar()
    bot = InvitedBot('mock-token', tmp_path / 'bot', lambda chat: calendar,
                     lambda directory, chat, adapter: application(directory, adapter))
    bot.invites.set_access('101', True)
    audio = {'file_id': 'audio1', 'duration': 21}
    stt = Mock(return_value='Завтра в шесть вечера, тренировка на 45 минут.')
    monkeypatch.setattr('services.invited_beta_runtime.transcribe_voice', stt)

    def send_voice(seq, payload=audio):
        update = envelope(seq, '101', '')
        update['message']['voice'] = payload
        return bot.process_update(update)

    assert 'Ввести голосом' in send_voice(1)['text']
    stt.assert_not_called()
    entry = bot.process_update(envelope(2, '101', '', data='personal:voice'))
    assert 'Groq' in entry['text']
    assert '60 секунд' in send_voice(3, {**audio, 'duration': 61})['text']
    stt.assert_not_called()
    draft = send_voice(4)
    assert 'Распознано:' in draft['text']
    assert '18:00–18:45' in draft['text']
    assert not calendar.writes
    assert 'записано' in bot.process_update(envelope(5, '101', '', data=save_button(draft)))['text']
    assert len(calendar.writes) == 1


def test_audio_provider_uses_russian_ogg_and_never_sends_user_identifiers(monkeypatch):
    meta = Mock(status_code=200)
    meta.json.return_value = {'ok': True, 'result': {'file_path': 'voice/file_1.oga'}}
    stream = Mock(status_code=200)
    stream.iter_content.return_value = iter([b'OggS  OpusHead  test'])
    download = Mock()
    download.__enter__ = Mock(return_value=stream)
    download.__exit__ = Mock(return_value=None)
    monkeypatch.setattr('services.telegram_voice_transcription.requests.get',
                        Mock(side_effect=[meta, download]))
    provider = Mock(status_code=200)
    provider.json.return_value = {'text': 'Тренировка завтра в 18'}
    post = Mock(return_value=provider)
    monkeypatch.setattr('services.telegram_voice_transcription.requests.post', post)
    assert transcribe_voice('https://api.telegram.org/botmock', {'file_id': 'test', 'duration': 20},
                            'mock-key') == 'Тренировка завтра в 18'
    assert post.call_args.args == (ENDPOINT,)
    args = post.call_args.kwargs
    assert args['files']['file'] == ('voice.ogg', b'OggS  OpusHead  test', 'audio/ogg')
    assert args['data']['model'] == 'whisper-large-v3-turbo'
    assert args['data']['language'] == 'ru'
    assert args['allow_redirects'] is False


def test_homework_phrase_moves_existing_preparation_and_keeps_materials(tmp_path):
    from copy import deepcopy
    from tests.test_learning_calendar import setup, prepare_homework, operation, remote_for

    app, calendar, _, _ = setup(tmp_path)
    lesson_id = prepare_homework(app, deadline=NOW.replace(day=26, hour=20))
    lesson = app.learning.store.get_lesson(lesson_id)
    before = deepcopy(operation(app).published_plan['rows'])
    original = next(b for b in operation(app).blocks if b.id.startswith('learning-prep:'))
    writes = list(calendar.writes)
    first = app.handle_text('101', 'Буду делать ДЗ по ОС')
    assert 'Нашёл ДЗ: ОС' in first['text']
    assert calendar.writes == writes
    draft = app.handle_text('101', '24 сентября в 18:30')
    assert 'сохранённому ДЗ: ОС' in draft['text']
    assert calendar.writes == writes
    callback = next(b['callback_data'] for row in draft['buttons'] for b in row
                    if b['callback_data'].startswith('personal:hw_save:'))
    assert 'стоит в Google Calendar' in app.handle_callback('101', callback)['text']
    after = operation(app)
    blocks = [b for b in after.blocks if b.source_event_id == lesson['source_personal_id']
              and b.id.startswith('learning-prep:')]
    assert len(blocks) == 1 and blocks[0].start == NOW.replace(day=24, hour=18, minute=30)
    assert original.id not in after.published_plan['rows']
    assert 'Номера 15–20' in remote_for(app, calendar, blocks[0].id)['description']
    assert app.learning.store.get_lesson(lesson_id)['materials'] == lesson['materials']
    assert all(row == after.published_plan['rows'][uid] for uid, row in before.items() if uid != original.id)
    assert not app.natural_text.proposals.application_service.snapshot()['tasks']
    assert all(cid != 'primary' for cid, _ in calendar.remote)


def test_unknown_homework_never_creates_a_personal_event(tmp_path):
    calendar = PersonalCalendar()
    app = application(tmp_path, calendar)
    onboard(app)
    reply = app.handle_text('101', 'Буду делать ДЗ по математике завтра в 18')
    assert 'Не нашёл сохранённого' in reply['text']
    assert app.personal_plans.pending is None and not calendar.writes


def test_pinned_homework_conflict_leaves_calendar_and_journal_untouched(tmp_path):
    from copy import deepcopy
    from tests.test_learning_calendar import setup, prepare_homework, operation

    app, calendar, _, _ = setup(tmp_path)
    prepare_homework(app, deadline=NOW.replace(day=26, hour=20))
    before = deepcopy(calendar.remote)
    writes = list(calendar.writes)
    draft = app.handle_text('101', 'Буду делать ДЗ по ОС 25 сентября в 10')
    callback = next(b['callback_data'] for row in draft['buttons'] for b in row
                    if b['callback_data'].startswith('personal:hw_save:'))
    response = app.handle_callback('101', callback)
    assert 'не хватает свободного времени' in response['text']
    assert calendar.remote == before and calendar.writes == writes
    assert not operation(app).pending_plan_update


def test_voice_math_homework_uses_saved_lesson_and_asks_which_when_ambiguous(tmp_path, monkeypatch):
    from tests.test_learning_lifecycle import event, seed

    monkeypatch.setenv('Groq_API_KEY', 'mock-key')
    monkeypatch.setenv('PERSONAL_OS_GROQ_FREE_TIER', '1')
    calendar = PersonalCalendar()
    app = application(tmp_path / 'state', calendar)
    seed(app.learning.store, [event('math1', 'Математика (ПР)', day=24),
                              event('math2', 'Математика (ПР)', day=25)])
    for lesson in app.learning.store.lessons():
        app.learning.store.add_material(lesson['id'], 'Сохранённое задание')
    bot = InvitedBot('mock-token', tmp_path / 'bot', lambda chat: calendar,
                     lambda directory, chat, adapter: app)
    bot.invites.set_access('101', True)
    monkeypatch.setattr('services.invited_beta_runtime.transcribe_voice',
                        Mock(return_value='Буду делать ДЗ по математике.'))
    bot.process_update(envelope(1, '101', '', data='personal:voice'))
    update = envelope(2, '101', '')
    update['message']['voice'] = {'file_id': 'audio1', 'duration': 9}
    response = bot.process_update(update)
    assert 'Какое сохранённое ДЗ' in response['text']
    picks = [b for row in response['buttons'] for b in row if b['callback_data'].startswith('personal:hw_pick:')]
    assert len(picks) == 2 and all('Математика' in b['text'] for b in picks)
    selected = bot.process_update(envelope(3, '101', '', data=picks[1]['callback_data']))
    assert 'Когда начать' in selected['text']
    assert app.personal_plans.pending is None and not calendar.writes
    assert all(len(row['materials']) == 1 for row in app.learning.store.lessons())


def test_voice_disabled_until_free_tier_is_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv('Groq_API_KEY', 'mock-key')
    monkeypatch.delenv('PERSONAL_OS_GROQ_FREE_TIER', raising=False)
    app = application(tmp_path / 'state')
    bot = InvitedBot('mock-token', tmp_path / 'bot', lambda chat: app.adapter,
                     lambda directory, chat, adapter: app)
    bot.invites.set_access('101', True)
    transcribe = Mock()
    monkeypatch.setattr('services.invited_beta_runtime.transcribe_voice', transcribe)
    opened = bot.process_update(envelope(1, '101', '', data='personal:new'))
    assert not any(b['callback_data'] == 'personal:voice' for row in opened['buttons'] for b in row)
    update = envelope(2, '101', '')
    update['message']['voice'] = {'file_id': 'audio1', 'duration': 12}
    assert 'недоступен' in bot.process_update(update)['text']
    transcribe.assert_not_called()
    assert not (app.directory / 'personal_plan_voice_quota.json').exists()


def test_groq_rate_limit_falls_back_to_text_without_retry(tmp_path, monkeypatch):
    from services.telegram_voice_transcription import VoiceQuotaExhausted

    monkeypatch.delenv('Groq_API_KEY', raising=False)
    monkeypatch.setenv('GROQ_API_KEY', 'mock-key')
    monkeypatch.setenv('PERSONAL_OS_GROQ_FREE_TIER', '1')
    app = application(tmp_path / 'state')
    bot = InvitedBot('mock-token', tmp_path / 'bot', lambda chat: app.adapter,
                     lambda directory, chat, adapter: app)
    bot.invites.set_access('101', True)
    transcribe = Mock(side_effect=VoiceQuotaExhausted)
    monkeypatch.setattr('services.invited_beta_runtime.transcribe_voice', transcribe)
    bot.process_update(envelope(1, '101', '', data='personal:voice'))
    update = envelope(2, '101', '')
    update['message']['voice'] = {'file_id': 'audio1', 'duration': 12}
    assert 'добавить план текстом' in bot.process_update(update)['text']
    transcribe.assert_called_once()
    assert transcribe.call_args.args[-1] == 'mock-key'
    assert app.personal_plans.pending is None


@pytest.mark.parametrize('failure_kind, reply_fragment, stage, status', [
    ('telegram', 'временно недоступен', 'telegram_metadata', None),
    ('metadata_json', 'временно недоступен', 'telegram_metadata', '200'),
    ('download_stream', 'временно недоступен', 'telegram_download', '200'),
    ('groq', 'временно недоступен', 'groq_response', '503'),
    ('groq_forbidden', 'временно недоступен', 'groq_response', '403'),
    ('invalid', 'аудио', 'audio_validation', None),
    ('rate', 'исчерпан', 'groq_response', '429'),
])
def test_voice_failures_do_not_charge_quota_and_logs_are_sanitized(
        tmp_path, monkeypatch, caplog, failure_kind, reply_fragment, stage, status):
    import logging
    import requests

    monkeypatch.setenv('GROQ_API_KEY', 'private-api-key-sentinel')
    monkeypatch.setenv('PERSONAL_OS_GROQ_FREE_TIER', '1')
    app = application(tmp_path / 'state')
    bot = InvitedBot('private-telegram-token-sentinel', tmp_path / 'bot', lambda chat: app.adapter,
                     lambda directory, chat, adapter: app)
    bot.invites.set_access('101', True)
    bot.process_update(envelope(1, '101', '', data='personal:voice'))
    quota_file = app.directory / 'personal_plan_voice_quota.json'
    metadata = Mock(status_code=200)
    metadata.json.return_value = {'ok': True, 'result': {'file_path': 'voice/a.oga'}}
    stream = Mock(status_code=200)
    stream.iter_content.return_value = iter([b'OggS  OpusHead  private-audio-sentinel'])
    if failure_kind == 'invalid':
        stream.iter_content.return_value = iter([b'not an ogg private-audio-sentinel'])
    if failure_kind == 'download_stream':
        stream.iter_content.side_effect = requests.ConnectionError('private-stream-body-sentinel')
    download = Mock()
    download.__enter__ = Mock(return_value=stream)
    download.__exit__ = Mock(return_value=None)
    if failure_kind == 'telegram':
        get = Mock(side_effect=requests.ConnectionError('private-url-sentinel private-body-sentinel'))
    elif failure_kind == 'metadata_json':
        metadata.json.side_effect = ValueError('private-metadata-body-sentinel')
        get = Mock(return_value=metadata)
    else:
        get = Mock(side_effect=[metadata, download])
    monkeypatch.setattr('services.telegram_voice_transcription.requests.get', get)
    post = Mock()
    if failure_kind in {'groq', 'rate'}:
        response = Mock(status_code=429 if failure_kind == 'rate' else 503)
        response.json.side_effect = ValueError('private-response-body-sentinel')
        post.return_value = response
    if failure_kind == 'groq_forbidden':
        response = requests.Response()
        response.status_code = 403
        response._content = b'{"error":{"code":"unsupported_country_region_territory", "message":"private-response-body-sentinel"}}'
        post.return_value = response
    monkeypatch.setattr('services.telegram_voice_transcription.requests.post', post)
    caplog.set_level(logging.WARNING)
    first = envelope(2, '101', '')
    first['message']['voice'] = {'file_id': 'private-file-id-sentinel', 'duration': 10}
    assert reply_fragment in bot.process_update(first)['text']
    assert not quota_file.exists()
    log_text = caplog.text
    assert stage in log_text
    if status:
        assert status in log_text
    if failure_kind == 'groq_forbidden':
        assert 'HTTP 403; reason unsupported_region' in log_text
    if failure_kind in {'telegram', 'metadata_json', 'download_stream', 'invalid'}:
        post.assert_not_called()
    for secret in ('private-api-key-sentinel', 'private-telegram-token-sentinel',
                   'private-url-sentinel', 'private-body-sentinel', 'private-response-body-sentinel',
                   'private-metadata-body-sentinel', 'private-stream-body-sentinel',
                   'private-audio-sentinel', 'private-file-id-sentinel'):
        assert secret not in log_text

    monkeypatch.setattr('services.invited_beta_runtime.transcribe_voice',
                        Mock(return_value='Тренировка завтра в 18'))
    second = envelope(3, '101', '')
    second['message']['voice'] = {'file_id': 'audio2', 'duration': 10}
    assert 'Распознано:' in bot.process_update(second)['text']
    import json
    assert json.loads(quota_file.read_text())['count'] == 1
