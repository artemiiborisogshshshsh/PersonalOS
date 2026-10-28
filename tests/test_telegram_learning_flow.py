"""Real application routing with local durable state and synthetic providers."""
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from services.telegram_learning_flow import TelegramLearningFlow
from services.onboarding_service import OnboardingStep
from tests.test_published_plan_deletions import setup
from tests.test_closed_beta_runtime import NOW, application, URL


def flow_app(tmp_path, monkeypatch, *, same_day=False):
    app, calendar, content, factory = setup(tmp_path)
    events = app.snapshot.personal_events()
    first = events[0]
    second = replace(events[1], title='Физика (ПР)',
                     university_event_uid='physics', id='personal-university:physics')
    if same_day:
        second = replace(second, title=first.title,
                         start_time=first.start_time + timedelta(hours=2),
                         end_time=first.end_time + timedelta(hours=2))
    monkeypatch.setattr(app.snapshot, 'personal_events', lambda: [first, second])
    monkeypatch.setattr(app, 'sync_learning', lambda: '')
    app.learning.refresh()
    lessons = [row for row in app.learning.store.lessons() if row['active']]
    return app, lessons


def test_explicit_ambiguous_lesson_selection_is_applied_once(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch, same_day=True)
    result = app.handle_text('101', 'По ОС задали номера 15–20')
    chosen = result['buttons'][0][0]['callback_data']
    chosen_id = app.learning._state()['selection']['lessons'][0]
    saved = app.handle_callback('101', chosen)
    assert 'Сохранено' in saved['text'] and not saved['buttons']
    assert app.learning.store.get_lesson(chosen_id)['materials'][0]['text'] == 'По ОС задали номера 15–20'
    assert 'устарела' in app.handle_callback('101', chosen)['text']
    assert len(app.learning.store.get_lesson(chosen_id)['materials']) == 1


def test_short_course_and_unknown_explicit_subject_never_use_other_context(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    physics = next(row for row in lessons if row['course'] == 'Физика')
    os_lesson = next(row for row in lessons if row['course'] == 'ОС')
    app.learning.store.set_context(physics['id'])
    assert 'Сохранено: ОС' in app.handle_text('101', 'По ОС задали номера 15–20')['text']
    assert not app.learning.store.get_lesson(physics['id'])['materials']
    assert app.learning.store.get_lesson(os_lesson['id'])['materials']
    before = app.learning.store.get_lesson(os_lesson['id'])['materials']
    assert app.handle_text('101', 'По БЖД задали номера 5')['buttons']
    assert app.learning.store.get_lesson(os_lesson['id'])['materials'] == before


def test_existing_task_confirmation_precedes_learning_intake(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    app.learning.store.set_context(lessons[0]['id'])
    propose = Mock(return_value={'text': 'Task preview', 'buttons': [['confirm']]})
    monkeypatch.setattr(app.natural_text, 'propose', propose)
    result = app.handle_text('101', 'Добавь задачу сделать отчёт')
    assert result['text'] == 'Task preview'
    propose.assert_called_once()
    assert all(not row['materials'] for row in app.learning.store.lessons())
    assert app.learning.handle_text('Привет') is None


def test_attendance_prompt_reply_keeps_lesson_after_other_callback_and_restart(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    first, second = lessons
    app.now = lambda: first_time(first['end']) - timedelta(minutes=5)
    send = Mock(return_value={'message_id': 321})
    assert app.learning.deliver_due(send, Mock())
    assert send.call_count == 1
    app.learning.handle_callback(app.learning.store.callback_data(second['id'], 'attend'))
    app.learning = TelegramLearningFlow(app)
    reply = app.handle_text('101', 'задали номера 4–7', message={
        'message_id': 500, 'reply_to_message': {'message_id': 321}})
    assert 'Сохранено' in reply['text']
    assert app.learning.store.get_lesson(first['id'])['materials'][0]['text'] == 'задали номера 4–7'
    assert not app.learning.store.get_lesson(second['id'])['materials']
    # A reply whose bot prompt is unknown must ask instead of using context.
    unknown = app.handle_text('101', 'задали номера 9', message={
        'message_id': 501, 'reply_to_message': {'message_id': 999}})
    assert unknown['buttons']


def first_time(value):
    from datetime import datetime
    return datetime.fromisoformat(value)


def test_photo_reply_private_document_type_and_explicit_schedule_route(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    first, second = lessons
    app.learning.bind_message(42, first['id'])
    app.learning.store.set_context(second['id'])
    photo = {'message_id': 60, 'photo': [{'file_id': 'private-photo', 'file_unique_id': 'unique'}],
             'reply_to_message': {'message_id': 42}}
    assert 'Сохранено' in app.handle_attachment('101', photo)['text']
    material = app.learning.store.get_lesson(first['id'])['materials'][0]
    assert material['file_id'] == 'private-photo' and material['file_type'] == 'photo'
    assert not app.learning.store.get_lesson(second['id'])['materials']
    assert not app.learning.wants_schedule_image('')
    assert app.learning.wants_schedule_image('Расписание на неделю')
    app.enable_schedule_ocr = True  # Exercise only the dormant legacy import dialogue.
    app.handle_text('101', '/import_schedule')
    assert app.learning.wants_schedule_image('21–27 сентября')
    assert not app.learning.wants_schedule_image('')


def test_prepare_returns_saved_files_and_ack_survives_restart(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    store = app.learning.store
    store.add_material(lesson['id'], text='номера 1–5', file_id='photo', file_type='photo')
    store.add_material(lesson['id'], kind='note', file_id='pdf', file_type='document')
    now = first_time(lesson['end']) + timedelta(hours=2)
    app.now = lambda: now
    store.register_preparation_blocks([{'id': 'block', 'lesson_id': lesson['id'],
        'start': now.isoformat(), 'end': (now + timedelta(hours=1)).isoformat()}])
    monkeypatch.setattr(app.learning, 'refresh', lambda: None)
    send, photo, document = Mock(return_value={'message_id': 77}), Mock(), Mock()
    ids = app.learning.deliver_due(send, photo, document)
    assert any(identifier.startswith('preparation_start:') for identifier in ids)
    assert 'номера 1–5' in send.call_args.args[1]
    photo.assert_called_once_with('101', 'photo')
    document.assert_called_once_with('101', 'pdf')
    app.learning = TelegramLearningFlow(app)
    monkeypatch.setattr(app.learning, 'refresh', lambda: None)
    app.learning.deliver_due(send, photo, document)
    assert send.call_count == 1
    assert app.learning._state()['prompts']['77']['lesson_id'] == lesson['id']


def test_failed_notification_send_retries_without_receipt(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    app.now = lambda: first_time(lessons[0]['end']) - timedelta(minutes=5)
    failed = Mock(side_effect=RuntimeError('synthetic delivery failure'))
    assert app.learning.deliver_due(failed, Mock()) == []
    assert not app.learning._state().get('prompts')
    success = Mock(return_value={'message_id': 88})
    assert app.learning.deliver_due(success, Mock())
    assert success.call_count == 1
    assert app.learning.deliver_due(success, Mock()) == []


def test_onboarding_cannot_publish_before_style_sleep_and_travel(tmp_path):
    app = application(tmp_path)
    app.handle_text('101', '/start')
    app.handle_text('101', '/connect_tpu ' + URL)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    assert app.onboarding.store.load().step == OnboardingStep.CALENDAR
    assert not app.handle_text('101', '/weekly_preview')['buttons']
    assert not app.adapter.writes


def test_invalid_deadline_is_actionable_and_does_not_mutate(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    app.learning.store.set_context(lessons[0]['id'])
    result = app.handle_text('101', 'Это нужно сделать к 32.10')
    assert 'существующую дату' in result['text']
    assert not app.learning.store.get_lesson(lessons[0]['id'])['due_explicit']


def test_source_move_updates_durable_lesson_and_delivers_notice_once(tmp_path):
    app, calendar, content, factory = setup(tmp_path)
    app.learning.refresh()
    origin = app.learning.store.lessons()[0]
    app.learning.store.add_material(origin['id'], text='Тема: процессы', kind='note')
    app.sync_learning()
    content['ics'] = content['ics'].replace(b'030000Z', b'040000Z').replace(b'043000Z', b'053000Z')
    app.now = lambda: NOW.replace(hour=12)
    before = len(calendar.writes)
    send = Mock(return_value={'message_id': 101})
    app.background_tick(send, Mock())
    updated = app.learning.store.get_lesson(origin['id'])
    assert first_time(updated['start']).hour == 11
    assert updated['materials'][0]['text'] == 'Тема: процессы'
    assert len(calendar.writes) > before
    notices = lambda: [call.args[1] for call in send.call_args_list if call.args[1].startswith('Расписание изменилось')]
    assert len(notices()) == 1
    notice = notices()[0]
    assert 'Google Calendar обновлён' in notice
    assert 'ОС (ЛК)' in notice and '24.09 10:00–11:30 → 24.09 11:00–12:30' in notice
    assert all(hidden not in notice for hidden in ('пока без записи', 'PlanningEngine', 'Тема: процессы', 'AI Calendar', 'Обновление плана v'))
    assert len(notice) < 1000
    writes = list(calendar.writes)
    app.background_tick(send, Mock())
    assert len(notices()) == 1 and calendar.writes == writes


def test_source_notice_survives_delivery_failure_and_restart(tmp_path):
    app, calendar, content, factory = setup(tmp_path)
    content['ics'] = content['ics'].replace(b'030000Z', b'040000Z').replace(b'043000Z', b'053000Z')
    app.now = lambda: NOW.replace(hour=12)
    app.background_tick(Mock(side_effect=RuntimeError('synthetic offline')), Mock())
    assert app.learning._state()['source_notice']
    writes = list(calendar.writes)
    restarted = factory(tmp_path)
    restarted.now = app.now
    send = Mock(return_value={'message_id': 102})
    restarted.background_tick(send, Mock())
    notices = lambda: [call.args[1] for call in send.call_args_list if call.args[1].startswith('Расписание изменилось')]
    assert len(notices()) == 1 and not restarted.learning._state().get('source_notice')
    restarted.background_tick(send, Mock())
    assert len(notices()) == 1 and calendar.writes == writes


def test_source_conflict_is_visible_once_until_problem_changes(tmp_path, monkeypatch):
    app, calendar, content, factory = setup(tmp_path)
    monkeypatch.setattr(app, 'preview', lambda: {'text': 'Ручное изменение сохранено. Открой /review_calendar.', 'buttons': []})
    app.now = lambda: NOW.replace(hour=12)
    send = Mock(return_value={'message_id': 103})
    app.background_tick(send, Mock())
    notices = lambda: [call.args[1] for call in send.call_args_list if call.args[1].startswith('Автоматическое обновление')]
    assert len(notices()) == 1 and '/review_calendar' in notices()[0]
    app.now = lambda: NOW.replace(hour=15)
    app.background_tick(send, Mock())
    assert len(notices()) == 1


def test_new_user_first_plan_needs_no_optional_attendance_questionnaire(tmp_path):
    app = application(tmp_path)
    app.handle_text('101', '/start')
    app.handle_text('101', URL)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    app.handle_text('101', '/calendar_status')
    app.handle_callback('101', 'ob:style:friendly')
    app.handle_text('101', '/sleep 23:00 07:00')
    app.handle_text('101', '/travel 30')
    assert app.onboarding.store.load().step == OnboardingStep.COMPLETE
    preview = app.handle_text('101', '/weekly_preview')
    assert preview['buttons'], preview
    assert not app.adapter.writes


def test_failed_file_delivery_retries_only_unsent_attachment(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    store = app.learning.store
    store.add_material(lesson['id'], text='номера 1–5', file_id='photo')
    store.add_material(lesson['id'], kind='note', file_id='pdf', file_type='document')
    now = first_time(lesson['end']) + timedelta(hours=2)
    app.now = lambda: now
    store.register_preparation_blocks([{'id': 'block', 'lesson_id': lesson['id'],
        'start': now.isoformat(), 'end': (now + timedelta(hours=1)).isoformat()}])
    monkeypatch.setattr(app.learning, 'refresh', lambda: None)
    send = Mock(return_value={'message_id': 77})
    photo, document = Mock(), Mock(side_effect=RuntimeError('synthetic offline'))
    assert not app.learning.deliver_due(send, photo, document)
    app.learning = TelegramLearningFlow(app)
    monkeypatch.setattr(app.learning, 'refresh', lambda: None)
    document.side_effect = None
    assert app.learning.deliver_due(send, photo, document)
    assert send.call_count == photo.call_count == 1
    assert document.call_count == 2


def test_remaining_reply_replans_whole_homework_without_reentering_materials(tmp_path):
    from tests.test_learning_calendar import setup as calendar_setup, prepare_homework, operation, assert_no_prep_conflicts
    app, calendar, content, factory = calendar_setup(tmp_path)
    lesson_id = prepare_homework(app, minutes=120, deadline=NOW.replace(day=26, hour=20))
    previous = app.learning.store.get_lesson(lesson_id)['materials']
    block = next(block for block in operation(app).blocks if block.id.startswith('learning-prep:'))
    app.now = lambda: block.end
    response = app.handle_callback('101', app.learning.store.callback_data(lesson_id, 'incomplete'))
    assert 'Что осталось' in response['text']
    saved = app.handle_text('101', 'Остались номера 18–20, ещё 40 минут')
    assert 'продолжения' in saved['text'] and 'Google Calendar обновлён' in saved['text']
    lesson = app.learning.store.get_lesson(lesson_id)
    assert lesson['remaining_minutes'] == 40 and '18–20' in lesson['remaining_text']
    assert lesson['materials'] == previous
    future = [item for item in operation(app).blocks if item.id.startswith('learning-prep:') and item.start > app.now()]
    assert sum(item.minutes for item in future) == 40
    assert_no_prep_conflicts(app)
    # Whole homework completion removes that future continuation exactly once.
    done = app.learning.store.callback_data(lesson_id, 'done')
    assert 'ДЗ выполнено' in app.handle_callback('101', done)['text']
    writes = list(calendar.writes)
    assert not [item for item in operation(app).blocks if item.id.startswith('learning-prep:') and item.start > app.now()]
    assert 'уже использована' in app.handle_callback('101', done)['text']
    assert calendar.writes == writes


@pytest.mark.parametrize('answer,expected', [('Был', True), ('Пропустил', False)])
def test_bound_attendance_words_save_attendance_without_homework(tmp_path, monkeypatch, answer, expected):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    app.learning.bind_message(900, lesson['id'], 'attendance')
    app.learning.store.set_context(lessons[1]['id'])
    reply = app.handle_text('101', answer, message={'message_id': 901, 'reply_to_message': {'message_id': 900}})
    assert 'Пришли ДЗ' in reply['text']
    saved = app.learning.store.get_lesson(lesson['id'])
    assert saved['attendance'] is expected and saved['materials'] == []


@pytest.mark.parametrize('answer,completed', [('Да', True), ('Нет', False)])
def test_bound_preparation_end_yes_no_preserves_saved_homework(tmp_path, monkeypatch, answer, completed):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    app.learning.store.add_material(lesson['id'], text='номера 1–5')
    app.learning.bind_message(910, lesson['id'], 'preparation_end')
    app.learning.store.set_context(lessons[1]['id'])
    app.learning = TelegramLearningFlow(app)
    reply = app.handle_text('101', answer, message={'message_id': 911, 'reply_to_message': {'message_id': 910}})
    saved = app.learning.store.get_lesson(lesson['id'])
    assert saved['completed'] is completed
    assert [item['text'] for item in saved['materials']] == ['номера 1–5']
    assert ('ДЗ выполнено' if completed else 'Что осталось') in reply['text']


def test_old_preparation_prompt_text_cannot_reopen_completed_homework(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    app.learning.store.add_material(lesson['id'], text='номера 1–5')
    app.learning.bind_message(920, lesson['id'], 'preparation_end')
    app.handle_text('101', 'Да', message={'message_id': 921, 'reply_to_message': {'message_id': 920}})
    saved = app.learning.store.get_lesson(lesson['id'])
    reply = app.handle_text('101', 'Нет', message={'message_id': 922, 'reply_to_message': {'message_id': 920}})
    assert 'уже изменился' in reply['text']
    assert app.learning.store.get_lesson(lesson['id']) == saved


def test_restart_syncs_completion_after_crash_before_dialogue_dirty_write(tmp_path, monkeypatch):
    from tests.test_learning_calendar import setup as calendar_setup, prepare_homework, operation
    from tests.test_published_plan_updates import ProcessCrash
    app, calendar, content, factory = calendar_setup(tmp_path)
    lesson_id = prepare_homework(app, minutes=120, deadline=NOW.replace(day=26, hour=20))
    assert not app.learning.store.has_pending_calendar_sync()
    monkeypatch.setattr(app.learning, '_changed', Mock(side_effect=ProcessCrash()))
    with pytest.raises(ProcessCrash):
        app.handle_callback('101', app.learning.store.callback_data(lesson_id, 'done'))
    assert app.learning.store.get_lesson(lesson_id)['completed']
    assert app.learning.store.has_pending_calendar_sync()
    assert not app.learning._state().get('sync_pending')
    restarted = factory(tmp_path)
    restarted.background_tick(Mock(return_value={'message_id': 930}), Mock())
    assert not [block for block in operation(restarted).blocks if block.id.startswith('learning-prep:')]
    assert not restarted.learning.store.has_pending_calendar_sync()


def _new_user_with_automatic_attendance(tmp_path, content):
    from services.published_plan_updates import PublishedPlanUpdates
    app = application(tmp_path, content=content)
    app.updates = PublishedPlanUpdates(app)
    app.handle_text('101', '/start')
    app.handle_text('101', URL)
    app.handle_callback('101', 'ob:timezone:Asia/Tomsk')
    app.handle_text('101', '/calendar_status')
    app.handle_callback('101', 'ob:style:friendly')
    app.handle_text('101', '/sleep 23:00 07:00')
    app.handle_text('101', '/travel 30')
    return app


@pytest.mark.parametrize('new_course', [False, True])
def test_ordinary_new_source_occurrence_extends_defaults_after_classification(tmp_path, new_course):
    from tests.test_closed_beta_runtime import ICS, confirmation
    from services.attendance_preferences import AttendancePreferenceStore
    from models import PersonalEventState
    content = {'ics': ICS}
    app = _new_user_with_automatic_attendance(tmp_path, content)
    first = app.handle_text('101', '/weekly_preview')
    assert 'опубликован' in app.handle_callback('101', confirmation(first))['text']
    block = ICS.split(b'BEGIN:VEVENT')[1].split(b'END:VEVENT')[0]
    block = block.replace(b'pilot-lecture', b'new-next-week').replace(b'20260924', b'20260928')
    if new_course:
        block = block.replace('ОС (ЛК)'.encode(), 'Математика (ЛК)'.encode())
    content['ics'] = ICS.replace(b'END:VCALENDAR', b'BEGIN:VEVENT' + block + b'END:VEVENT\r\nEND:VCALENDAR')
    preview = app.handle_text('101', '/weekly_preview')
    assert '+ Добавить:' in preview['text'], preview
    saved = next(row for row in app.snapshot.personal_events() if row.university_event_uid == 'new-next-week')
    assert saved.state == PersonalEventState.CONFIRMED
    prefs = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
    subject = 'Математика' if new_course else 'ОС'
    assert 'new-next-week' in prefs.subjects[subject].defaulted_lecture_uids


def test_ambiguous_source_replacement_does_not_gain_default_attendance(tmp_path):
    from tests.test_closed_beta_runtime import ICS
    from services.attendance_preferences import AttendancePreferenceStore
    from models import PersonalEventState
    content = {'ics': ICS}
    app = _new_user_with_automatic_attendance(tmp_path, content)
    app.handle_text('101', '/weekly_preview')
    block = ICS.split(b'BEGIN:VEVENT')[1].split(b'END:VEVENT')[0]
    candidates = b''.join(b'BEGIN:VEVENT' + block.replace(b'pilot-lecture', uid)
                         .replace('ОС (ЛК)'.encode(), title.encode()) + b'END:VEVENT\r\n'
                         for uid, title in ((b'physics', 'Физика (ЛК)'), (b'chemistry', 'Химия (ЛК)')))
    content['ics'] = b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\n' + candidates + b'END:VCALENDAR\r\n'
    response = app.handle_text('101', '/weekly_preview')
    assert '/review_replacements' in response['text']
    assert app.snapshot.personal_events()[0].state == PersonalEventState.NEEDS_REVIEW
    prefs = AttendancePreferenceStore.load(app.directory / 'attendance_preferences.json')
    assert 'Физика' not in prefs.subjects and 'Химия' not in prefs.subjects


def test_bound_evening_no_keeps_homework_unknown_without_inventing_task(tmp_path, monkeypatch):
    app, lessons = flow_app(tmp_path, monkeypatch)
    lesson = lessons[0]
    app.handle_callback('101', app.learning.store.callback_data(lesson['id'], 'attend'))
    app.learning.bind_message(940, lesson['id'], 'evening')
    reply = app.handle_text('101', 'Нет', message={'message_id': 941, 'reply_to_message': {'message_id': 940}})
    assert 'Когда узнаешь' in reply['text']
    saved = app.learning.store.get_lesson(lesson['id'])
    assert saved['homework_state'] == 'later' and saved['materials'] == []


def test_applied_source_notice_uses_user_timezone_and_only_preparation_counts():
    from copy import deepcopy
    from types import SimpleNamespace
    from services.closed_beta_runtime import ClosedBetaApplication
    def row(uid, kind, start, end):
        return {'kind': kind, 'data': {'uid': uid, 'summary': 'Физика (ПР)' if kind == 'class' else 'Подготовка',
            'dtstart': start, 'dtend': end, 'description': 'PlanningEngine: private full materials'}}
    before = {'class': row('class', 'class', '2026-10-29T03:00:00+00:00', '2026-10-29T04:00:00+00:00'),
              'moved': row('moved', 'preparation', '2026-10-28T06:00:00+00:00', '2026-10-28T07:00:00+00:00'),
              'removed': row('removed', 'preparation', '2026-10-28T08:00:00+00:00', '2026-10-28T09:00:00+00:00')}
    after = deepcopy(before)
    after['class']['data'].update(dtstart='2026-10-29T04:00:00+00:00', dtend='2026-10-29T05:00:00+00:00')
    after['moved']['data'].update(dtstart='2026-10-28T07:00:00+00:00', dtend='2026-10-28T08:00:00+00:00')
    del after['removed']
    after['added'] = row('added', 'preparation', '2026-10-28T09:00:00+00:00', '2026-10-28T10:00:00+00:00')
    text = ClosedBetaApplication._source_update_notice(SimpleNamespace(published_plan={'rows': before}),
        SimpleNamespace(published_plan={'rows': after}), 'Asia/Tomsk')
    assert 'Физика (ПР): 29.10 10:00–11:00 → 29.10 11:00–12:00' in text
    assert 'Подготовки: добавлено — 1, перенесено — 1, удалено — 1.' in text
    assert 'PlanningEngine' not in text and 'private full materials' not in text and 'пока без записи' not in text
