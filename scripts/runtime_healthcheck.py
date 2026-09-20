#!/usr/bin/env python3
"""Non-invasive readiness check for the deployed Telegram runtime."""

from __future__ import annotations

import os
from pathlib import Path
import re
from urllib.parse import urlparse


def main() -> int:
    missing = [
        name for name in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID')
        if not os.environ.get(name)
    ]
    data_dir = Path(os.environ.get('PERSONAL_OS_DATA_DIR', 'data'))
    source_url = os.environ.get('UNIVERSITY_SCHEDULE_URL', '')
    tpu_view_url = os.environ.get('TPU_SCHEDULE_VIEW_URL', '')
    if missing:
        print('NOT READY: missing ' + ', '.join(missing))
        return 1
    if not re.fullmatch(r'[1-9][0-9]{0,19}', os.environ['TELEGRAM_CHAT_ID']):
        print('NOT READY: TELEGRAM_CHAT_ID is invalid')
        return 1
    if source_url:
        parsed = urlparse(source_url)
        if parsed.scheme != 'https' or not parsed.netloc:
            print('NOT READY: UNIVERSITY_SCHEDULE_URL must be an HTTPS URL')
            return 1
    if tpu_view_url:
        from services.tpu_schedule_source import validate_tpu_group_page_url
        try:
            validate_tpu_group_page_url(tpu_view_url)
        except ValueError:
            print('NOT READY: TPU_SCHEDULE_VIEW_URL is not a valid TPU group page')
            return 1
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / '.healthcheck'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
    except OSError:
        print('NOT READY: data directory is not writable')
        return 1
    print('READY: runtime configuration and data directory are valid')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
