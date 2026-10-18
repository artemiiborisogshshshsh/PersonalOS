from icalendar import Calendar as ICalendar, Event
from unittest.mock import Mock

from tests.test_closed_beta_runtime import application


def _files(path):
    return {item.relative_to(path): item.read_bytes()
            for item in path.rglob('*') if item.is_file()}


def _event(uid, summary, *, weekly=False):
    event = Event()
    event.add('uid', uid)
    event.add('summary', summary)
    if weekly:
        event.add('x-personal-os-weekly', 'TRUE')
    return event


def test_image_status_missing_file_reports_zero_without_state_or_calendar_changes(tmp_path):
    app = application(tmp_path)
    app.pending = ('pending-confirmation',)
    app.image_pending = ('pending-image',)
    before = _files(app.directory)
    app.adapter.list_visible_calendars = Mock(side_effect=AssertionError('must not read Calendar'))

    result = app.handle_text('101', '/image_status@pilot')

    assert 'еженедельных шаблонов — 0' in result['text']
    assert 'разовых занятий — 0' in result['text']
    assert 'Google Calendar не изменён' in result['text']
    assert app.pending == ('pending-confirmation',)
    assert app.image_pending == ('pending-image',)
    assert not app.adapter.writes
    assert _files(app.directory) == before
    app.adapter.list_visible_calendars.assert_not_called()


def test_image_status_counts_weekly_templates_and_dated_events_only(tmp_path):
    app = application(tmp_path)
    calendar = ICalendar()
    calendar.add('version', '2.0')
    calendar.add_component(_event('weekly-secret-uid', 'Private weekly title', weekly=True))
    calendar.add_component(_event('dated-secret-uid', 'Private dated title'))
    calendar.add_component(_event('weekly-second', 'Another weekly title', weekly=True))
    app.image_path.parent.mkdir(parents=True, exist_ok=True)
    app.image_path.write_bytes(calendar.to_ical())
    before = _files(app.directory)

    result = app.handle_text('101', '/image_status')

    assert 'еженедельных шаблонов — 2' in result['text']
    assert 'разовых занятий — 1' in result['text']
    assert 'weekly-secret-uid' not in result['text']
    assert 'Private weekly title' not in result['text']
    assert 'dated-secret-uid' not in result['text']
    assert _files(app.directory) == before
    assert not app.adapter.writes


def test_image_status_foreign_chat_has_no_response_or_side_effects(tmp_path):
    app = application(tmp_path)
    app.image_path.parent.mkdir(parents=True, exist_ok=True)
    app.image_path.write_bytes(b'not inspected for another chat')
    before = _files(app.directory)

    assert app.handle_text('202', '/image_status') is None

    assert _files(app.directory) == before
    assert not app.adapter.writes


def test_image_status_malformed_ics_fails_closed_without_echoing_content(tmp_path, caplog):
    app = application(tmp_path)
    private_payload = b'not-an-ics private-token-and-image-path'
    app.image_path.parent.mkdir(parents=True, exist_ok=True)
    app.image_path.write_bytes(private_payload)
    before = _files(app.directory)

    result = app.handle_text('101', '/image_status')

    assert result['text'] == (
        'Не удалось проверить локальное расписание из фото. Файл и Google Calendar '
        'не изменены. Обратись к оператору.'
    )
    assert 'private-token' not in result['text'] + caplog.text
    assert 'image-path' not in result['text'] + caplog.text
    assert _files(app.directory) == before
    assert not app.adapter.writes
