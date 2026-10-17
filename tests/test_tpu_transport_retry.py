from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests
from icalendar import Calendar, Event

from services.tpu_schedule_source import fetch_tpu_group_schedule
from services.university_schedule_source import (
    UniversityScheduleFetchError,
    fetch_university_schedule,
)


VIEW = 'https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html'
SOURCE = 'https://ro-rasp.tpu.ru/app/modal/source.html?class=export&kalendar=key'
ACTION = 'https://ro-rasp.tpu.ru/app/modal/update.html?class=export&kalendar=key'
FEED = 'https://ro-rasp.tpu.ru/export/ical.html?key=temporary'


def ics_payload():
    calendar = Calendar()
    event = Event()
    event.add('uid', 'class-1')
    event.add('summary', 'Class')
    event.add('dtstart', datetime(2026, 9, 1, 10, tzinfo=timezone.utc))
    event.add('dtend', datetime(2026, 9, 1, 11, tzinfo=timezone.utc))
    calendar.add_component(event)
    return calendar.to_ical()


def ok_response(content):
    response = Mock(content=content)
    response.raise_for_status.return_value = None
    return response


class ScriptedSession:
    def __init__(self, get_results, post_results=()):
        self.get_results = {key: list(values) for key, values in get_results.items()}
        self.post_results = list(post_results)
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        result = self.get_results[url].pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        result = self.post_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def test_feed_get_retries_timeout_once_then_succeeds(tmp_path):
    session = ScriptedSession({
        'https://example.test/feed.ics': [requests.Timeout('private timeout'), ok_response(ics_payload())],
    })

    result = fetch_university_schedule(
        'https://example.test/feed.ics', tmp_path / 'schedule.ics', session=session,
    )

    assert result.event_count == 1
    assert len(session.get_calls) == 2
    assert session.get_calls[0] == session.get_calls[1]


def test_feed_get_second_timeout_is_wrapped_and_preserves_snapshot(tmp_path):
    output = tmp_path / 'schedule.ics'
    output.write_bytes(b'known snapshot')
    session = ScriptedSession({
        'https://example.test/feed.ics': [
            requests.Timeout('private timeout one'), requests.Timeout('private timeout two'),
        ],
    })

    with pytest.raises(UniversityScheduleFetchError) as error:
        fetch_university_schedule('https://example.test/feed.ics', output, session=session)

    assert isinstance(error.value.__cause__, requests.Timeout)
    assert len(session.get_calls) == 2
    assert output.read_bytes() == b'known snapshot'


def test_discovery_gets_retry_timeout_but_export_post_timeout_is_not_retried():
    view_content = (f'<a data-source="{SOURCE}" data-action="{ACTION}">iCal</a>').encode()
    session = ScriptedSession({
        VIEW: [requests.Timeout('private page timeout'), ok_response(view_content)],
        SOURCE: [requests.Timeout('private modal timeout'), ok_response(b'<form>export</form>')],
    }, post_results=[requests.Timeout('private post timeout')])
    with patch('services.tpu_schedule_source.requests.Session', return_value=session):
        with pytest.raises(UniversityScheduleFetchError) as error:
            from services.tpu_schedule_source import discover_tpu_ical_url
            discover_tpu_ical_url(VIEW)

    assert isinstance(error.value.__cause__, requests.Timeout)
    assert [url for url, _ in session.get_calls] == [VIEW, VIEW, SOURCE, SOURCE]
    assert len(session.post_calls) == 1


def test_non_timeout_connection_failure_is_not_retried(tmp_path):
    session = ScriptedSession({
        'https://example.test/feed.ics': [requests.ConnectionError('private connection failure')],
    })

    with pytest.raises(UniversityScheduleFetchError):
        fetch_university_schedule('https://example.test/feed.ics', tmp_path / 'schedule.ics', session=session)

    assert len(session.get_calls) == 1


def test_failed_second_staged_feed_retry_keeps_published_snapshot(tmp_path):
    feeds = ['https://ro-rasp.tpu.ru/export/ical.html?key=one',
             'https://ro-rasp.tpu.ru/export/ical.html?key=two']
    session = ScriptedSession({
        feeds[0]: [ok_response(ics_payload())],
        feeds[1]: [requests.Timeout('private timeout one'), requests.Timeout('private timeout two')],
    })
    output = Path(tmp_path / 'schedule.ics')
    output.write_bytes(b'known snapshot')

    with patch('services.tpu_schedule_source.discover_tpu_ical_url', side_effect=feeds):
        with pytest.raises(UniversityScheduleFetchError):
            fetch_tpu_group_schedule(
                VIEW, output, session=session, auto_period=True, export_variant_id=2,
                now=datetime(2026, 9, 1, tzinfo=timezone.utc),
            )

    assert len(session.get_calls) == 3
    assert output.read_bytes() == b'known snapshot'
