"""Build the calendar projection from saved attendance choices."""

from typing import Iterable, List

from models import PersonalAttendanceRule, PersonalUniversityEvent, UniversityEvent
from services.attendance_preferences import AttendancePreferenceStore
from services.attendance_service import AttendanceRuleService


def build_personal_calendar_events(
    events: Iterable[UniversityEvent], preferences: AttendancePreferenceStore,
) -> List[PersonalUniversityEvent]:
    rule = PersonalAttendanceRule(
        id='telegram-attendance-preferences',
        description='Saved Telegram attendance choices',
        metadata={'attendance_preferences': preferences.as_rule_metadata()},
    )
    personal_events = AttendanceRuleService(rule).find_personal_events(list(events))
    for event in personal_events:
        # Source UIDs remain stable through schedule moves and make Google
        # Calendar idempotency unambiguous, including repeated lab sessions.
        event.id = f'personal-university:{event.university_event_uid}'
    return personal_events
