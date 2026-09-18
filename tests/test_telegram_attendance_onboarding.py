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
