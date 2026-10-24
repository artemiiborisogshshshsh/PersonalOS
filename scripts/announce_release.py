#!/usr/bin/env python3
"""Explicitly validate and deliver the configured release announcement."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.release_delivery import delivery_from_environment, release_artifact_fingerprint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fingerprint', action='store_true',
                        help='print the local release artifact fingerprint without network access')
    args = parser.parse_args(argv)
    if args.fingerprint:
        value = release_artifact_fingerprint(Path(__file__).resolve().parent.parent)
        if value == 'unavailable':
            print('error')
            return 1
        print(value)
        return 0
    try:
        result = delivery_from_environment().check_once()
    except Exception:
        result = {'status': 'error'}
    status = result.get('status', 'error')
    print(status)
    if status in {'sent', 'already_sent'}:
        message_id = result.get('message_id')
        if isinstance(message_id, int) and not isinstance(message_id, bool):
            print(f'message_id={message_id}')
        return 0
    if status == 'rejected':
        code = result.get('error_code')
        if isinstance(code, int) and not isinstance(code, bool):
            print(f'error_code={code}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
