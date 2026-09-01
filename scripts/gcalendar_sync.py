#!/usr/bin/env python3
"""
Google Calendar sync utilities for Personal OS.
Handles authentication and event insertion/update using iCalUID
for deduplication.
Refactored to use dependency injection and service layer.
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.calendar import calendar_sync_service  # noqa: E402


def main():
    """Main entry point for the calendar synchronization script."""
    if len(sys.argv) < 2:
        print("Usage: python gcalendar_sync.py <ics_file> [calendar_summary]")
        sys.exit(1)

    ics_file = sys.argv[1]
    calendar_summary = (
        sys.argv[2] if len(sys.argv) > 2 else 'University Schedule'
    )

    # Configure adapter
    config = {
        'calendar': {
            'credentials_path': '~/.config/google/credentials.json',
            'token_path': '~/.config/google/token.json'
        }
    }

    # Create service with dependency injection
    service = calendar_sync_service.create_calendar_sync_service(config)
    if not asyncio.run(service.calendar_adapter.initialize()):
        print(
            "Google Calendar initialization failed; synchronization was not "
            "started."
        )
        sys.exit(2)

    # Run the synchronization
    service.sync_ics_to_gcalendar(
        ics_file_path=ics_file,
        calendar_summary=calendar_summary
    )


if __name__ == '__main__':
    main()
