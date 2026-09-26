"""Exercise legacy commands through main with synthetic sources and Calendar."""

import json
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

from models import PersonalEventState, PersonalUniversityEvent
from services.alfacrm_schedule_source import AlfaCRMLesson
from services.calendar.personal_event_sync_service import PersonalEventSyncService
from services.telegram_schedule_bot import TelegramScheduleBot


@pytest.fixture
def legacy_runtime(tmp_path):
    from scripts import telegram_schedule_bot as runtime

    now = datetime.now(ZoneInfo('Asia/Tomsk'))
    start = (now + timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0)
    lessons = [AlfaCRMLesson('7:1', 'Synthetic work', start, start + timedelta(hours=1),
                            subject='Synthetic work', group='Synthetic group')]
    university = [PersonalUniversityEvent('synthetic-study', 'Synthetic study (ЛК)', '',
        start.replace(hour=10), start.replace(hour=11), state=PersonalEventState.CONFIRMED,
        metadata={'course': 'Synthetic study', 'session_type': 'lecture'})]
    adapter = Mock(is_initialized=True)
    adapter.service = object()
    names = {'Personal University Schedule': 'study', 'Работа': 'work', 'Личное': 'personal'}
    adapter._get_or_create_calendar.side_effect = names.__getitem__
    adapter.list_visible_calendars.return_value = [
        {'id': value, 'summary': name} for name, value in names.items()]
    remote = {('personal', 'manual'): dict(
        id='manual', summary='Synthetic manual event', description='User-owned',
        start={'dateTime': start.replace(hour=12).isoformat()},
        end={'dateTime': start.replace(hour=13).isoformat()})}

    def write(data, calendar, identifier=None, **kwargs):
        identifier = identifier or f'event-{len(remote)}'
        remote[calendar, identifier] = dict(id=identifier, iCalUID=data.uid,
            summary=data.summary, description=data.description,
            start={'dateTime': data.dtstart.isoformat()}, end={'dateTime': data.dtend.isoformat()},
            extendedProperties={'private': {
                'personal_os_block_id': getattr(data, 'system_block_id', ''),
                'personal_os_source_event_id': getattr(data, 'system_source_event_id', ''),
                'personal_os_operation_id': getattr(data, 'system_operation_id', ''),
            }})
        return identifier

    adapter._insert_event.side_effect = write
    adapter._update_event.side_effect = lambda calendar, identifier, data, **kwargs: write(
        data, calendar, identifier, **kwargs)
    adapter.event_exists_by_uid.side_effect = lambda calendar, uid: next(
        (identifier for (cal, identifier), event in remote.items()
         if cal == calendar and event.get('iCalUID') == uid), None)
    adapter.get_event_by_uid.side_effect = lambda calendar, uid, **kwargs: next(
        (event for (cal, _), event in remote.items()
         if cal == calendar and event.get('iCalUID') == uid), None)
    adapter.get_event_by_id.side_effect = lambda calendar, identifier, **kwargs: remote.get((calendar, identifier))
    adapter.list_events_in_calendar.side_effect = lambda calendar, *args: [
        deepcopy(event) for (cal, _), event in remote.items() if cal == calendar]
    output = tmp_path / 'schedule.ics'
    output.write_text('BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n')
    bot = TelegramScheduleBot('synthetic-token', '123', 'https://example.test/feed', output,
        fetcher=lambda *_: SimpleNamespace(content_hash='synthetic', event_count=1))
    environment = dict(ALFACRM_BASE_URL='https://crm.example.test', ALFACRM_BRANCH_ID='7',
        ALFACRM_EMAIL='test@example.test', ALFACRM_API_KEY='synthetic-key', ALFACRM_TEACHER_ID='1')

    def run(callback):
        with patch.dict('os.environ', environment, clear=True), \
             patch('sys.argv', ['bot', '--source-url', 'https://example.test/feed', '--output', str(output)]), \
             patch.object(runtime.TelegramScheduleBot, 'from_environment', return_value=bot), \
             patch.object(runtime, 'migrate_missing_user_state', return_value=[]), \
             patch.object(runtime, 'personal_schedule_events', return_value=university), \
             patch.object(runtime, 'load_events', return_value=[]), \
             patch.object(runtime, 'create_personal_event_sync_service',
                 return_value=PersonalEventSyncService(calendar_adapter=adapter)), \
             patch.object(runtime.AlfaCRMScheduleSource, 'fetch', return_value=lessons), \
             patch.object(bot, 'run_forever', side_effect=lambda: callback(bot, adapter, remote)):
            runtime.main()

    return SimpleNamespace(run=run, lessons=lessons, university=university, path=tmp_path)


def test_legacy_commands_complete_routes_then_publish_shared_preparations(legacy_runtime):
    def check(bot, adapter, remote):
        source_snapshot = deepcopy((legacy_runtime.lessons, legacy_runtime.university))
        manual = deepcopy(remote['personal', 'manual'])
        route = bot.handle_text('123', '/work_schedule')
        assert 'заедешь домой' in route['text']
        assert legacy_runtime.lessons[0].start.strftime('%d.%m %H:%M') in route['text']
        assert bot.work_route_apply('7:1', 'direct').startswith('Маршрут сохранён')
        work = bot.handle_text('123', '/work_schedule')
        assert 'Субботняя очередь' in work['text']
        assert any('AI Calendar Block:' in event['description'] for event in remote.values())
        adapter.reset_mock()
        preparations = bot.handle_text('123', '/preparations')
        assert 'не изменился' in preparations['text']
        adapter._insert_event.assert_not_called()
        adapter._update_event.assert_not_called()
        adapter._delete_event.assert_not_called()
        assert remote['personal', 'manual'] == manual
        assert (legacy_runtime.lessons, legacy_runtime.university) == source_snapshot

    legacy_runtime.run(check)


def test_legacy_preparations_wait_for_route_when_university_touches_work(legacy_runtime):
    legacy_runtime.university[0].end_time = legacy_runtime.lessons[0].start

    def check(bot, adapter, remote):
        source_snapshot = deepcopy((legacy_runtime.lessons, legacy_runtime.university))
        manual = deepcopy(remote['personal', 'manual'])
        route = bot.handle_text('123', '/work_schedule')
        assert 'заедешь домой' in route['text']

        preparation = bot.handle_text('123', '/preparations')
        text = preparation['text'] if isinstance(preparation, dict) else preparation
        assert 'маршруте' in text
        assert 'Не удалось обработать команду' not in text
        assert not any(event.get('extendedProperties', {}).get('private', {}).get(
            'personal_os_block_id', '').startswith('work-prep:') for event in remote.values())
        assert (legacy_runtime.lessons, legacy_runtime.university) == source_snapshot
        assert remote['personal', 'manual'] == manual
        projected_work = next(event for event in remote.values()
            if event.get('iCalUID') == 'personal-os:work:7:1')
        assert projected_work['start']['dateTime'] == legacy_runtime.lessons[0].start.isoformat()
        assert projected_work['end']['dateTime'] == legacy_runtime.lessons[0].end.isoformat()
        assert bot.work_route_apply('7:1', 'direct').startswith('Маршрут сохранён')
        completed = bot.handle_text('123', '/work_schedule')
        assert 'Субботняя очередь' in completed['text']
        assert (legacy_runtime.lessons, legacy_runtime.university) == source_snapshot
        assert remote['personal', 'manual'] == manual

    legacy_runtime.run(check)


@pytest.mark.parametrize('command', ['/work_schedule', '/preparations'])
@pytest.mark.parametrize('journal_state, expected', [
    ({'version': 1, 'phase': 'work'}, 'Незавершённый старый план'),
    ({'version': 999, 'phase': 'work'}, 'Неизвестный формат журнала'),
    ({'version': 2, 'phase': 'study', 'candidates': [
        {'id': 'saved-study', 'blocks': []}, {'id': 'saved-work', 'blocks': []},
    ]}, 'Условия незавершённого плана изменились'),
])
def test_legacy_commands_explain_blocked_journal_without_replacing_it(
        legacy_runtime, command, journal_state, expected):
    def check(bot, adapter, remote):
        bot.handle_text('123', '/work_schedule')
        bot.work_route_apply('7:1', 'direct')
        user_dir = next((legacy_runtime.path / 'users').iterdir())
        journal = user_dir / 'shared_preparation.json'
        journal.write_text(json.dumps({**journal_state, 'private': 'DO_NOT_EXPOSE'}))
        before_journal = journal.read_bytes()
        before_remote = deepcopy(remote)
        adapter.reset_mock()
        reply = bot.handle_text('123', command)
        text = reply['text'] if isinstance(reply, dict) else reply
        assert expected in text
        assert 'разработчику' in text
        assert 'DO_NOT_EXPOSE' not in text
        assert journal.read_bytes() == before_journal
        assert remote == before_remote
        adapter._insert_event.assert_not_called()
        adapter._update_event.assert_not_called()
        adapter._delete_event.assert_not_called()
        assert not reply['buttons']
        if command == '/work_schedule':
            assert 'Календарь «Работа»' in text

    legacy_runtime.run(check)
