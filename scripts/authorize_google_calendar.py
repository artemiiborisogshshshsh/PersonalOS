#!/usr/bin/env python3
"""Run the one-time Google Calendar OAuth flow without synchronizing events."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.google_calendar_adapter import GoogleCalendarAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='Authorize Google Calendar once.')
    parser.add_argument(
        '--credentials',
        default=os.environ.get('GOOGLE_CALENDAR_CREDENTIALS_PATH'),
        help='Path to OAuth client_secret JSON.',
    )
    parser.add_argument(
        '--token',
        default=os.environ.get('GOOGLE_CALENDAR_TOKEN_PATH'),
        help='Writable path for the per-user OAuth token.',
    )
    args = parser.parse_args()
    if not args.credentials or not args.token:
        parser.error('set --credentials/--token or GOOGLE_CALENDAR_*_PATH')
    try:
        GoogleCalendarAdapter({
            'credentials_path': args.credentials,
            'token_path': args.token,
        })._get_calendar_service()
    except Exception:
        print('Google Calendar authorization failed. Check the private OAuth configuration.', file=sys.stderr)
        return 1
    print('Google Calendar authorized. The token was saved to the selected token path.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
