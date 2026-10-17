from unittest.mock import Mock

import pytest

from adapters import google_calendar_adapter as adapter_module
from adapters.google_calendar_adapter import GoogleCalendarAdapter, CalendarReadRequest


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan'), True])
def test_http_timeout_must_be_positive_and_finite(timeout):
    with pytest.raises(ValueError, match='positive finite'):
        GoogleCalendarAdapter({'http_timeout_seconds': timeout})


@pytest.mark.parametrize(('config', 'expected_timeout'), [({}, 20.0), ({'http_timeout_seconds': 7}, 7.0)])
def test_service_factory_builds_authorized_transport_with_explicit_timeout(
        tmp_path, monkeypatch, config, expected_timeout):
    token_path = tmp_path / 'token.json'
    token_path.write_text('{}')
    creds = Mock(valid=True)
    monkeypatch.setattr(
        adapter_module.Credentials, 'from_authorized_user_file',
        Mock(return_value=creds),
    )
    transport = Mock()
    http_factory = Mock(return_value=transport)
    monkeypatch.setattr(adapter_module.httplib2, 'Http', http_factory)
    authorized_http = Mock()
    auth_factory = Mock(return_value=authorized_http)
    monkeypatch.setattr(adapter_module.google_auth_httplib2, 'AuthorizedHttp', auth_factory)
    service = Mock()
    build = Mock(return_value=service)
    monkeypatch.setattr(adapter_module, 'build', build)

    adapter = GoogleCalendarAdapter({'token_path': str(token_path), **config})
    assert adapter._get_calendar_service() is service

    http_factory.assert_called_once_with(timeout=expected_timeout)
    auth_factory.assert_called_once_with(creds, http=transport)
    build.assert_called_once_with(
        'calendar', 'v3', http=authorized_http,
        requestBuilder=CalendarReadRequest,
    )

