from datetime import datetime

from models import EventType, PersonalAttendanceRule, UniversityEvent
from services.attendance_preferences import AttendancePreferenceStore, lab_slot_key
from services.attendance_service import AttendanceRuleService


def event(event_type, start=datetime(2026, 9, 2, 10, 15)):
    return UniversityEvent(
        uid='event', summary='ОС (ЛБ)', description='', location='',
        dtstart=start, dtend=start.replace(hour=11), event_type=event_type,
        is_group_event=True,
    )


def test_store_persists_type_and_lab_slot_choices(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'preferences.json')
    store.set_choice('ОС', 'lecture', True)
    store.set_choice('ОС', 'practical', False)
    store.set_choice('ОС', 'lab', lab_slot_key(event(EventType.LAB)))
    store.save()

    loaded = AttendancePreferenceStore.load(store.path)
    assert loaded.preference_for(event(EventType.LAB)) is True
    assert loaded.preference_for(event(EventType.LECTURE)) is True
    assert loaded.preference_for(event(EventType.PRACTICAL)) is False


def test_attendance_service_excludes_event_selected_as_not_attending():
    rule = PersonalAttendanceRule(
        id='me', description='my choices', metadata={
            'attendance_preferences': {'ОС': {'lectures': False}}
        },
    )

    result = AttendanceRuleService(rule).evaluate_event(event(EventType.LECTURE))

    assert result.personal_event_id is None


def test_unconfigured_subject_stays_expected_until_the_student_chooses():
    result = AttendanceRuleService(PersonalAttendanceRule(
        id='me', description='my choices', metadata={'attendance_preferences': {}},
    )).find_personal_events([event(EventType.LECTURE)])[0]

    assert result.state.value == 'expected'
    assert result.university_event_uid == 'event'


def test_lab_can_be_explicitly_disabled_for_every_week(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'preferences.json')
    store.set_choice('ОС', 'lab', False)
    store.save()

    assert AttendancePreferenceStore.load(store.path).preference_for(
        event(EventType.LAB)
    ) is False


def test_lecture_opt_out_excludes_every_session_type_for_the_course(tmp_path):
    store = AttendancePreferenceStore(tmp_path / 'preferences.json')
    store.set_choice('ОС', 'lecture', False)

    assert store.preference_for(event(EventType.LECTURE)) is False
    assert store.preference_for(event(EventType.PRACTICAL)) is False
    assert store.preference_for(event(EventType.LAB)) is False
