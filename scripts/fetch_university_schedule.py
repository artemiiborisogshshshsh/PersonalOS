#!/usr/bin/env python3
"""Download the official university iCalendar feed into a local ICS file."""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.university_schedule_source import (  # noqa: E402
    UniversityScheduleFetchError,
    fetch_university_schedule,
)

DEFAULT_SOURCE_FILE = (
    PROJECT_ROOT / '01-University/03-Schedule/TPU-iCal-source.md'
)
DEFAULT_OUTPUT = PROJECT_ROOT / 'data/university_schedule.ics'


def read_feed_url(source_file: Path) -> str:
    """Read the ``URL:`` value from the local university source note."""
    try:
        contents = source_file.read_text(encoding='utf-8')
    except OSError as error:
        raise UniversityScheduleFetchError(
            f'Could not read source configuration: {source_file}'
        ) from error

    match = re.search(r'^URL:\s*(https?://\S+)\s*$', contents, re.MULTILINE)
    if match is None:
        raise UniversityScheduleFetchError(
            f'No HTTP(S) URL found in {source_file}'
        )
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Download and validate an official university ICS feed.'
    )
    parser.add_argument('--url', help='Direct official iCalendar feed URL')
    parser.add_argument(
        '--source-file', type=Path, default=DEFAULT_SOURCE_FILE,
        help='Markdown source note containing a URL: line',
    )
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--timeout', type=float, default=20.0)
    args = parser.parse_args()

    try:
        source_url = args.url or read_feed_url(args.source_file)
        result = fetch_university_schedule(
            source_url, args.output, args.timeout,
        )
    except (ValueError, UniversityScheduleFetchError) as error:
        print(f'University schedule fetch failed: {error}', file=sys.stderr)
        return 2

    print(f'Downloaded {result.event_count} events to {result.output_path}')
    print(f'Content hash: {result.content_hash}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
