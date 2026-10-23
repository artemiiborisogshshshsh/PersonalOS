"""Image import critical path with synthetic Telegram, vision and Calendar."""
from datetime import timedelta
from unittest.mock import Mock

import pytest

from services.image_schedule import (
    ImageScheduleError, ImageScheduleReader, MAX_IMAGE_BYTES,
    download_telegram_image, validate_extraction, merged_image_calendar, image_calendar_events,
)
from services.user_state_backup import UserStateBackupService
from models import EventType
from tests.test_closed_beta_runtime import application, NOW, confirmation, onboard
from tests.test_invited_beta import setup, envelope


def payload(**changes):
    row = dict(summary='Математика', start='2026-09-24T10:00', end='2026-09-24T11:30',
               location='Ауд. 204', event_type='lecture')
    row.update(changes)
    return {'clarification': '', 'events': [row]}


def reader(app):
    app.image_reader = Mock()
    app.image_reader.extract.return_value = validate_extraction(payload(), 'Asia/Tomsk')


def image_preview(app):
    reader(app)
    return app.handle_image('101', b'image', 'Неделя 21–27 сентября 2026')


def finish_onboarding(app):
    response = app.handle_text('101', '/attendance')
    for _ in range(10):
        if response.get('attendance_complete'):
            break
        response = app.handle_callback('101', response['buttons'][0][0]['callback_data'])
    assert response['attendance_complete']
    app.handle_text('101', '/sleep 23:00 06:40')
    app.handle_text('101', '/travel 30')
    app.handle_text('101', '/calendar_status')


def test_image_only_onboarding_planning_calendar_restart_and_backup(tmp_path):
    app = application(tmp_path / 'source')
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    preview = image_preview(app)
    assert '24.09.2026 10:00' in preview['text']
    assert app.adapter.writes == [] and not app.image_path.exists()
    assert app.handle_callback('202', confirmation(preview)) is None
    app.handle_callback('101', confirmation(preview))
    assert app.onboarding.store.load().step.value == 'attendance'
    state = app.source.state_store.load(app.account.id)
    events = app._source_events(state.sources[state.active_source_id])
    assert events[0].event_type == EventType.LECTURE
    assert events[0].location == 'Ауд. 204'
    finish_onboarding(app)
    preview = app.handle_text('101', '/weekly_preview')
    assert 'Математика' in preview['text'] and preview['buttons']
    assert app.adapter.writes == []
    assert 'опубликован' in app.handle_callback('101', confirmation(preview))['text']
    saved_writes = list(app.adapter.writes)
    archive = tmp_path / 'backup.zip'
    manifest = UserStateBackupService(app.root).backup(app.account.id, archive)
    assert 'image_schedule.ics' in manifest['files']
    target = tmp_path / 'restored'
    UserStateBackupService(target).restore(app.account.id, archive)
    restored = application(target, app.adapter)
    assert 'уже опубликован' in restored.handle_text('101', '/weekly_preview')['text']
    assert app.adapter.writes == saved_writes


def test_repeat_adds_no_duplicates_and_tpu_keeps_its_events(tmp_path):
    app = application(tmp_path)
    onboard(app)
    for _ in range(2):
        app.handle_callback('101', confirmation(image_preview(app)))
    state = app.source.state_store.load(app.account.id)
    assert state.active_source_id == 'tpu-primary'
    events = app._source_events(state.sources[state.active_source_id])
    assert len(events) == 2
    assert {event.summary for event in events} == {'ОС (ЛК)', 'Математика'}
    assert app.adapter.writes == []


def test_weekly_image_survives_restart_moves_dates_and_stops_without_calendar_writes(tmp_path):
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    preview = image_preview(app)
    weekly = preview['buttons'][1][0]['callback_data']
    assert 'Повторять каждую неделю' == preview['buttons'][1][0]['text']
    assert app.handle_callback('202', weekly) is None
    assert 'Еженедельное' in app.handle_callback('101', weekly)['text']
    finish_onboarding(app)
    source = app.source.state_store.load(app.account.id).sources['image-primary']
    first = app._source_events(source)[0]
    assert first.dtstart.day == 24
    assert 'Математика' in app.handle_text('101', '/weekly_preview')['text']
    restored = application(tmp_path, app.adapter)
    restored.now = lambda: NOW + timedelta(weeks=2)
    following = restored._source_events(source)[0]
    assert following.dtstart == first.dtstart + timedelta(weeks=2)
    assert following.dtend == first.dtend + timedelta(weeks=2)
    assert following.uid != first.uid
    assert restored._source_events(source)[0].uid == following.uid
    assert 'Математика' in restored.handle_text('101', '/weekly_preview')['text']
    assert 'остановлены' in restored.handle_text('101', '/stop_image_repeats')['text']
    assert restored._source_events(source) == []
    assert app.adapter.writes == []


def test_weekly_import_deduplicates_templates_and_is_bounded(tmp_path):
    path = tmp_path / 'schedule.ics'
    rows = validate_extraction(payload(), 'Asia/Tomsk')
    path.write_bytes(merged_image_calendar(path, rows))
    path.write_bytes(merged_image_calendar(path, rows, weekly=True))
    later = validate_extraction(payload(start='2026-10-01T10:00', end='2026-10-01T11:30'), 'Asia/Tomsk')
    path.write_bytes(merged_image_calendar(path, later, weekly=True))
    events = image_calendar_events(path, NOW, NOW + timedelta(weeks=4))
    assert len(events) == 4
    assert events[0]['uid'] == rows[0]['uid']
    assert len({event['uid'] for event in events}) == 4
    assert [event['dtstart'].hour for event in events] == [10] * 4
    assert image_calendar_events(path, NOW - timedelta(weeks=2), NOW) == []


def test_multiple_weeks_have_no_weekly_option_and_reject_forged_callback(tmp_path):
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    reader(app)
    app.image_reader.extract.return_value += validate_extraction(
        payload(start='2026-10-01T10:00', end='2026-10-01T11:30'), 'Asia/Tomsk')
    preview = app.handle_image('101', b'image')
    assert not any(button['callback_data'].startswith('img:weekly:')
                   for row in preview['buttons'] for button in row)
    forged = confirmation(preview).replace('img:confirm:', 'img:weekly:')
    assert 'не завершён' in app.handle_callback('101', forged)['text']
    assert not app.image_path.exists()


def test_weekly_image_backup_preserves_future_occurrences(tmp_path):
    app = application(tmp_path / 'source')
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    preview = image_preview(app)
    app.handle_callback('101', preview['buttons'][1][0]['callback_data'])
    archive = tmp_path / 'backup.zip'
    UserStateBackupService(app.root).backup(app.account.id, archive)
    destination = tmp_path / 'restored'
    UserStateBackupService(destination).restore(app.account.id, archive)
    restored = application(destination)
    restored.now = lambda: NOW + timedelta(weeks=3)
    source = restored.source.state_store.load(restored.account.id).sources['image-primary']
    assert restored._source_events(source)[0].dtstart == validate_extraction(payload(), 'Asia/Tomsk')[0]['start'] + timedelta(weeks=3)


def test_image_duplicate_of_tpu_class_is_not_planned_twice(tmp_path):
    app = application(tmp_path)
    onboard(app)
    reader(app)
    app.image_reader.extract.return_value = validate_extraction(payload(summary='ОС', location=''), 'Asia/Tomsk')
    preview = app.handle_image('101', b'image')
    app.handle_callback('101', confirmation(preview))
    state = app.source.state_store.load(app.account.id)
    assert len(app._source_events(state.sources[state.active_source_id])) == 1


def test_pilot_transport_downloads_only_for_owner(tmp_path, monkeypatch):
    from services.closed_beta_runtime import PilotBot
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    reader(app)
    bot = PilotBot('synthetic', '101', app)
    progress = Mock()
    monkeypatch.setattr(bot, 'send_message', progress)
    download = Mock(return_value=b'image')
    monkeypatch.setattr('services.closed_beta_runtime.download_telegram_image', download)
    assert bot.handle_image('202', {'photo': [{'file_id': 'x'}]}) is None
    download.assert_not_called()
    progress.assert_not_called()
    assert bot.handle_image('101', {'photo': [{'file_id': 'x'}], 'caption': '21–27 сентября 2026'})['buttons']
    assert app.image_reader.extract.call_args.args[1] == '21–27 сентября 2026'
    progress.assert_called_once()


def test_cancel_expire_new_command_and_restart_invalidate_image(tmp_path):
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    preview = image_preview(app)
    app.handle_callback('101', preview['buttons'][0][1]['callback_data'])
    assert 'устарело' in app.handle_callback('101', confirmation(preview))['text']
    preview = image_preview(app)
    app.now = lambda: NOW + timedelta(minutes=11)
    assert 'устарело' in app.handle_callback('101', confirmation(preview))['text']
    app.now = lambda: NOW
    preview = image_preview(app)
    app.handle_text('101', '/start')
    assert 'устарело' in app.handle_callback('101', confirmation(preview))['text']
    preview = image_preview(app)
    restored = application(tmp_path, app.adapter)
    assert 'устарело' in restored.handle_callback('101', confirmation(preview))['text']
    assert not app.image_path.exists() and app.adapter.writes == []


@pytest.mark.parametrize('changes', [
    {'start': 'понедельник'}, {'end': '2026-09-24T09:00'}, {'summary': ''},
    {'end': '2026-09-26T11:30'}, {'start': '2026-09-24T10:00+07:00'},
])
def test_bad_extraction_never_accepted(changes):
    with pytest.raises(ImageScheduleError):
        validate_extraction(payload(**changes), 'Asia/Tomsk')


def test_clarification_and_no_events_do_not_import(tmp_path):
    with pytest.raises(ImageScheduleError, match='неделю'):
        validate_extraction({'clarification': 'Укажи неделю', 'events': []}, 'Asia/Tomsk')
    with pytest.raises(ImageScheduleError):
        validate_extraction({'clarification': '', 'events': []}, 'Asia/Tomsk')
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    image_preview(app)
    app.image_reader.extract.side_effect = ImageScheduleError('Укажи неделю')
    assert not app.handle_image('101', b'image')['buttons']
    assert app.image_pending is None and not app.image_path.exists()


def test_vision_request_carries_caption_image_local_time_and_validates(monkeypatch):
    import json
    response = Mock()
    response.status_code = 200
    response.json.return_value = {'choices': [{'finish_reason': 'stop',
        'message': {'content': json.dumps(payload())}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr('services.image_schedule.requests.post', post)
    rows = ImageScheduleReader(api_key='synthetic').extract(
        b'\x89PNG\r\n\x1a\nimage', '21–27 сентября 2026', now=NOW, timezone='Asia/Tomsk')
    assert rows[0]['start'].utcoffset() == timedelta(hours=7)
    request = post.call_args.kwargs['json']
    assert post.call_args.args[0] == 'https://openrouter.ai/api/v1/chat/completions'
    assert request['model'] == 'google/gemma-4-26b-a4b-it:free'
    assert request['max_tokens'] == 12000
    assert 'models' not in request and request['reasoning'] == {'enabled': False}
    assert request['provider']['max_price'] == {'prompt': 0, 'completion': 0, 'image': 0}
    assert request['messages'][1]['content'][0]['text'] == '21–27 сентября 2026'
    assert request['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert '2026-09-22' in request['messages'][0]['content']
    response.json.return_value['choices'][0]['finish_reason'] = 'length'
    with pytest.raises(ImageScheduleError, match='целиком'):
        ImageScheduleReader(api_key='synthetic').extract(b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')


def test_generic_primary_429_uses_one_free_router_attempt_with_same_image_and_zero_price(monkeypatch):
    import json
    busy = Mock(status_code=429)
    busy.json.return_value = {'error': {'message': 'provider rate limit'}}
    accepted = Mock(status_code=200)
    accepted.json.return_value = {'choices': [{'finish_reason': 'stop',
        'message': {'content': json.dumps(payload())}}]}
    post = Mock(side_effect=[busy, accepted])
    monkeypatch.setattr('services.image_schedule.requests.post', post)

    rows = ImageScheduleReader(api_key='synthetic').extract(
        b'\xff\xd8\xffimage', '12–18 октября', now=NOW, timezone='Asia/Tomsk')

    assert len(rows) == 1 and post.call_count == 2
    primary, router = [call.kwargs['json'] for call in post.call_args_list]
    assert primary['model'] == 'google/gemma-4-26b-a4b-it:free'
    assert router['model'] == 'openrouter/free'
    assert primary['messages'] == router['messages']
    assert primary['messages'][1]['content'][1]['image_url']['url'] == router['messages'][1]['content'][1]['image_url']['url']
    assert primary['provider']['require_parameters'] is router['provider']['require_parameters'] is True
    assert all(body['provider']['max_price'] == {'prompt': 0, 'completion': 0, 'image': 0}
               for body in (primary, router))


def test_free_router_error_is_not_retried_or_imported(monkeypatch):
    primary_429 = Mock(status_code=429)
    primary_429.json.return_value = {'error': {'message': 'provider rate limit'}}
    router_error = Mock(status_code=503)
    router_error.json.return_value = {'error': {'message': 'private provider detail'}}
    post = Mock(side_effect=[primary_429, router_error])
    monkeypatch.setattr('services.image_schedule.requests.post', post)

    with pytest.raises(ImageScheduleError, match='код 503') as error:
        ImageScheduleReader(api_key='synthetic').extract(
            b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')

    assert post.call_count == 2
    assert 'private provider detail' not in str(error.value)
    assert post.call_args_list[1].kwargs['json']['model'] == 'openrouter/free'


@pytest.mark.parametrize('finish_reason, content', [
    ('stop', 'not-json'), ('length', '{"events":['),
])
def test_bad_free_router_result_never_creates_image_import_preview(
        tmp_path, monkeypatch, finish_reason, content):
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    app.image_reader = ImageScheduleReader(api_key='synthetic')
    busy = Mock(status_code=429)
    busy.json.return_value = {'error': {'message': 'provider rate limit'}}
    router_response = Mock(status_code=200)
    router_response.json.return_value = {'choices': [{
        'finish_reason': finish_reason,
        'message': {'content': content},
    }]}
    post = Mock(side_effect=[busy, router_response])
    monkeypatch.setattr('services.image_schedule.requests.post', post)

    result = app.handle_image('101', b'\xff\xd8\xffimage', '12–18 октября')

    assert 'Расписание не изменено' in result['text']
    assert app.image_pending is None and not app.image_path.exists()
    assert app.adapter.writes == [] and post.call_count == 2
    assert post.call_args_list[1].kwargs['json']['model'] == 'openrouter/free'


def test_paid_models_and_exhausted_free_quota_never_import(monkeypatch):
    post = Mock()
    monkeypatch.setattr('services.image_schedule.requests.post', post)
    with pytest.raises(ImageScheduleError, match='бесплатные'):
        ImageScheduleReader(api_key='synthetic', model='openai/gpt-4.1').extract(
            b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')
    post.assert_not_called()
    post.return_value.status_code = 429
    with pytest.raises(ImageScheduleError, match='частоту'):
        ImageScheduleReader(api_key='synthetic').extract(
            b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')


def test_download_limits_actual_content_and_checks_mime(monkeypatch):
    response = Mock()
    response.json.return_value = {'ok': True, 'result': {'file_path': 'photos/a.jpg'}}
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(return_value=False)
    stream.iter_content.return_value = iter([b'\xff\xd8\xffimage'])
    get = Mock(side_effect=[response, stream])
    monkeypatch.setattr('services.image_schedule.requests.get', get)
    assert download_telegram_image('https://api.telegram.org/botsynthetic',
        {'photo': [{'file_id': 'small', 'width': 20, 'height': 20},
                   {'file_id': 'large', 'width': 500, 'height': 500}]}) == b'\xff\xd8\xffimage'
    assert get.call_args_list[0].kwargs['params']['file_id'] == 'large'
    assert get.call_args_list[1].args[0] == 'https://api.telegram.org/file/botsynthetic/photos/a.jpg'
    with pytest.raises(ImageScheduleError, match='большое'):
        download_telegram_image('unused', {'document': {'file_id': 'x', 'file_size': MAX_IMAGE_BYTES + 1}})
    stream.iter_content.return_value = iter([b'x' * (MAX_IMAGE_BYTES + 1)])
    get.side_effect = [response, stream]
    with pytest.raises(ImageScheduleError, match='большое'):
        download_telegram_image('https://api.telegram.org/botsynthetic', {'document': {'file_id': 'x'}})
    stream.iter_content.return_value = iter([b'not an image'])
    get.side_effect = [response, stream]
    with pytest.raises(ImageScheduleError, match='JPEG'):
        download_telegram_image('https://api.telegram.org/botsynthetic', {'document': {'file_id': 'x'}})


def test_complete_24_lesson_school_week_long_preview_and_confirmation(tmp_path, monkeypatch):
    from services.telegram_schedule_bot import TelegramScheduleBot
    days = ((1, 3, 4, 5), (0, 1, 3, 4, 5), (0, 1, 2, 3, 5), (2, 3, 4, 7, 8), (1, 2, 3, 4, 5))
    rows = []
    for day, slots in enumerate(days):
        for slot in slots:
            start = NOW.replace(day=21, hour=8, minute=30) + timedelta(days=day, hours=slot)
            rows.append(dict(summary='Английский язык — учебное занятие ' + 'с подробным названием ' * 2,
                             start=start.strftime('%Y-%m-%dT%H:%M'),
                             end=(start + timedelta(minutes=40)).strftime('%Y-%m-%dT%H:%M'),
                             location='Учебный корпус, дополнительная информация о месте проведения занятия, аудитория '
                                      + str(day * 10 + slot), event_type='other'))
    extracted = validate_extraction({'clarification': '', 'events': rows}, 'Asia/Tomsk')
    assert len(extracted) == 24
    app = application(tmp_path)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    app.image_reader = Mock()
    app.image_reader.extract.return_value = extracted
    preview = app.handle_image('101', b'image')
    assert len(preview['text']) > 4096 and preview['text'].count('• ') == 24
    assert preview['buttons'][1][0]['text'] == 'Повторять каждую неделю'
    bot = TelegramScheduleBot('synthetic', '101', '', tmp_path / 'unused')
    post = Mock()
    monkeypatch.setattr('services.telegram_schedule_bot.requests.post', post)
    bot.send_message('101', preview['text'], preview['buttons'])
    sent = [call.kwargs['json'] for call in post.call_args_list]
    assert all(len(message['text']) <= bot.TELEGRAM_MESSAGE_LIMIT for message in sent)
    assert sum(message['text'].count('• ') for message in sent) == 24
    assert all('reply_markup' not in message for message in sent[:-1])
    assert sent[-1]['reply_markup']['inline_keyboard'] == preview['buttons']
    app.handle_callback('101', confirmation(preview))
    source = app.source.state_store.load(app.account.id).sources['image-primary']
    assert len(app._source_events(source)) == 24 and app.adapter.writes == []


@pytest.mark.parametrize('status, message, phrase', [
    (429, 'Rate limit exceeded: free-models-per-day', 'Суточный'),
    (429, 'provider rate limit', 'несколько минут'),
    (401, 'secret provider response', 'проверить ключ'),
    (403, 'secret provider response', 'проверить ключ'),
    (503, 'secret provider response', 'код 503'),
])
def test_provider_errors_are_actionable_and_never_echo_raw_response(monkeypatch, status, message, phrase):
    response = Mock(status_code=status)
    response.json.return_value = {'error': {'message': message}}
    monkeypatch.setattr('services.image_schedule.requests.post', Mock(return_value=response))
    with pytest.raises(ImageScheduleError, match=phrase) as error:
        ImageScheduleReader(api_key='synthetic').extract(b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')
    assert 'secret provider response' not in str(error.value)


def test_free_model_markdown_json_and_bad_json(monkeypatch):
    import json
    response = Mock(status_code=200)
    choice = {'finish_reason': 'stop', 'message': {'content': '```json\n' + json.dumps(payload()) + '\n```'}}
    response.json.return_value = {'choices': [choice]}
    monkeypatch.setattr('services.image_schedule.requests.post', Mock(return_value=response))
    read = ImageScheduleReader(api_key='synthetic')
    assert len(read.extract(b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')) == 1
    choice['message']['content'] = 'not json secret response'
    with pytest.raises(ImageScheduleError, match='нечитаемый'):
        read.extract(b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')


def test_rejected_parameters_retry_once_with_single_free_model_and_plain_json(monkeypatch):
    import json
    rejected = Mock(status_code=400)
    rejected.json.return_value = {'error': {'message': 'Unsupported response_format'}}
    accepted = Mock(status_code=200)
    accepted.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(payload())}}]}
    post = Mock(side_effect=[rejected, accepted])
    monkeypatch.setattr('services.image_schedule.requests.post', post)
    rows = ImageScheduleReader(api_key='synthetic').extract(b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')
    assert len(rows) == 1 and post.call_count == 2
    original, compatible = [call.kwargs['json'] for call in post.call_args_list]
    assert original['response_format'] == {'type': 'json_object'}
    assert 'response_format' not in compatible
    assert compatible['model'] == 'openrouter/free'
    assert original['messages'] == compatible['messages']
    assert all('models' not in body for body in (original, compatible))
    assert original['reasoning'] == {'enabled': False}
    assert 'reasoning' not in compatible
    assert all(body['provider']['max_price'] == {'prompt': 0, 'completion': 0, 'image': 0}
               for body in (original, compatible))


def test_daily_quota_never_triggers_another_request(monkeypatch):
    response = Mock(status_code=429)
    response.json.return_value = {'error': {'message': 'Rate limit exceeded: free-models-per-day'}}
    post = Mock(return_value=response)
    monkeypatch.setattr('services.image_schedule.requests.post', post)
    with pytest.raises(ImageScheduleError, match='Суточный'):
        ImageScheduleReader(api_key='synthetic').extract(b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')
    assert post.call_count == 1


def test_truncated_completion_retries_other_model_without_importing_partial_json(monkeypatch):
    import json
    truncated = Mock(status_code=200)
    truncated.json.return_value = {'choices': [{'finish_reason': 'length',
        'message': {'content': json.dumps(payload(summary='Partial lesson'))}}]}
    complete = Mock(status_code=200)
    complete.json.return_value = {'choices': [{'finish_reason': 'stop',
        'message': {'content': json.dumps(payload(summary='Complete lesson'))}}]}
    post = Mock(side_effect=[truncated, complete])
    monkeypatch.setattr('services.image_schedule.requests.post', post)
    rows = ImageScheduleReader(api_key='synthetic', model='openrouter/free').extract(
        b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')
    assert [row['summary'] for row in rows] == ['Complete lesson']
    bodies = [call.kwargs['json'] for call in post.call_args_list]
    assert len(bodies) == 2 and bodies[0]['model'] != bodies[1]['model']
    assert bodies[0]['model'] == 'google/gemma-4-26b-a4b-it:free'
    assert bodies[1]['model'] == 'openrouter/free'
    assert all(body['reasoning'] == {'enabled': False} for body in bodies)


def test_both_truncated_completions_fail_closed_and_retries_are_bounded(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {'choices': [{'finish_reason': 'length',
                                              'message': {'content': 'partial'}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr('services.image_schedule.requests.post', post)
    with pytest.raises(ImageScheduleError, match='целиком'):
        ImageScheduleReader(api_key='synthetic').extract(
            b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')
    assert post.call_count == 2


def test_400_diagnostics_are_classified_without_leaking_upstream_text(monkeypatch, caplog):
    response = Mock(status_code=400)
    response.json.return_value = {'error': {'message': 'No endpoint matching data policy',
                                          'metadata': {'raw': 'private API secret image transcript'}}}
    post = Mock(return_value=response)
    monkeypatch.setattr('services.image_schedule.requests.post', post)
    with pytest.raises(ImageScheduleError, match='конфиденциальности') as error:
        ImageScheduleReader(api_key='synthetic').extract(b'\xff\xd8\xffimage', '', now=NOW, timezone='Asia/Tomsk')
    assert post.call_count == 2
    assert 'private API secret image transcript' not in str(error.value) + caplog.text


def test_invited_transport_rejects_foreign_media_before_download_and_routes_caption(tmp_path, monkeypatch):
    bot, calendars, _ = setup(tmp_path)
    progress = Mock()
    monkeypatch.setattr(bot, 'send_message', progress)
    bot.invites.set_access('101', True)
    bot.process_update(envelope(1, data='ob:timezone:Asia/Tomsk'))
    app = bot.applications['101'][1]
    reader(app)
    download = Mock(return_value=b'image')
    monkeypatch.setattr('services.invited_beta_runtime.download_telegram_image', download)
    media = envelope(2)
    media['message'].pop('text')
    media['message'].update(document={'file_id': 'x'}, caption='21–27 сентября 2026')
    foreign = envelope(2, chat='202')
    foreign['message']['photo'] = [{'file_id': 'foreign'}]
    assert bot.process_update(foreign) is None
    progress.assert_not_called()
    download.assert_not_called()
    preview = bot.process_update(media)
    assert preview['buttons']
    progress.assert_called_once_with('101', 'Распознаю расписание… Проверю все занятия перед добавлением.')
    assert app.image_reader.extract.call_args.args[1] == '21–27 сентября 2026'
    bot.process_update(envelope(3, data=confirmation(preview)))
    assert app.image_path.exists() and not calendars['101'].writes
