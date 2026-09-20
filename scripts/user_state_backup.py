#!/usr/bin/env python3
"""Create or restore one allow-listed PersonalOS user-state archive."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from zipfile import BadZipFile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.user_state_backup import UserStateBackupService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Back up or restore allow-listed PersonalOS user state.',
    )
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('action', choices=('backup', 'restore'))
    parser.add_argument('user_id')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = UserStateBackupService(args.data_dir)
    try:
        if args.action == 'backup':
            result = service.backup(args.user_id, args.archive)
            print(f'BACKUP OK: {len(result["files"])} state files')
        else:
            result = service.restore(args.user_id, args.archive)
            print(f'RESTORE OK: {len(result["restored_files"])} state files')
    except FileExistsError:
        print('RESTORE FAILED: existing state would be overwritten', file=sys.stderr)
        return 1
    except (BadZipFile, KeyError, OSError, UnicodeError, ValueError):
        # Paths, archive contents and state values may be sensitive. Keep the
        # operational error categorical and inspect the disposable copy only.
        print(f'{args.action.upper()} FAILED: invalid archive, scope or storage', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
