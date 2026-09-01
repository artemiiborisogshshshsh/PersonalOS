#!/usr/bin/env python3
"""Run the Telegram command bot for manually refreshing the TPU schedule."""

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_university_schedule import read_feed_url  # noqa: E402
from services.telegram_schedule_bot import TelegramScheduleBot  # noqa: E402
from services.attendance_preferences import (  # noqa: E402
    AttendancePreferenceStore,
)
from services.telegram_attendance_onboarding import (  # noqa: E402
    TelegramAttendanceOnboarding,
)
from scripts.parse_ics import parse_ics  # noqa: E402
from models import (  # noqa: E402
    EventType, UniversityEvent, UniversityEventStatus,
)
from services.calendar.personal_event_sync_service import (  # noqa: E402
    create_personal_event_sync_service,
)
from services.personal_schedule_projection import (  # noqa: E402
    build_personal_calendar_events,
)


def load_events(path: Path) -> list[UniversityEvent]:
    return [
        UniversityEvent(
            uid=item['uid'], summary=item['summary'],
            description=item['description'], location=item['location'],
            dtstart=item['dtstart'], dtend=item['dtend'],
            event_type=EventType.from_string(item['event_type'] or ''),
            is_group_event=item['is_group_event'],
            status=UniversityEventStatus.from_string(item['status']),
            sequence=item['sequence'],
        )
        for item in parse_ics(path)
    ]


def selected_events(schedule_path: Path, preferences_path: Path):
    preferences = AttendancePreferenceStore.load(preferences_path)
    return [
        event for event in build_personal_calendar_events(
            load_events(schedule_path), preferences,
        ) if event.is_matched()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Run the Telegram bot for /update_schedule.'
    )
    parser.add_argument(
        '--source-file', type=Path,
        default=PROJECT_ROOT / '01-University/03-Schedule/TPU-iCal-source.md',
    )
    parser.add_argument(
        '--output', type=Path,
        default=PROJECT_ROOT / 'data/university_schedule.ics',
    )
    args = parser.parse_args()
    try:
        bot = TelegramScheduleBot.from_environment(
            source_url=read_feed_url(args.source_file),
            output_path=args.output,
        )
    except ValueError as error:
        print(
            f'Telegram schedule bot configuration error: {error}',
            file=sys.stderr,
        )
        return 2
    preferences = AttendancePreferenceStore.load(
        PROJECT_ROOT / 'data/attendance_preferences.json'
    )
    bot.attendance_onboarding = TelegramAttendanceOnboarding(preferences)
    bot.events_loader = lambda: load_events(args.output)
    preferences_path = PROJECT_ROOT / 'data/attendance_preferences.json'
    bot.calendar_preview = lambda: '\n'.join(
        ['События для Google Calendar:'] + [
            f'• {event.start_time:%d.%m %H:%M} — {event.title}'
            for event in selected_events(args.output, preferences_path)
        ]
    )

    def apply_calendar() -> str:
        service = create_personal_event_sync_service({'calendar': {}})
        if not asyncio.run(service.calendar_adapter.initialize()):
            return 'Не удалось авторизоваться в Google Calendar.'
        events = selected_events(args.output, preferences_path)
        service.sync_personal_events_to_calendar(
            events, 'Personal University Schedule',
        )
        return f'Готово: синхронизировано {len(events)} событий.'

    bot.calendar_apply = apply_calendar
    print('Telegram bot started. Send /update_schedule or /attendance.')
    bot.run_forever()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
