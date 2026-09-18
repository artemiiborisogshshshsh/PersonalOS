import ssl
from unittest.mock import Mock, patch

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from adapters.google_calendar_adapter import CalendarReadRequest
from services.sync_retry import retry_read


def test_read_reconnects_and_succeeds():
    action = Mock(side_effect=[ssl.SSLEOFError('eof'), 'ok'])
    close, sleep = Mock(), Mock()
    assert retry_read(action, reconnect=close, sleep=sleep, jitter=lambda: 1) == 'ok'
    close.assert_called_once()
    sleep.assert_called_once_with(1.0)


def test_ten_attempts_and_bounded_backoff():
    action, sleep = Mock(side_effect=ssl.SSLError('bad record')), Mock()
    with pytest.raises(ssl.SSLError):
        retry_read(action, sleep=sleep, jitter=lambda: 1)
    assert action.call_count == 10
    assert sleep.call_count == 9
    assert max(call.args[0] for call in sleep.call_args_list) <= 30


@pytest.mark.parametrize('status', [400, 401, 403])
def test_fatal_errors_are_not_retried(status):
    action = Mock(side_effect=HttpError(Response({'status': status}), b'{}'))
    with pytest.raises(HttpError):
        retry_read(action, sleep=Mock())
    assert action.call_count == 1


def test_post_is_not_retried_even_if_caller_requests_retries():
    request = CalendarReadRequest(Mock(), lambda *args: None, 'https://example.test', method='POST')
    with patch('googleapiclient.http.HttpRequest.execute', side_effect=ssl.SSLError('lost')) as execute:
        with pytest.raises(ssl.SSLError):
            request.execute(num_retries=10)
    execute.assert_called_once_with(http=None, num_retries=0)
