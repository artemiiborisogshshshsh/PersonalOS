from copy import deepcopy

import pytest

from tests.test_legacy_preparation_commands import legacy_runtime


@pytest.mark.parametrize('command', ['/preparations', '/work_schedule'])
def test_saved_preparation_read_failure_is_actionable_without_writes(legacy_runtime, command):
    class ReadDenied(Exception):
        status_code = 403

    def check(bot, adapter, remote):
        bot.handle_text('123', '/work_schedule')
        bot.work_route_apply(legacy_runtime.lessons[0].id, 'direct')
        bot.handle_text('123', '/work_schedule')
        before = deepcopy(remote)
        saved = {path: path.read_bytes() for path in legacy_runtime.path.rglob('*draft_operations.json')}
        assert saved
        reader = adapter.get_event_by_id.side_effect

        def denied(calendar, identifier, **kwargs):
            event = reader(calendar, identifier, **kwargs)
            if event and event.get('extendedProperties', {}).get('private', {}).get('personal_os_block_id'):
                raise ReadDenied('PRIVATE_EVENT https://private.test/?token=SECRET')
            return event

        adapter.get_event_by_id.side_effect = denied
        adapter.reset_mock()
        reply = bot.handle_text('123', command)
        assert 'доступ' in reply['text'].lower()
        assert '403' in reply['text']
        assert reply['buttons'] == []
        assert all(word not in reply['text'] for word in ('PRIVATE_EVENT', 'private.test', 'SECRET'))
        assert remote == before
        assert all(path.read_bytes() == content for path, content in saved.items())
        adapter._insert_event.assert_not_called()
        adapter._update_event.assert_not_called()
        adapter._delete_event.assert_not_called()

    legacy_runtime.run(check)


@pytest.mark.parametrize('command', ['/preparations', '/work_schedule'])
def test_inaccessible_preparation_calendar_preserves_saved_plan(legacy_runtime, command):
    from adapters.google_calendar_adapter import GoogleCalendarAdapter
    from googleapiclient.errors import HttpError
    from httplib2 import Response
    from unittest.mock import Mock

    def check(bot, adapter, remote):
        bot.handle_text('123', '/work_schedule')
        bot.work_route_apply(legacy_runtime.lessons[0].id, 'direct')
        bot.handle_text('123', '/work_schedule')
        before = deepcopy(remote)
        saved = {path: path.read_bytes() for path in legacy_runtime.path.rglob('*draft_operations.json')}
        assert saved
        real_adapter = GoogleCalendarAdapter()
        real_adapter.service = Mock()
        api = real_adapter.service.events.return_value
        api.get.return_value.execute.side_effect = HttpError(Response({'status': '404'}), b'PRIVATE')
        api.list.return_value.execute.side_effect = HttpError(Response({'status': '404'}), b'PRIVATE')
        adapter.get_event_by_id.side_effect = real_adapter.get_event_by_id
        adapter.reset_mock()
        reply = bot.handle_text('123', command)
        assert 'календарь подготовок недоступен' in reply['text']
        assert '404' in reply['text']
        assert 'PRIVATE' not in reply['text']
        assert remote == before
        assert all(path.read_bytes() == data for path, data in saved.items())
        adapter._insert_event.assert_not_called()
        adapter._update_event.assert_not_called()
        adapter._delete_event.assert_not_called()

    legacy_runtime.run(check)
