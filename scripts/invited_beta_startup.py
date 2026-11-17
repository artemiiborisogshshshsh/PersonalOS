"""Start the bot after an explicitly configured, supervised local checkpoint.

The optional operator file lives on the private runtime volume, never in git.
An absent/malformed/rejected repair must not take the existing bot offline.
"""
from pathlib import Path
import base64
import json
import os
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
OPERATOR_FILE = Path('/data/operator_partial_checkpoint.json')
OPERATOR_ENV = 'PERSONAL_OS_OPERATOR_CHECKPOINT'
FIELDS = {'user_id', 'expected_digest', 'old_rows', 'new_classes',
          'new_preparations', 'absent_preparations'}


def checkpoint_arguments(path, raw=None):
    if raw is None:
        if not path.exists():
            return None
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 4096
                or path.stat().st_mode & 0o022):
            raise ValueError('Invalid operator checkpoint file')
        raw = path.read_text(encoding='utf-8')
    if len(raw.encode('utf-8')) > 4096:
        raise ValueError('Oversized checkpoint configuration')
    if raw.startswith('base64:'):
        raw = base64.b64decode(raw[7:], validate=True).decode('utf-8')
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('Invalid checkpoint configuration')
    if not isinstance(value['user_id'], str) or not re.fullmatch(r'user-[0-9a-f]{24}', value['user_id']):
        raise ValueError('Invalid checkpoint user')
    if not isinstance(value['expected_digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['expected_digest']):
        raise ValueError('Invalid checkpoint digest')
    if any(type(value[field]) is not int or not 0 <= value[field] <= 10000
           for field in FIELDS - {'user_id', 'expected_digest'}):
        raise ValueError('Invalid checkpoint counts')
    args = ['--apply']
    for field in sorted(FIELDS):
        args.extend(['--' + field.replace('_', '-'), str(value[field])])
    return args


def main():
    try:
        args = checkpoint_arguments(OPERATOR_FILE, os.environ.get(OPERATOR_ENV))
        if args:
            completed = subprocess.run(
                [sys.executable, '-u', str(ROOT / 'scripts/checkpoint_partial_plan.py'), *args],
                cwd=ROOT, timeout=180, check=False,
            )
            print('Operator checkpoint finished with status ' + str(completed.returncode), flush=True)
    except Exception as error:
        # Never log provider payloads, config, URLs, tokens or account details.
        print('Operator checkpoint skipped: ' + type(error).__name__, flush=True)
    os.execv(sys.executable, [sys.executable, '-u', str(ROOT / 'scripts/invited_beta_bot.py'), 'run'])


if __name__ == '__main__':
    main()
