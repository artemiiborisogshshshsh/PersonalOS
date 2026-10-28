"""Release surface: schedule OCR deferred, note photos kept, source rows preserved."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from models import EventType, UniversityEvent
from services.closed_beta_runtime import ClosedBetaApplication, PilotBot, PilotSource
from services.image_schedule import merged_image_calendar, validate_extraction
from services.invited_beta_runtime import InvitedBot
from tests.test_closed_beta_runtime import Calendar, ICS, NOW, URL, onboard
from tests.test_image_schedule import payload
from tests.test_invited_beta import envelope


def app_factory(root, chat='101', adapter=None):
    def fetch(url, output, **kwargs):
        assert url == URL
        output.write_bytes(ICS)
    return ClosedBetaApplication(root, chat, adapter or Calendar(), enable_plan_updates=True,
        source_factory=lambda store: PilotSource(store, fetcher=fetch), now=lambda: NOW)


def notes_app(root):
    app = app_factory(root)
    onboard(app)
    app.handle_text('101', '/weekly_preview')
    app.learning.refresh()
    lesson = app.learning.store.lessons()[0]
    app.learning.store.set_context(lesson['id'])
    return app, lesson


def test_production_default_defers_import_direct_bytes_and_old_callbacks_before_provider(tmp_path, monkeypatch):
    app = app_factory(tmp_path)
    assert app.enable_schedule_ocr is False
    provider = Mock(side_effect=AssertionError('Deferred OCR must not call a provider'))
    monkeypatch.setattr(app.image_reader, 'extract', provider)
    monkeypatch.setattr('services.image_schedule.requests.post', provider)
    assert '/import_schedule' not in app.HELP
    assert 'Пришли фото расписания' not in app.HELP
    assert '/image_status' in app.HELP and '/stop_image_repeats' in app.HELP
    assert 'следующее обновление' in app.handle_text('101', '/import_schedule')['text']
    assert not app.learning._state().get('schedule_image')
    assert 'следующее обновление' in app.handle_image('101', b'private-image', 'расписание')['text']
    for data in ['img:confirm:old', 'img:weekly:old', 'img:edit:old', 'img:cancel:old']:
        app.image_pending = ('old', NOW + timedelta(minutes=10), [], 'Asia/Tomsk')
        assert 'следующее обновление' in app.handle_callback('101', data)['text']
        assert app.image_pending is None
    assert not app.image_path.exists()
    assert not app.adapter.writes
    provider.assert_not_called()


@pytest.mark.parametrize('transport', ['pilot', 'invited'])
def test_explicit_schedule_photo_is_deferred_before_transport_download(tmp_path, monkeypatch, transport):
    download = Mock(side_effect=AssertionError('Deferred schedule must not download media'))
    monkeypatch.setattr('services.closed_beta_runtime.download_telegram_image', download)
    monkeypatch.setattr('services.invited_beta_runtime.download_telegram_image', download)
    photo = {'photo': [{'file_id': 'private-schedule'}], 'caption': 'Расписание на неделю'}
    if transport == 'pilot':
        app = app_factory(tmp_path)
        bot = PilotBot('synthetic', '101', app)
        result = bot.handle_image('101', photo)
    else:
        bot = InvitedBot('synthetic', tmp_path, lambda chat: Calendar(), application_factory=app_factory)
        bot.invites.set_access('101', True)
        update = envelope(1)
        update['message'].update(photo)
        result = bot.process_update(update)
        app = bot.applications['101'][1]
    assert 'следующее обновление' in result['text']
    assert not app.image_path.exists()
    assert not app.learning.store.lessons()
    download.assert_not_called()


def test_explicit_schedule_attachment_never_becomes_note_or_pending_material(tmp_path):
    app, lesson = notes_app(tmp_path)
    before = app.learning.store.get_lesson(lesson['id'])
    media = {'photo': [{'file_id': 'private-schedule'}], 'caption': 'Фото расписания'}
    assert 'следующее обновление' in app.handle_attachment('101', media)['text']
    assert 'следующее обновление' in app.learning.handle_attachment(media)['text']
    assert app.learning.store.get_lesson(lesson['id']) == before
    assert not app.learning._state().get('selection')


@pytest.mark.parametrize('transport', ['pilot', 'invited'])
def test_unlabelled_note_photo_keeps_context_even_with_old_import_flag_and_never_downloads(tmp_path, monkeypatch, transport):
    app, lesson = notes_app(tmp_path / 'app')
    app.learning._save({'schedule_image': True})  # Previous version's unconsumed command.
    download = Mock(side_effect=AssertionError('Note photos use private Telegram file references'))
    monkeypatch.setattr('services.closed_beta_runtime.download_telegram_image', download)
    monkeypatch.setattr('services.invited_beta_runtime.download_telegram_image', download)
    media = {'photo': [{'file_id': 'private-note', 'file_unique_id': 'note-unique'}], 'message_id': 42}
    if transport == 'pilot':
        result = PilotBot('synthetic', '101', app).handle_image('101', media)
    else:
        bot = InvitedBot('synthetic', tmp_path / 'bot', lambda chat: app.adapter,
                         application_factory=lambda root, chat, adapter: app)
        bot.invites.set_access('101', True)
        update = envelope(1)
        update['message'].update(media)
        result = bot.process_update(update)
    assert 'Сохранено' in result['text']
    materials = app.learning.store.get_lesson(lesson['id'])['materials']
    assert len(materials) == 1 and materials[0]['file_id'] == 'private-note'
    assert not app.image_path.exists()
    download.assert_not_called()


def test_unlabelled_photo_without_context_asks_lesson_before_saving(tmp_path):
    app, lesson = notes_app(tmp_path)
    app.learning.store.set_context(None)
    response = app.handle_attachment('101', {'photo': [{'file_id': 'private-note'}]})
    assert 'К какому занятию' in response['text']
    assert response['buttons']
    assert not app.learning.store.get_lesson(lesson['id'])['materials']
    saved = app.handle_callback('101', response['buttons'][0][0]['callback_data'])
    assert 'Сохранено' in saved['text']
    assert app.learning.store.get_lesson(lesson['id'])['materials'][0]['file_id'] == 'private-note'


def test_existing_image_templates_remain_readable_and_can_stop_when_import_is_deferred(tmp_path):
    app = app_factory(tmp_path)
    rows = validate_extraction(payload(), 'Asia/Tomsk')
    app.directory.mkdir(parents=True, exist_ok=True)
    app.image_path.write_bytes(merged_image_calendar(app.image_path, rows, weekly=True))
    assert 'еженедельных шаблонов — 1' in app.handle_text('101', '/image_status')['text']
    assert 'остановлены' in app.handle_text('101', '/stop_image_repeats')['text']
    assert 'еженедельных шаблонов — 0' in app.handle_text('101', '/image_status')['text']
    assert app.image_path.exists()
    assert not app.adapter.writes


def test_parallel_tpu_locations_preserve_all_forty_identities_and_survive_source_reordering(tmp_path, monkeypatch):
    app = app_factory(tmp_path)
    first = UniversityEvent('official-room-108', 'ОС (ЛБ)', '8И41', 'к. 10, ауд. 108',
        NOW + timedelta(days=2), NOW + timedelta(days=2, minutes=95), EventType.LAB, True)
    events = [first, replace(first, uid='official-room-109', location='к. 10, ауд. 109')]
    events += [replace(first, uid=f'official-{index}', dtstart=first.dtstart + timedelta(days=index + 1),
                       dtend=first.dtend + timedelta(days=index + 1)) for index in range(38)]
    monkeypatch.setattr(app.source, 'events', lambda source: list(events))
    source = SimpleNamespace(id='tpu-primary')
    before = {row.uid: row for row in app._source_events(source)}
    assert len(before) == 40
    assert before['official-room-108'].location != before['official-room-109'].location
    events.reverse()
    assert {row.uid: row for row in app._source_events(source)} == before


def test_same_title_and_time_different_lesson_kinds_are_not_collapsed(tmp_path, monkeypatch):
    app = app_factory(tmp_path)
    lecture = UniversityEvent('official-lecture', 'ОС (ЛК)', '', 'Ауд. 108', NOW,
        NOW + timedelta(minutes=95), EventType.LECTURE, True)
    lab = replace(lecture, uid='official-lab', summary='ОС (ЛБ)', event_type=EventType.LAB)
    monkeypatch.setattr(app.source, 'events', lambda source: [lecture, lab])
    assert {row.uid for row in app._source_events(SimpleNamespace(id='tpu-primary'))} == {'official-lecture', 'official-lab'}
