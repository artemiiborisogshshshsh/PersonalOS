from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from icalendar import Calendar, Event

from services.tpu_schedule_source import (
    discover_tpu_ical_url,
    fetch_tpu_group_schedule,
    alternate_tpu_group_page_url,
    current_tpu_group_page_url,
    normalize_tpu_group_page_url,
    validate_tpu_group_page_url,
)


VIEW_URL = 'https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html'
SOURCE_URL = (
    'https://ro-rasp.tpu.ru/app/modal/source.html?class=export&kalendar=nUgbIg'
)
ICAL_URL = (
    'https://ro-rasp.tpu.ru/export/ical.html?key=nUgbIg&now=2026-09-03'
    '&variant_id=3&sig=private-signature'
)


def ics_payload() -> bytes:
    calendar = Calendar()
    event = Event()
    event.add('uid', 'tpu-event')
    event.add('summary', 'Архитектура ИС (ЛБ)')
    event.add('dtstart', datetime(2026, 9, 3, 10, 25, tzinfo=timezone.utc))
    event.add('dtend', datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc))
    calendar.add_component(event)
    return calendar.to_ical()


def response(content: bytes) -> Mock:
    item = Mock(content=content)
    item.raise_for_status.return_value = None
    return item


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return self.responses[url]

    def post(self, url, data, **_kwargs):
        self.urls.append(url)
        self.post_data = data
        return self.responses[('POST', url)]


def test_discovers_current_ical_link_from_stable_tpu_group_page():
    session = FakeSession({
        VIEW_URL: response(
            b'<a data-source="/app/modal/source.html?class=export&amp;kalendar=nUgbIg">'
            b'\xd0\xa4\xd0\xbe\xd1\x80\xd0\xbc\xd0\xb0\xd1\x82 iCal</a>'
        ),
        SOURCE_URL: response(
            f'<input value="{ICAL_URL}">'.encode(),
        ),
    })

    assert discover_tpu_ical_url(VIEW_URL, session=session) == ICAL_URL
    assert session.urls == [VIEW_URL, SOURCE_URL]


def test_existing_cached_export_does_not_bypass_requested_two_week_range():
    action = 'https://ro-rasp.tpu.ru/app/modal/update.html?class=export&kalendar=nUgbIg'
    session = FakeSession({
        VIEW_URL: response(b'<a data-source="/app/modal/source.html?class=export&amp;kalendar=nUgbIg" '
            b'data-action="/app/modal/update.html?class=export&amp;kalendar=nUgbIg">iCal</a>'),
        SOURCE_URL: response(f'<input value="{ICAL_URL}">'.encode()),
        ('POST', action): response(f'{{"url":"{ICAL_URL}"}}'.encode()),
    })
    assert discover_tpu_ical_url(VIEW_URL, session=session, export_variant_id=2) == ICAL_URL
    assert session.post_data['GroupExportForm[variant_id]'] == '2'


def test_creates_export_link_through_tpu_save_form_when_source_modal_is_a_form():
    action_url = (
        'https://ro-rasp.tpu.ru/app/modal/update.html?class=export&kalendar=nUgbIg'
    )
    escaped_ical_url = ICAL_URL.replace('/', '\\/')
    session = FakeSession({
        VIEW_URL: response(
            b'<a data-source="/app/modal/source.html?class=export&amp;kalendar=nUgbIg" '
            b'data-action="/app/modal/update.html?class=export&amp;kalendar=nUgbIg">'
        ),
        SOURCE_URL: response(b'<form>select an export variant</form>'),
        ('POST', action_url): response(
            f'{{"url":"{escaped_ical_url}"}}'.encode(),
        ),
    })

    assert discover_tpu_ical_url(VIEW_URL, session=session) == ICAL_URL
    assert session.post_data == {
        'GroupExportForm[kalendar]': 'nUgbIg',
        'GroupExportForm[variant_id]': '2',
        'GroupExportForm[agree]': '1',
    }


def test_resolves_relative_ical_url_returned_by_real_tpu_export_response():
    action_url = (
        'https://ro-rasp.tpu.ru/app/modal/update.html?class=export&kalendar=nUgbIg'
    )
    relative_url = '/export/ical.html?key=nUgbIg&now=2026-09-03&sig=private-signature'
    session = FakeSession({
        VIEW_URL: response(
            b'<a data-source="/app/modal/source.html?class=export&amp;kalendar=nUgbIg" '
            b'data-action="/app/modal/update.html?class=export&amp;kalendar=nUgbIg">'
        ),
        SOURCE_URL: response(b'<form>select an export variant</form>'),
        ('POST', action_url): response(
            f'{{"url":"{relative_url}" ,"error":false}}'.encode(),
        ),
    })

    assert discover_tpu_ical_url(VIEW_URL, session=session) == (
        'https://ro-rasp.tpu.ru' + relative_url
    )


def test_accepts_a_tpu_page_pasted_as_a_markdown_link():
    assert normalize_tpu_group_page_url(f'[TPU group]({VIEW_URL})') == VIEW_URL


def test_export_diagnostic_redacts_an_unexpected_ical_link():
    from services.tpu_schedule_source import _safe_export_response_message

    message = _safe_export_response_message(
        f'Error while handling {ICAL_URL}'.encode(),
    )

    assert 'private-signature' not in message
    assert '[скрытая ссылка]' in message


def test_fetch_uses_discovered_link_but_result_does_not_expose_ical_key(tmp_path):
    session = FakeSession({
        VIEW_URL: response(b'<a data-source="/app/modal/source.html?class=export&amp;kalendar=nUgbIg">'),
        SOURCE_URL: response(f'<a href="{ICAL_URL}">download</a>'.encode()),
        ICAL_URL: response(ics_payload()),
    })

    result = fetch_tpu_group_schedule(VIEW_URL, tmp_path / 'schedule.ics', session=session)

    assert result.source_url == VIEW_URL
    assert 'private-signature' not in result.source_url
    assert result.event_count == 1
    assert (tmp_path / 'schedule.ics').exists()


def test_auto_period_uses_the_other_page_when_configured_period_is_stale(tmp_path):
    alternate = alternate_tpu_group_page_url(VIEW_URL)
    source_one = 'https://ro-rasp.tpu.ru/app/modal/source.html?class=export&kalendar=one'
    source_two = 'https://ro-rasp.tpu.ru/app/modal/source.html?class=export&kalendar=two'
    ical_one = 'https://ro-rasp.tpu.ru/export/ical.html?key=one'
    ical_two = 'https://ro-rasp.tpu.ru/export/ical.html?key=two'

    def payload(start: datetime) -> bytes:
        calendar = Calendar()
        event = Event()
        event.add('uid', start.isoformat())
        event.add('summary', 'Пара')
        event.add('dtstart', start)
        event.add('dtend', start + timedelta(minutes=90))
        calendar.add_component(event)
        return calendar.to_ical()

    now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    session = FakeSession({
        VIEW_URL: response(
            b'<a data-source="/app/modal/source.html?class=export&amp;kalendar=one">'
        ),
        alternate: response(
            b'<a data-source="/app/modal/source.html?class=export&amp;kalendar=two">'
        ),
        source_one: response(f'<a href="{ical_one}">'.encode()),
        source_two: response(f'<a href="{ical_two}">'.encode()),
        ical_one: response(payload(now - timedelta(days=14))),
        ical_two: response(payload(now + timedelta(days=2))),
    })

    result = fetch_tpu_group_schedule(
        VIEW_URL, tmp_path / 'schedule.ics', session=session,
        auto_period=True, now=now,
    )

    assert result.source_url == alternate
    assert alternate in session.urls
    assert VIEW_URL not in session.urls


@pytest.mark.parametrize('day,week', [(1, 1), (6, 1), (7, 2), (9, 2), (14, 3), (28, 5)])
def test_academic_week_advances_each_monday(day, week):
    now = datetime(2026, 9, day, 10, tzinfo=timezone.utc)
    expected = VIEW_URL.replace('/1/view.html', f'/{week}/view.html')
    assert current_tpu_group_page_url(VIEW_URL, now) == expected
    assert current_tpu_group_page_url(expected, now) == expected


def test_academic_week_uses_tomsk_monday_boundary():
    now = datetime(2026, 9, 6, 17, tzinfo=timezone.utc)
    assert '/2026/2/' in current_tpu_group_page_url(VIEW_URL, now)


@pytest.mark.parametrize('url', [
    'http://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html',
    'https://example.test/gruppa_41736/2026/1/view.html',
    'https://ro-rasp.tpu.ru/user_123/2026/1/view.html',
])
def test_rejects_non_tpu_group_pages(url):
    with pytest.raises(ValueError, match='TPU source'):
        validate_tpu_group_page_url(url)
