from datetime import datetime

from models import EventType, UniversityEvent
from services.attendance_preferences import AttendancePreferenceStore
from services.personal_schedule_projection import build_personal_calendar_events


def test_projection_contains_only_attended_events(tmp_path):
    start = datetime(2026, 9, 1, 10)
    lecture = UniversityEvent('lecture', 'ОС (ЛК)', '', '', start, start.replace(hour=11), EventType.LECTURE, True)
    practical = UniversityEvent('practical', 'ОС (ПР)', '', '', start, start.replace(hour=11), EventType.PRACTICAL, True)
    preferences = AttendancePreferenceStore(tmp_path / 'preferences.json')
    preferences.set_choice('ОС', 'lecture', True)
    preferences.set_choice('ОС', 'practical', False)

    result = build_personal_calendar_events([lecture, practical], preferences)

    assert result[0].is_matched()
    assert not result[1].is_matched()
    assert result[0].id == 'personal-university:lecture'
