#!/usr/bin/env python3
"""
Generate preparation block events in Google Calendar for university schedule.
Refactored to use dependency injection and service layer.
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.preparation.preparation_block_service import (  # noqa: E402
    create_preparation_block_service,
)


def main():
    """Main entry point for the preparation block synchronization script."""
    if len(sys.argv) < 2:
        print(
            "Usage: python prep_blocks.py <ics_file> "
            "[calendar_summary] [lead_time_hours]"
        )
        sys.exit(1)

    ics_file = sys.argv[1]
    calendar_summary = (
        sys.argv[2] if len(sys.argv) > 2 else 'University Schedule'
    )
    lead_time_hours = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

    # Credentials are supplied per user through the runtime environment.
    config = {
        'calendar': {},
        'notification': {
            # Telegram adapter will read from environment variables
        },
        'automation': {
            # n8n adapter will read from environment variables
        }
    }

    # Create service with dependency injection
    service = create_preparation_block_service(config)
    if not asyncio.run(service.calendar_adapter.initialize()):
        print(
            "Google Calendar initialization failed; synchronization was not "
            "started."
        )
        sys.exit(2)

    # Run the synchronization
    service.sync_preparation_blocks(
        ics_file_path=ics_file,
        calendar_summary=calendar_summary,
        lead_time_hours=lead_time_hours
    )


if __name__ == '__main__':
    main()
