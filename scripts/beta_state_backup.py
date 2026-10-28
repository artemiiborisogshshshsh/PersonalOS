#!/usr/bin/env python3
"""Offline operator backup/restore; never starts providers or the bot."""
from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys
from zipfile import BadZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.beta_state_backup import BetaStateBackupService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    backup = commands.add_parser('backup', help='Stop all writers first')
    backup.add_argument('--state-dir', type=Path, required=True)
    backup.add_argument('--archive', type=Path, required=True)
    restore = commands.add_parser('restore', help='Create an isolated new state directory')
    restore.add_argument('--archive', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(argv)
    service = BetaStateBackupService()
    try:
        if args.action == 'backup':
            result = service.backup(args.state_dir, args.archive)
            count = len(result['files'])
        else:
            result = service.restore(args.archive, args.destination)
            count = len(result['restored_files'])
    except (OSError, ValueError, KeyError, TypeError, BadZipFile, sqlite3.Error):
        # Contents, paths and exception details may disclose private user data.
        print(f'{args.action.upper()} FAILED: stop writers; check archive, scope and new destination', file=sys.stderr)
        return 1
    print(f'{args.action.upper()} OK: {count} operational state files; no providers started')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
