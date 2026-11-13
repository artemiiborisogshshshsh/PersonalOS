from datetime import datetime, timezone
import logging
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
    with patch('services.tpu_schedule_source._TpuSession', return_value=session):
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


def test_tpu_session_rotates_failed_proxy_and_reuses_working_one():
    from services.tpu_schedule_source import _TpuSession

    response = ok_response(b'ok')
    with patch('services.tpu_schedule_source._TPU_PROXIES', ['http://one:80', 'http://two:80']):
        with patch.object(requests.Session, 'request', side_effect=[requests.exceptions.ProxyError(), response, response]) as request:
            session = _TpuSession()
            assert session.get(VIEW, timeout=20) is response
            assert session.post(ACTION, timeout=20) is response
    assert [call.kwargs['proxies']['https'] for call in request.call_args_list] == [
        'http://one:80', 'http://two:80', 'http://two:80',
    ]
    assert session.trust_env is False


def test_tpu_session_never_falls_back_to_direct_connection():
    from services.tpu_schedule_source import _TpuSession

    with patch('services.tpu_schedule_source._TPU_PROXIES', ['http://one:80', 'http://two:80']):
        with patch.object(requests.Session, 'request', side_effect=requests.exceptions.ProxyError()) as request:
            with pytest.raises(requests.exceptions.ProxyError):
                _TpuSession().get(VIEW, timeout=20)
    assert request.call_count == 2
    assert all(call.kwargs['proxies']['https'] for call in request.call_args_list)


@pytest.mark.parametrize('failed_stage', ['group_get', 'modal_get', 'form_post', 'ics_download'])
def test_tpu_failure_diagnostics_are_stage_specific_and_redact_private_data(
        tmp_path, caplog, failed_stage):
    from services.tpu_schedule_source import discover_tpu_ical_url

    view_content = f'<a data-source="{SOURCE}" data-action="{ACTION}">iCal</a>'.encode()
    private_error = requests.ConnectionError(
        'private-group-41736-secret-key https://private.example/export?token=private-token'
    )
    if failed_stage == 'group_get':
        session = ScriptedSession({VIEW: [private_error]})
        run = lambda: discover_tpu_ical_url(VIEW, session=session, export_label='current')
        export = 'current'
    elif failed_stage == 'modal_get':
        session = ScriptedSession({VIEW: [ok_response(view_content)], SOURCE: [private_error]})
        run = lambda: discover_tpu_ical_url(VIEW, session=session, export_label='next')
        export = 'next'
    elif failed_stage == 'form_post':
        session = ScriptedSession(
            {VIEW: [ok_response(view_content)], SOURCE: [ok_response(b'<form>export</form>')]},
            post_results=[private_error],
        )
        run = lambda: discover_tpu_ical_url(VIEW, session=session)
        export = 'current'
    else:
        session = ScriptedSession({'https://example.test/feed.ics': [private_error]})
        def run():
            with patch('services.tpu_schedule_source.discover_tpu_ical_url',
                       return_value='https://example.test/feed.ics'):
                fetch_tpu_group_schedule(
                    VIEW, tmp_path / 'schedule.ics', session=session,
                )
        export = 'selected'

    caplog.set_level(logging.WARNING, logger='services.tpu_schedule_source')
    with pytest.raises((UniversityScheduleFetchError, requests.ConnectionError)):
        run()
    diagnostic_records = [record for record in caplog.records
                          if record.name == 'services.tpu_schedule_source']
    assert len(diagnostic_records) == 1
    message = diagnostic_records[0].getMessage()
    assert f'stage={failed_stage}' in message
    assert f'export={export}' in message
    assert 'elapsed_ms=' in message and 'error=connection_error' in message
    for private in ('private-group-41736-secret-key', 'private.example', 'private-token'):
        assert private not in message


def test_tpu_http_failure_diagnostic_keeps_only_numeric_status(caplog):
    from services.tpu_schedule_source import discover_tpu_ical_url

    response = Mock(status_code=503)
    error = requests.HTTPError(
        'private-response-body sentinel https://private.example/?key=secret', response=response,
    )
    session = ScriptedSession({VIEW: [error]})
    caplog.set_level(logging.WARNING, logger='services.tpu_schedule_source')

    with pytest.raises(UniversityScheduleFetchError):
        discover_tpu_ical_url(VIEW, session=session)

    message = caplog.records[-1].getMessage()
    assert 'stage=group_get' in message and 'http_status=503' in message
    assert 'private-response-body' not in message and 'private.example' not in message and 'secret' not in message


def test_tpu_read_timeout_rotates_for_get_but_not_post():
    from services.tpu_schedule_source import _TpuSession

    response = ok_response(b'ok')
    with patch('services.tpu_schedule_source._TPU_PROXIES', ['http://one:80', 'http://two:80']):
        with patch.object(requests.Session, 'request', side_effect=[requests.ReadTimeout(), response]) as request:
            session = _TpuSession()
            assert session.get(VIEW, timeout=20) is response
            assert [call.kwargs['proxies']['https'] for call in request.call_args_list] == [
                'http://one:80', 'http://two:80',
            ]
            assert request.call_args.kwargs['timeout'] == (3, 4)
        with patch.object(requests.Session, 'request', side_effect=requests.ReadTimeout()) as request:
            with pytest.raises(requests.ReadTimeout):
                session.post(ACTION, timeout=20)
            assert request.call_count == 1


def test_tpu_connection_error_evicts_proxy_and_refreshes_exhausted_pool():
    from services import tpu_schedule_source as tpu

    first, second = 'http://one:80', 'socks5h://two:1080'
    pool = tpu._ProxyPoolManager()
    response = ok_response(b'ok')
    with patch.object(pool, '_refresh', side_effect=[(first,), (second,)]) as refresh:
        with patch.object(tpu, '_TPU_PROXY_POOL', pool), patch.object(
            requests.Session, 'request', side_effect=[requests.ConnectionError(), response, response]
        ) as request:
            session = tpu._TpuSession()
            assert session.get(VIEW, timeout=20) is response
            assert session.get(VIEW, timeout=20) is response
        assert pool.get() == (second,)
        assert first in pool._bad_until
        assert refresh.call_count == 2
    assert [call.kwargs['proxies']['https'] for call in request.call_args_list] == [first, second, second]


def test_tpu_wrapped_post_read_timeout_marks_proxy_bad_without_replay():
    from urllib3.exceptions import ReadTimeoutError
    from services import tpu_schedule_source as tpu

    proxy = 'socks5h://one:1080'
    pool = tpu._ProxyPoolManager()
    error = requests.ConnectionError(ReadTimeoutError(None, ACTION, 'timed out reading response'))
    with patch.object(pool, '_refresh', return_value=(proxy,)) as refresh:
        with patch.object(tpu, '_TPU_PROXY_POOL', pool), patch.object(
            requests.Session, 'request', side_effect=error
        ) as request:
            with pytest.raises(requests.ConnectionError) as caught:
                tpu._TpuSession().post(ACTION, timeout=20)
            assert caught.value is error
            assert request.call_count == 1
        assert pool.get() == ()
        assert refresh.call_count == 1


def test_tpu_production_falls_back_to_direct_after_proxy_attempt_limit():
    from services import tpu_schedule_source as tpu

    proxies = tuple(f'http://{index}.example:80' for index in range(4))
    pool = tpu._ProxyPoolManager()
    response = ok_response(b'ok')
    with patch.object(pool, '_refresh', return_value=proxies), patch.object(
        tpu, '_TPU_PROXY_POOL', pool
    ), patch.object(requests.Session, 'request', side_effect=[
        requests.ConnectionError(), requests.ConnectionError(), requests.ConnectionError(), response,
    ]) as request:
        session = tpu._TpuSession()
        assert session.get(VIEW, timeout=20) is response

    assert [call.kwargs['proxies'].get('https') for call in request.call_args_list] == [
        *proxies[:3], None,
    ]
    assert session.last_transport == 'direct'


def test_tpu_pool_filters_downloads_validates_candidates_and_caches():
    from services import tpu_schedule_source as tpu

    def http_response(content):
        response = requests.Response()
        response.status_code = 200
        response._content = content
        response._content_consumed = True
        return response

    http_list = http_response(b'http://127.0.0.1:8080\nhttp://1.1.1.1:80\n')
    socks_list = http_response(b'socks5://8.8.8.8:1080\n')
    pool = tpu._ProxyPoolManager()
    with patch.object(requests.Session, 'get', side_effect=[
        http_list, socks_list, http_response(b''), http_response(b''),
    ]) as get:
        expected = {'http://1.1.1.1:80', 'socks5h://8.8.8.8:1080'}
        assert set(pool.get()) == expected
        assert set(pool.get()) == expected
    downloads, checks = get.call_args_list[:2], get.call_args_list[2:]
    assert [call.kwargs['params']['protocol'] for call in downloads] == ['http', 'socks5']
    assert all(call.kwargs['params']['country'] == 'RU' for call in downloads)
    assert {call.kwargs['proxies']['https'] for call in checks} == expected
    assert all(call.args == ('https://ro-rasp.tpu.ru/',) and call.kwargs['timeout'] == (2.5, 3.5)
               for call in checks)


def test_tpu_pool_falls_back_to_file_then_direct_when_no_proxies():
    from services import tpu_schedule_source as tpu

    pool = tpu._ProxyPoolManager()
    response = ok_response(b'ok')
    with patch.object(pool, '_download_candidates', return_value=[]):
        with patch.object(Path, 'read_text', return_value='["http://1.1.1.1:80"]'):
            assert pool.get() == ('http://1.1.1.1:80',)
        pool.mark_bad('http://1.1.1.1:80')
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError), patch.object(
            tpu, '_TPU_PROXY_POOL', pool
        ), patch.object(requests.Session, 'request', return_value=response) as request:
            assert tpu._TpuSession().get(VIEW, timeout=20) is response
            assert request.call_args.kwargs['proxies'] == {}


def test_tpu_pool_expires_and_stops_scheduling_checks_when_full():
    from services import tpu_schedule_source as tpu

    candidates = [f'http://1.1.1.1:{port}' for port in range(1000, 1040)]
    with patch.object(tpu, '_PROXY_CHECK_WORKERS', 2):
        pool = tpu._ProxyPoolManager()
        with patch.object(tpu.time, 'monotonic', return_value=100) as clock, patch.object(
            pool, '_download_candidates', return_value=candidates
        ) as download, patch.object(pool, '_is_live', return_value=True) as check:
            assert len(pool.get()) == 6
            assert 6 <= check.call_count <= 7
            clock.return_value += tpu._PROXY_POOL_TTL - 1
            assert len(pool.get()) == 6
            assert download.call_count == 1
            clock.return_value += 1
            assert len(pool.get()) == 6
            assert download.call_count == 2


def test_empty_tpu_proxy_override_skips_pool_discovery():
    from services import tpu_schedule_source as tpu

    response = ok_response(b'ok')
    with patch.object(tpu, '_TPU_PROXIES', []), patch.object(tpu._TPU_PROXY_POOL, 'get') as pool_get:
        with patch.object(requests.Session, 'request', return_value=response):
            assert tpu._TpuSession().get(VIEW, timeout=20) is response
    pool_get.assert_not_called()
