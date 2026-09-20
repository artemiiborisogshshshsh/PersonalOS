"""Run the actual Telegram entrypoint with isolated stores and fake providers."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import pytest
from models import PersonalUniversityEvent, PersonalEventState
from services.calendar.personal_event_sync_service import PersonalEventSyncService

from services.alfacrm_schedule_source import AlfaCRMLesson
from services.telegram_schedule_bot import TelegramScheduleBot


@pytest.mark.parametrize('with_university', [False, True, 'floating'])
def test_real_runtime_updates_and_verifies_two_work_preparations(tmp_path, with_university):
    from scripts import telegram_schedule_bot as runtime
    now = datetime.now(ZoneInfo('Asia/Tomsk'))
    start = (now + timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0)
    lessons = [AlfaCRMLesson(f'7:{i}', 'Информатика', start + timedelta(hours=i*2),
               start + timedelta(hours=i*2, minutes=90), subject='Информатика') for i in range(2)]
    university = [PersonalUniversityEvent('uni-test', 'Тест (ЛК)', '',
        (start - timedelta(days=1)).replace(hour=10),
        (start - timedelta(days=1)).replace(hour=11), state=PersonalEventState.CONFIRMED,
        metadata={'course': 'Тест', 'session_type': 'lecture'})] if with_university else []
    if with_university == 'floating':
        university[0].start_time = university[0].start_time.replace(tzinfo=None)
        university[0].end_time = university[0].end_time.replace(tzinfo=None)
    adapter = Mock(is_initialized=True)
    adapter.service = object()
    names = {'Personal University Schedule': 'study', 'Работа': 'work'}
    adapter._get_or_create_calendar.side_effect = lambda name: names[name]
    adapter.list_visible_calendars.return_value = [dict(id=value, summary=key) for key, value in names.items()]
    remote = {}

    def write(data, calendar_id, identifier=None, *, strict=False):
        identifier = identifier or f'event-{len(remote)}'
        remote[(calendar_id, identifier)] = dict(id=identifier, iCalUID=data.uid,
            summary=data.summary, description=data.description,
            start={'dateTime': data.dtstart.isoformat()}, end={'dateTime': data.dtend.isoformat()},
            extendedProperties={'private': {
                'personal_os_block_id': getattr(data, 'system_block_id', ''),
                'personal_os_source_event_id': getattr(data, 'system_source_event_id', ''),
                'personal_os_operation_id': getattr(data, 'system_operation_id', ''),
            }})
        return identifier

    adapter._insert_event.side_effect = write
    adapter._update_event.side_effect = lambda calendar, identifier, data, **kwargs: write(data, calendar, identifier, **kwargs)
    adapter.event_exists_by_uid.side_effect = lambda calendar, uid: next(
        (identifier for (cal, identifier), event in remote.items()
         if cal == calendar and (event['iCalUID'] == uid
            or event['extendedProperties']['private']['personal_os_block_id'] == uid)), None)
    adapter.get_event_by_uid.side_effect = lambda calendar, uid, **kwargs: next(
        (event for (cal, _), event in remote.items()
         if cal == calendar and event['iCalUID'] == uid), None)
    adapter.list_events_in_calendar.side_effect = lambda calendar, *args: [
        event for (cal, _), event in remote.items() if cal == calendar]
    output = tmp_path / 'schedule.ics'
    bot = TelegramScheduleBot('test-token', '123', 'https://example.test/feed', output,
        fetcher=lambda *_: SimpleNamespace(content_hash='test', event_count=0))
    result = {}
    planner_calls = []
    real_work_planner = runtime.WorkPreparationPlanner

    def record_work_planner(*args, **kwargs):
        planner_calls.append(kwargs.copy())
        return real_work_planner(*args, **kwargs)

    def run_bot():
        result['reply'] = bot.handle_text('123', '/update_all')
        assert result['reply']['text'] == 'Проверка завершена, проблем нет.'
        assert bot.handle_text('123', '/deep_work 40').startswith('Настройки сохранены')
        # This callback creates the cached work workflow. It must use the new
        # profile rather than the 120-minute planner built by /update_all.
        bot.handle_text('123', '/work_preparation_feedback')
        result['remote'] = list(remote.values())
        adapter.reset_mock()
        result['second'] = bot.handle_text('123', '/update_all')
        adapter._insert_event.assert_not_called()
        adapter._delete_event.assert_not_called()
        adapter._update_event.assert_not_called()
        removed_key = next(key for key, event in remote.items()
                           if 'AI Calendar Block: work-prep:' in event['description'])
        remote.pop(removed_key)
        result['missing'] = bot.handle_text('123', '/update_all')
        adapter._insert_event.assert_not_called()

    environment = dict(ALFACRM_BASE_URL='https://crm.example.test', ALFACRM_BRANCH_ID='7',
                       ALFACRM_EMAIL='test@example.test', ALFACRM_API_KEY='test-key', ALFACRM_TEACHER_ID='1')
    with patch.dict('os.environ', environment, clear=True), \
         patch('sys.argv', ['bot', '--source-url', 'https://example.test/feed', '--output', str(output)]), \
         patch.object(runtime.TelegramScheduleBot, 'from_environment', return_value=bot), \
         patch.object(runtime, 'migrate_missing_user_state', return_value=[]), \
         patch.object(runtime, 'personal_schedule_events', return_value=university), \
         patch.object(runtime, 'create_personal_event_sync_service', return_value=PersonalEventSyncService(calendar_adapter=adapter)), \
         patch.object(runtime.AlfaCRMScheduleSource, 'fetch', return_value=lessons), \
         patch.object(runtime, 'WorkPreparationPlanner', side_effect=record_work_planner), \
         patch.object(bot, 'run_forever', side_effect=run_bot):
        runtime.main()
    assert result['reply']['text'] == 'Проверка завершена, проблем нет.'
    assert len(result['remote']) == (6 if with_university else 4)
    assert result['second']['text'] == 'Проверка завершена, проблем нет.'
    assert 'удалена пользователем вручную' in result['missing']['text']
    assert [call['max_continuous_minutes'] for call in planner_calls] == [120, 40]


@pytest.mark.parametrize('status', [400, 401, 403, 429, 500])
def test_calendar_write_failure_reaches_safe_workflow_report(tmp_path, capsys, status):
    from adapters.google_calendar_adapter import GoogleCalendarAdapter
    from services.update_all_workflow import UpdateAllWorkflow
    from googleapiclient.errors import HttpError
    import httplib2

    adapter = object.__new__(GoogleCalendarAdapter)
    adapter.calendar_id = 'unrelated-default'
    adapter.service = Mock()
    api = adapter.service.events.return_value
    api.list.return_value.execute.return_value = {'items': []}
    api.insert.return_value.execute.side_effect = HttpError(
        httplib2.Response({'status': str(status)}),
        b'{"error":{"message":"PRIVATE_TEST_TOKEN"}}')
    now = datetime(2026, 9, 15, 10, tzinfo=ZoneInfo('Asia/Tomsk'))
    event = PersonalUniversityEvent('test-class', 'Test', '', now,
        now + timedelta(hours=1), state=PersonalEventState.CONFIRMED)
    service = PersonalEventSyncService(adapter)
    preparations = Mock()
    workflow = UpdateAllWorkflow(tmp_path / 'report.json', [
        ('публикация пар в Calendar', lambda: service.sync_personal_event_to_calendar(event, 'study')),
        ('подготовки', preparations),
    ], lambda: [])
    workflow.run()
    state = workflow.load()
    assert state['issues'][0]['diagnostic']['status'] == status
    preparations.assert_not_called()
    assert api.insert.call_count == (3 if status in {429, 500} else 1)
    assert 'PRIVATE_TEST_TOKEN' not in str(state) + capsys.readouterr().out
