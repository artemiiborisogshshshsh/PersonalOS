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
