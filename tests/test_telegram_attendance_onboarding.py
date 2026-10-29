from datetime import datetime

from models import EventType, UniversityEvent
from services.attendance_preferences import AttendancePreferenceStore
from services.telegram_attendance_onboarding import TelegramAttendanceOnboarding


def event(event_type, hour=10):
    start = datetime(2026, 9, 2, hour)
    return UniversityEvent('id', 'ОС (ЛБ)', '', '', start, start.replace(hour=hour + 1), event_type, True)


def test_inline_flow_stores_choices_and_lists_lab_slots(tmp_path):
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    first = flow.start([event(EventType.LECTURE), event(EventType.PRACTICAL), event(EventType.LAB, 12)])
    assert 'лекции' in first['text']
    second = flow.handle_callback(first['buttons'][0][0]['callback_data'])
    third = flow.handle_callback(second['buttons'][0][0]['callback_data'])
    assert 'лабораторные' in third['text']
    selected = flow.handle_callback(third['buttons'][0][0]['callback_data'])
    done = flow.handle_callback(selected['buttons'][-1][0]['callback_data'])
    assert 'завершена' in done['text']
    assert done['attendance_complete'] is True
    assert 'ОС:' in done['text']
    restarted = flow.handle_callback(done['buttons'][0][0]['callback_data'])
    assert 'лекции' in restarted['text']


def test_lab_question_includes_skip_button(tmp_path):
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    first = flow.start([event(EventType.LAB, 12)])

    assert first['buttons'][-1][0]['text'] == 'Сохранить: не посещаю ЛБ'
    done = flow.handle_callback(first['buttons'][-1][0]['callback_data'])
    assert 'завершена' in done['text']


def test_utc_lab_keys_and_labels_match_planner_profile_timezone(tmp_path):
    from datetime import timezone
    from dataclasses import replace
    from zoneinfo import ZoneInfo
    from services.attendance_preferences import lab_slot_key
    lab = event(EventType.LAB, 5)
    lab.dtstart = datetime(2026, 9, 3, 5, 40, tzinfo=timezone.utc)
    lab.dtend = datetime(2026, 9, 3, 7, 15, tzinfo=timezone.utc)
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    question = flow.start([lab])
    assert question['buttons'][0][0]['text'] == 'Чт 12:40'
    selection = flow.handle_callback(question['buttons'][0][0]['callback_data'])
    reply = flow.handle_callback(selection['buttons'][-1][0]['callback_data'])
    assert 'ЛБ — Чт 12:40' in reply['text']
    local_lab = replace(lab, dtstart=lab.dtstart.astimezone(ZoneInfo('Asia/Tomsk')),
                        dtend=lab.dtend.astimezone(ZoneInfo('Asia/Tomsk')))
    assert flow.store.subjects['ОС'].lab_slots == {lab_slot_key(local_lab)}
    assert flow.store.preference_for(local_lab) is True
    assert lab.dtstart.tzinfo is timezone.utc
    other_flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'other.json'),
                                              timezone='Asia/Vladivostok')
    assert 'Чт 15:40' in str(other_flow.start([lab])['buttons'])


def test_lab_label_rolls_weekday_and_course_quotes_are_display_only(tmp_path):
    from datetime import timezone
    lab = event(EventType.LAB)
    lab.dtstart = datetime(2026, 9, 2, 22, tzinfo=timezone.utc)
    lab.summary = r'\"Код ТПУ\" (ЛБ)'
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    question = flow.start([lab])
    assert question['buttons'][0][0]['text'] == 'Чт 05:00'
    assert question['text'].startswith('"Код ТПУ":')
    selected = flow.handle_callback(question['buttons'][0][0]['callback_data'])
    flow.handle_callback(selected['buttons'][-1][0]['callback_data'])
    assert r'\"Код ТПУ\"' in flow.store.subjects


def test_management_edit_delegates_to_per_subject_drafts(tmp_path):
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    source = [event(EventType.LECTURE), event(EventType.PRACTICAL)]
    menu = flow.start(source, edit=True)
    subject = flow.handle_callback(menu['buttons'][0][0]['callback_data'], events=source)
    assert 'Выбор независим' in subject['text']
    practical = flow.handle_callback(subject['buttons'][1][0]['callback_data'], events=source)
    assert not flow.store.path.exists()
    saved = flow.handle_callback(practical['buttons'][-1][0]['callback_data'], events=source)
    assert saved['attendance_saved']
    assert flow.store.subjects['ОС'].lectures is False
    assert flow.store.subjects['ОС'].practicals is True


def test_wizard_lecture_no_still_disables_entire_course(tmp_path):
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    first = flow.start([event(EventType.LECTURE), event(EventType.PRACTICAL), event(EventType.LAB)])
    done = flow.handle_callback(first['buttons'][0][1]['callback_data'])
    assert done['attendance_complete']
    assert flow.store.subjects['ОС'].lectures is False
    assert flow.store.subjects['ОС'].practicals is False
    assert flow.store.subjects['ОС'].labs_enabled is False


def test_wizard_accepts_old_lab_none_callback(tmp_path):
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    flow.start([event(EventType.LAB)])
    done = flow.handle_callback('att:0:lab:none')
    assert done['attendance_complete']
    assert flow.store.subjects['ОС'].labs_enabled is False


def test_old_management_buttons_after_wizard_restart_do_not_write(tmp_path):
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    menu = flow.start([event(EventType.LAB)], edit=True)
    old = menu['buttons'][0][0]['callback_data']
    flow.start([event(EventType.LAB)])
    assert 'устарели' in flow.handle_callback(old)['text']
    assert not flow.store.path.exists()


def test_old_wizard_reset_cannot_clear_preferences_during_management_edit(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'prefs.json')
    store.set_choice('ОС', 'practical', True)
    store.save()
    before = store.path.read_bytes()
    flow = TelegramAttendanceOnboarding(store)
    flow.start([event(EventType.PRACTICAL)], edit=True)
    assert 'устарели' in flow.handle_callback('att:reset')['text']
    assert store.path.read_bytes() == before


def test_wizard_utc_lab_slot_uses_local_weekday_after_midnight(tmp_path):
    from dataclasses import replace
    from datetime import timezone
    from zoneinfo import ZoneInfo

    lab = replace(event(EventType.LAB),
                  dtstart=datetime(2026, 9, 2, 22, tzinfo=timezone.utc),
                  dtend=datetime(2026, 9, 2, 23, tzinfo=timezone.utc))
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    question = flow.start([lab])
    selected = flow.handle_callback(question['buttons'][0][0]['callback_data'])
    flow.handle_callback(selected['buttons'][-1][0]['callback_data'])
    loaded = AttendancePreferenceStore.load(flow.store.path)
    assert loaded.subjects['ОС'].lab_slots == {'3:05:00'}
    local = replace(lab, dtstart=lab.dtstart.astimezone(ZoneInfo('Asia/Tomsk')),
                    dtend=lab.dtend.astimezone(ZoneInfo('Asia/Tomsk')))
    assert loaded.preference_for(local) is True
