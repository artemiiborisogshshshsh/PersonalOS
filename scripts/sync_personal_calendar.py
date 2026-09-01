#!/usr/bin/env python3
# flake8: noqa
"""Preview or sync only the university events selected in Telegram."""

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models import EventType, UniversityEvent, UniversityEventStatus  # noqa: E402
from scripts.parse_ics import parse_ics  # noqa: E402
from services.attendance_preferences import AttendancePreferenceStore  # noqa: E402
from services.calendar.personal_event_sync_service import (  # noqa: E402
    create_personal_event_sync_service,
)
from services.personal_schedule_projection import build_personal_calendar_events  # noqa: E402


def load_events(path: Path) -> list[UniversityEvent]:
    return [UniversityEvent(
        uid=item['uid'], summary=item['summary'], description=item['description'],
        location=item['location'], dtstart=item['dtstart'], dtend=item['dtend'],
        event_type=EventType.from_string(item['event_type'] or ''),
        is_group_event=item['is_group_event'],
        status=UniversityEventStatus.from_string(item['status']), sequence=item['sequence'],
    ) for item in parse_ics(path)]


def main() -> int:
    parser = argparse.ArgumentParser(description='Sync Telegram-selected university events.')
    parser.add_argument('--apply', action='store_true', help='Write the preview to Google Calendar')
    args = parser.parse_args()
    events = build_personal_calendar_events(
        load_events(PROJECT_ROOT / 'data/university_schedule.ics'),
        AttendancePreferenceStore.load(PROJECT_ROOT / 'data/attendance_preferences.json'),
    )
    selected = [event for event in events if event.is_matched()]
    print(f'Preview: {len(selected)} selected of {len(events)} university events.')
    for event in selected:
        print(f'• {event.start_time:%d.%m %H:%M} — {event.title}')
    if not args.apply:
        print('Preview only. Run again with --apply to sync to Google Calendar.')
        return 0
    service = create_personal_event_sync_service({'calendar': {}})
    if not asyncio.run(service.calendar_adapter.initialize()):
        print('Google Calendar initialization failed.', file=sys.stderr)
        return 2
    service.sync_personal_events_to_calendar(selected, 'Personal University Schedule')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
