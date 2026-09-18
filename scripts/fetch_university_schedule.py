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
from services.tpu_schedule_source import fetch_tpu_group_schedule  # noqa: E402

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
        '--tpu-view-url',
        help='Stable public TPU group page; its temporary iCal link is refreshed automatically.',
    )
    parser.add_argument(
        '--tpu-export-variant', type=int, choices=(1, 2, 3), default=2,
        help='TPU export range: 1=week, 2=two weeks, 3=month (default: 2).',
    )
    parser.add_argument(
        '--source-file', type=Path, default=DEFAULT_SOURCE_FILE,
        help='Markdown source note containing a URL: line',
    )
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--timeout', type=float, default=20.0)
    args = parser.parse_args()

    try:
        if args.tpu_view_url:
            result = fetch_tpu_group_schedule(
                args.tpu_view_url, args.output, args.timeout,
                export_variant_id=args.tpu_export_variant,
            )
        else:
            source_url = args.url or read_feed_url(args.source_file)
            result = fetch_university_schedule(
                source_url, args.output, args.timeout,
            )
    except (ValueError, UniversityScheduleFetchError):
        print('University schedule fetch failed. Check the source and try again.', file=sys.stderr)
        return 2

    print(f'Downloaded {result.event_count} events to {result.output_path}')
    print(f'Content hash: {result.content_hash}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
