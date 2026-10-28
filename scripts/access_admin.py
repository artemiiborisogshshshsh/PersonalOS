#!/usr/bin/env python3
"""Local-only operator status and audited free beta extension; preview by default."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.access_billing import AccessBillingStore  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True,
                        help='Existing private shared access_billing.sqlite3; no account is created')
    actions = parser.add_subparsers(dest='action', required=True)
    status = actions.add_parser('status', help='Read status for an existing opaque user ID')
    status.add_argument('user_id')
    extend = actions.add_parser('extend', help='Preview a free beta extension; write only with --apply')
    extend.add_argument('user_id')
    extend.add_argument('--days', type=int, required=True, help='Whole days, 1 through 365')
    extend.add_argument('--request-id', required=True, help='Stable unique operation ID; reuse only for exact retries')
    extend.add_argument('--reason', required=True, help='Short reason visible in user export; never include credentials')
    extend.add_argument('--apply', action='store_true', help='Commit this local operator extension')
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        # Every command first opens the existing file read-only. A typo in its
        # path or user ID cannot create a ledger or silently create an account.
        reader = AccessBillingStore(args.ledger, read_only=True)
        status = reader.known_status(args.user_id)
        if args.action == 'status':
            result = {'mode': 'read_only', 'status': status}
        else:
            extension = reader.preview_beta_extension(args.user_id, args.days, args.request_id, args.reason)
            mode = 'preview'
            if args.apply:
                writer = AccessBillingStore(args.ledger)
                extension = writer.grant_beta_extension(args.user_id, args.days, args.request_id, args.reason)
                status = writer.known_status(args.user_id)
                mode = 'applied'
            result = {'mode': mode, 'extension': extension, 'status': status}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, sqlite3.Error, ValueError):
        # Do not echo operator reasons, state, filesystem paths or raw SQL.
        print('ACCESS ADMIN FAILED: invalid request, unknown user, unsafe ledger, or storage failure', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
