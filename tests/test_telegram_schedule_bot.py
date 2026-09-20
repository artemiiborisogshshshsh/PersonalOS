from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import requests

from services.telegram_schedule_bot import TelegramScheduleBot
from services.university_schedule_source import UniversityScheduleFetchResult


def fetch_result(_: str, output_path: Path) -> UniversityScheduleFetchResult:
    return UniversityScheduleFetchResult(
        source_url='https://example.test/schedule.ics',
        output_path=output_path,
        event_count=40,
        content_hash='a' * 64,
        fetched_at=datetime.now(timezone.utc),
    )


def bot() -> TelegramScheduleBot:
    return TelegramScheduleBot(
        token='test-token',
        allowed_chat_id='123',
        source_url='https://example.test/schedule.ics',
        output_path=Path('data/schedule.ics'),
        fetcher=fetch_result,
    )


def test_update_schedule_command_fetches_and_reports_result():
    reply = bot().handle_text('123', '/update_schedule')

    assert '40 событий' in reply
    assert 'aaaaaaaaaaaa' in reply


def test_update_command_is_recognized_with_a_bot_mention():
    assert '40 событий' in bot().handle_text('123', '/update_schedule@PersonalOSBot')


def test_schedule_change_triggers_replan_only_for_a_new_hash():
    instance = bot()
    instance.last_schedule_hash = 'old-hash'
    instance.schedule_change_replan = lambda: 'черновики перепланированы.'

    reply = instance.handle_text('123', '/update_schedule')

    assert 'черновики перепланированы' in reply


def test_schedule_refresh_reconciles_local_state_before_reporting_success():
    instance = bot()
    reconciled = []
    instance.schedule_refresh_reconcile = lambda: reconciled.append(True)

    reply = instance.handle_text('123', '/update_schedule')

    assert '40 событий' in reply
    assert reconciled == [True]


def test_bot_routes_durable_reconciliation_review_and_choice():
    instance = bot()
    instance.reconciliation_review = Mock(return_value={
        'text': 'Выбери замену',
        'buttons': [[{'text': 'Физика', 'callback_data': 'rec:accept:0:0'}]],
    })
    instance.reconciliation_apply = Mock(return_value={'text': 'Перенос подтверждён', 'buttons': []})

    assert instance.handle_text('123', '/schedule_review')['text'] == 'Выбери замену'
    assert instance.handle_callback('123', 'rec:accept:0:0')['text'] == 'Перенос подтверждён'
    instance.reconciliation_apply.assert_called_once_with('rec:accept:0:0')


def test_start_and_callback_can_use_resumable_onboarding_when_configured():
    instance = bot()
    instance.onboarding_start = Mock(return_value={'text': 'Выбери часовой пояс', 'buttons': []})
    instance.onboarding_action = Mock(return_value={'text': 'Источник', 'buttons': []})

    assert instance.handle_text('123', '/start')['text'] == 'Выбери часовой пояс'
    assert instance.handle_callback('123', 'ob:timezone:Asia/Tomsk')['text'] == 'Источник'


def test_timezone_onboarding_callback_updates_profile_before_next_step():
    instance = bot()
    applied = Mock()
    instance.onboarding_timezone_apply = applied
    instance.onboarding_action = Mock(return_value={'text': 'Источник', 'buttons': []})
    instance.handle_callback('123', 'ob:timezone:Asia/Tomsk')
    applied.assert_called_once_with('Asia/Tomsk')


def test_tpu_source_command_delegates_only_explicit_url():
    instance = bot()
    instance.onboarding_source_connect = Mock(return_value={'text': 'Источник проверен', 'buttons': []})
    assert 'Формат' in instance.handle_text('123', '/connect_tpu')
    assert instance.handle_text('123', '/connect_tpu https://ro-rasp.tpu.ru/gruppa_1/2026/1/view.html')['text'] == 'Источник проверен'


def test_attendance_completion_advances_onboarding_once(tmp_path):
    from services.attendance_preferences import AttendancePreferenceStore
    from services.telegram_attendance_onboarding import TelegramAttendanceOnboarding
    from models import EventType, UniversityEvent
    start = datetime(2026, 9, 2, 10)
    event = UniversityEvent('one', 'ОС (ЛК)', '', '', start, start.replace(hour=11), EventType.LECTURE, True)
    instance = bot()
    instance.events_loader = lambda: [event]
    instance.attendance_onboarding = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    completed = Mock()
    instance.onboarding_attendance_complete = completed
    prompt = instance.handle_text('123', '/attendance')
    instance.handle_callback('123', prompt['buttons'][0][0]['callback_data'])
    completed.assert_called_once()


def test_profile_and_successful_calendar_apply_advance_onboarding():
    instance = bot()
    instance.profile_update = Mock(return_value='Профиль сохранён')
    instance.calendar_apply = Mock(return_value='Готово: синхронизировано 1 событий.')
    profile_done, calendar_done = Mock(), Mock()
    instance.onboarding_profile_complete = profile_done
    instance.onboarding_calendar_complete = calendar_done

    instance.handle_text('123', '/sleep 23:00 06:40')
    instance.handle_callback('123', 'cal:apply')

    profile_done.assert_called_once()
    calendar_done.assert_called_once()


def test_explicit_preparation_preview_records_first_plan_milestone():
    instance = bot()
    instance.preparation_preview = Mock(return_value='План готов')
    instance.analytics_first_plan = Mock()
    assert instance.handle_text('123', '/preparations')['text'] == 'План готов'
    instance.analytics_first_plan.assert_called_once()


def test_completed_update_all_records_weekly_activity_but_blocked_run_does_not():
    instance = bot()
    active = Mock(); instance.analytics_weekly_active = active
    instance.update_all = Mock(return_value={'text': 'Проверка завершена, проблем нет.', 'buttons': []})
    instance.handle_text('123', '/update_all')
    active.assert_called_once()
    instance.update_all.return_value = {'text': 'Не удалось завершить «TPU».', 'buttons': []}
    instance.handle_text('123', '/update_all')
    active.assert_called_once()


def test_schedule_refresh_remains_available_when_optional_replan_fails():
    instance = bot()
    instance.last_schedule_hash = 'old-hash'
    secret = 'https://private.test/?token=TOP_SECRET'
    instance.schedule_change_replan = lambda: (_ for _ in ()).throw(RuntimeError(secret))

    reply = instance.handle_text('123', '/update_schedule')

    assert '40 событий' in reply
    assert 'черновики не были перепланированы' in reply
    assert 'TOP_SECRET' not in reply
    assert 'private.test' not in reply


def test_bot_rejects_messages_from_other_chats():
    assert bot().handle_text('999', '/update_schedule') is None


def test_bot_supports_help_and_unknown_command():
    assert '/update_schedule' in bot().handle_text('123', '/help')
    assert 'Неизвестная' in bot().handle_text('123', '/anything')


def test_update_all_routes_to_workflow_and_callback_only_for_owner():
    instance = bot()
    instance.update_all = Mock(return_value={'text': 'Проблем нет', 'buttons': []})
    instance.update_all_action = Mock(return_value={'text': 'Подробности', 'buttons': []})
    assert instance.handle_text('123', '/update_all@PersonalOSBot')['text'] == 'Проблем нет'
    assert instance.handle_text('999', '/update_all') is None
    assert instance.handle_callback('999', 'ua:run:retry:0') is None
    assert instance.handle_callback('123', 'ua:run:page:0')['text'] == 'Подробности'
    instance.update_all.assert_called_once()


def test_running_update_blocks_mutations_but_not_help():
    import threading
    instance = bot()
    started, stop = threading.Event(), threading.Event()

    def hold():
        with instance.operation_lock:
            started.set()
            stop.wait(5)

    worker = threading.Thread(target=hold)
    worker.start()
    try:
        assert started.wait(2)
        assert instance.handle_text('123', '/work_schedule') == 'Обновление уже выполняется'
        assert instance.handle_callback('123', 'prep:stage')['text'] == 'Обновление уже выполняется'
        assert '/update_all' in instance.handle_text('123', '/help')
    finally:
        stop.set()
        worker.join(2)


def test_background_update_edits_one_status_message():
    instance = bot()
    instance.update_all = Mock(return_value={'text': 'Проверка завершена, проблем нет.', 'buttons': []})
    response = Mock()
    response.json.return_value = {'result': {'message_id': 42}}
    with patch('services.telegram_schedule_bot.requests.post', return_value=response) as post:
        worker = instance.start_update_all('123')
        worker.join(2)
        assert not worker.is_alive()
        assert post.call_count == 2
        assert post.call_args.args[0].endswith('/editMessageText')
        assert post.call_args.kwargs['json']['message_id'] == 42


def test_help_explains_full_refresh_order_and_calendar_side_effects():
    instance = bot()
    instance.fetcher = Mock()
    instance.work_schedule_preview = Mock()
    instance.preparation_preview = Mock()
    reply = instance.handle_text('123', '/help@PersonalOSBot')

    commands = ['/update_schedule', '/attendance', '/work_schedule', '/preparations']
    positions = [reply.index(command) for command in commands]
    assert positions == sorted(positions)
    assert 'Отправить в Google Calendar' in reply
    assert 'дожидайся результата' in reply
    assert 'Черновики могут сразу появиться' in reply
    assert 'может удалить' in reply
    for command in ('/reset_preparation_drafts', '/sleep', '/prep_urgency'):
        assert command in reply
    assert len(reply) <= instance.TELEGRAM_MESSAGE_LIMIT
    assert instance.handle_text('123', '/start') == reply
    instance.fetcher.assert_not_called()
    instance.work_schedule_preview.assert_not_called()
    instance.preparation_preview.assert_not_called()


def test_bot_exposes_work_schedule_preview_without_accepting_credentials_in_chat():
    instance = bot()
    instance.work_schedule_preview = lambda: 'AlfaCRM: рабочих занятий: 2.'

    assert instance.handle_text('123', '/work_schedule') == 'AlfaCRM: рабочих занятий: 2.'


def test_bot_routes_safe_system_edit_commands_with_a_code_from_inventory():
    instance = bot()
    instance.system_events_preview = lambda: 'u1: ВУЗ'
    instance.system_delete = lambda code: f'deleted={code}'
    instance.system_restore = lambda code: f'restored={code}'

    assert instance.handle_text('123', '/system_events') == 'u1: ВУЗ'
    assert instance.handle_text('123', '/system_delete u1') == 'deleted=u1'
    assert instance.handle_text('123', '/system_restore u1') == 'restored=u1'
    assert 'Сначала открой' in instance.handle_text('123', '/system_delete')


def test_bot_routes_persistent_preparation_rule_commands():
    instance = bot()
    instance.preparation_rules_preview = lambda: 'ЛБ отключены.'
    instance.preparation_rule_disable = lambda raw: f'disabled={raw}'
    instance.preparation_rule_enable = lambda raw: f'enabled={raw}'

    assert instance.handle_text('123', '/preparation_rules') == 'ЛБ отключены.'
    assert instance.handle_text('123', '/disable_preparation Архитектура ИС ЛБ') == (
        'disabled=Архитектура ИС ЛБ'
    )
    assert instance.handle_text('123', '/enable_preparation Архитектура ИС ЛБ') == (
        'enabled=Архитектура ИС ЛБ'
    )


def test_bot_exposes_button_driven_management_center():
    instance = bot()
    instance.management_home = lambda: {
        'text': 'Центр управления',
        'buttons': [[{'text': 'Учёба', 'callback_data': 'manage:study'}]],
    }
    instance.management_action = lambda data: {
        'text': f'action={data}', 'buttons': [],
    }

    assert instance.handle_text('123', '/manage')['text'] == 'Центр управления'
    assert instance.handle_callback('123', 'manage:study')['text'] == 'action=manage:study'


def test_bot_collects_work_feedback_in_three_safe_steps():
    instance = TelegramScheduleBot('token', '123', 'https://example.test', Path('schedule.ics'))
    received = []
    instance.work_feedback_complete = lambda *values: received.append(values) or 'saved'

    reply = instance.handle_callback('123', 'work:feedback:7:11:done')
    assert 'задал домой' in reply['text']
    assert 'дедлайн' in instance.handle_text('123', 'лист 2').casefold()
    assert 'комментарий' in instance.handle_text('123', '12.09').casefold()
    assert instance.handle_text('123', 'нет') == 'saved'
    assert received == [('7:11', 'done', 'лист 2', '12.09', '')]


def test_bot_collects_work_preparation_feedback():
    instance = TelegramScheduleBot('token', '123', 'https://example.test', Path('schedule.ics'))
    received = []
    instance.work_preparation_feedback_detail = lambda *values: received.append(values) or 'saved prep'

    reply = instance.handle_callback('123', 'wpfb:work-prep:abc:partial')
    assert 'Сколько минут' in reply['text']
    assert instance.handle_text('123', '15 4 устал') == 'saved prep'
    assert received == [('work-prep:abc', 'partial', 15, 4, 'устал')]


def test_bot_exposes_explicit_duplicate_cleanup_confirmation():
    instance = bot()
    instance.preparation_cleanup_preview = lambda: 'Найдено дубликатов: 19.'
    instance.preparation_cleanup_confirm = lambda: 'Удалено: 19.'

    preview = instance.handle_text('123', '/cleanup_preparation_drafts')

    assert 'дубликатов' in preview['text']
    assert preview['buttons'][0][0]['callback_data'] == 'prep:cleanup-confirm'
    assert instance.handle_callback('123', 'prep:cleanup-confirm')['text'] == 'Удалено: 19.'


def test_bot_exposes_explicit_reset_confirmation_for_all_system_drafts():
    instance = bot()
    instance.preparation_reset_preview = lambda: 'Будет удалено: 20.'
    instance.preparation_reset_confirm = lambda: 'Удалено: 20.'

    preview = instance.handle_text('123', '/reset_preparation_drafts')

    assert preview['buttons'][0][0]['callback_data'] == 'prep:reset-confirm'
    assert instance.handle_callback('123', 'prep:reset-confirm')['text'] == 'Удалено: 20.'


def test_polling_error_diagnostics_do_not_include_bot_url_or_token():
    error = requests.HTTPError('https://api.telegram.org/botsecret/getUpdates')
    error.response = Mock(status_code=409)

    message = TelegramScheduleBot._telegram_error_message('polling', error)

    assert '409' in message
    assert 'secret' not in message


def test_send_message_splits_a_long_schedule_report_without_losing_text():
    instance = bot()
    response = Mock()
    response.raise_for_status.return_value = None
    long_text = ('строка отчёта\n' * 500)

    with patch('services.telegram_schedule_bot.requests.post', return_value=response) as post:
        instance.send_message('123', long_text)

    sent = [call.kwargs['json']['text'] for call in post.call_args_list]
    assert len(sent) == 2
    assert all(len(part) <= 4096 for part in sent)
    assert ''.join(part + ('\n' if not part.endswith('\n') else '') for part in sent).replace('\n\n', '\n').startswith('строка')


def test_bot_exposes_preparation_preview_and_draft_lifecycle_callbacks():
    instance = bot()
    instance.preparation_preview = lambda: 'Найдено 2 подготовки.'
    instance.preparation_stage = lambda: 'Черновики созданы в календаре Работа.'
    instance.preparation_confirm = lambda: 'Черновики подтверждены.'
    instance.preparation_rollback = lambda: 'Черновики отменены и snapshot восстановлен.'
    instance.preparation_replan = lambda: 'Новый план подготовлен.'

    preview = instance.handle_text('123', '/preparations')
    staged = instance.handle_callback('123', 'prep:stage')

    assert preview['text'] == 'Найдено 2 подготовки.'
    assert staged['text'] == 'Черновики созданы в календаре Работа.'
    assert instance.handle_callback('123', 'prep:confirm')['text'] == 'Черновики подтверждены.'
    assert 'snapshot' in instance.handle_callback('123', 'prep:rollback')['text']
    assert instance.handle_callback('123', 'prep:replan')['text'] == 'Новый план подготовлен.'


def test_bot_delegates_planning_profile_updates_from_telegram_commands():
    instance = bot()
    instance.profile_preview = lambda: 'Текущий сон: 23:00–07:00.'
    instance.profile_update = lambda command, args: f'{command}={args}'

    assert 'Текущий сон' in instance.handle_text('123', '/planning_profile')
    assert instance.handle_text('123', '/sleep 00:00 08:00') == '/sleep=00:00 08:00'
    assert instance.handle_text('123', '/recovery 60') == '/recovery=60'


def test_bot_accepts_feedback_buttons_and_detailed_telegram_feedback():
    instance = bot()
    instance.feedback_preview = lambda: {
        'text': 'Выбери блок',
        'buttons': [[{'text': 'Сделал', 'callback_data': 'fb:0:done'}]],
    }
    instance.feedback_submit = lambda index, outcome: f'{index}:{outcome}'
    instance.feedback_detail = lambda details: f'feedback={details}'

    assert instance.handle_text('123', '/feedback')['text'] == 'Выбери блок'
    prompt = instance.handle_callback('123', 'fb:0:partial')['text']
    assert 'Сколько минут' in prompt
    assert instance.handle_text('123', '20 4 сложно') == (
        'feedback=1 partial 20 4 сложно'
    )
    assert instance.handle_text('123', '/feedback 0 done 55 4 сложно') == (
        'feedback=0 done 55 4 сложно'
    )


def test_bot_applies_or_rejects_feedback_proposal_only_by_explicit_command():
    instance = bot()
    instance.feedback_proposal_apply = Mock(return_value='Предложение применено.')
    instance.feedback_proposal_reject = Mock(return_value='Предложение отклонено.')

    assert 'применено' in instance.handle_text('123', '/feedback_apply')
    assert 'отклонено' in instance.handle_text('123', '/feedback_reject')
    instance.feedback_proposal_apply.assert_called_once_with()
    instance.feedback_proposal_reject.assert_called_once_with()


def test_bot_routes_plain_text_and_natural_language_callbacks_through_proposal_boundary():
    instance = bot()
    flow = Mock()
    flow.propose.return_value = {'text': 'Предложение', 'buttons': []}
    flow.handle_callback.return_value = {'text': 'Выполнено', 'buttons': []}
    instance.natural_text_proposals = flow

    assert instance.handle_text('123', 'Создай задачу') ['text'] == 'Предложение'
    assert instance.handle_callback('123', 'nl:confirm:proposal')['text'] == 'Выполнено'
    flow.propose.assert_called_once_with('123', 'Создай задачу')
    flow.handle_callback.assert_called_once_with('123', 'nl:confirm:proposal')
