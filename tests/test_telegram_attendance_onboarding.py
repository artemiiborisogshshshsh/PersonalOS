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
    assert 'лабораторную' in third['text']
    done = flow.handle_callback(third['buttons'][0][0]['callback_data'])
    assert 'завершена' in done['text']
    assert done['attendance_complete'] is True
    assert 'ОС:' in done['text']
    restarted = flow.handle_callback(done['buttons'][0][0]['callback_data'])
    assert 'лекции' in restarted['text']


def test_lab_question_includes_skip_button(tmp_path):
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    first = flow.start([event(EventType.LAB, 12)])

    assert first['buttons'][-1][0]['text'] == 'Не посещаю ЛБ'
    done = flow.handle_callback(first['buttons'][-1][0]['callback_data'])
    assert 'завершена' in done['text']


def test_utc_lab_labels_use_profile_timezone_without_changing_saved_keys(tmp_path):
    from datetime import timezone
    from services.attendance_preferences import lab_slot_key
    lab = event(EventType.LAB, 5)
    lab.dtstart = datetime(2026, 9, 3, 5, 40, tzinfo=timezone.utc)
    lab.dtend = datetime(2026, 9, 3, 7, 15, tzinfo=timezone.utc)
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    question = flow.start([lab])
    assert question['buttons'][0][0]['text'] == 'Чт 12:40'
    reply = flow.handle_callback(question['buttons'][0][0]['callback_data'])
    assert 'ЛБ — Чт 12:40' in reply['text']
    assert flow.store.subjects['ОС'].lab_slots == {lab_slot_key(lab)}
    assert flow.store.preference_for(lab) is True
    flow.timezone = 'Asia/Vladivostok'
    assert 'Чт 15:40' in flow.start([lab])['text']


def test_lab_label_rolls_weekday_and_course_quotes_are_display_only(tmp_path):
    from datetime import timezone
    lab = event(EventType.LAB)
    lab.dtstart = datetime(2026, 9, 2, 22, tzinfo=timezone.utc)
    lab.summary = r'\"Код ТПУ\" (ЛБ)'
    flow = TelegramAttendanceOnboarding(AttendancePreferenceStore(tmp_path / 'prefs.json'))
    question = flow.start([lab])
    assert question['buttons'][0][0]['text'] == 'Чт 05:00'
    assert question['text'].startswith('"Код ТПУ":')
    flow.handle_callback(question['buttons'][0][0]['callback_data'])
    assert r'\"Код ТПУ\"' in flow.store.subjects
