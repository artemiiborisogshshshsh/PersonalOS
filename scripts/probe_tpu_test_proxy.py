"""Check both weekly TPU exports using only the explicitly configured proxy."""
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import requests

from services.tpu_schedule_source import fetch_tpu_group_schedule


def main():
    logging.basicConfig(level=logging.WARNING)
    proxy = os.environ['TPU_TEST_PROXY_URL']
    endpoint = urlsplit(proxy)
    if endpoint.scheme not in {'http', 'https', 'socks5h'} or not endpoint.hostname or not endpoint.port:
        raise ValueError('Invalid proxy configuration')

    with requests.Session() as session, TemporaryDirectory() as staging:
        session.trust_env = False
        session.proxies = {'https': proxy, 'http': proxy}
        result = fetch_tpu_group_schedule(
            'https://ro-rasp.tpu.ru/gruppa_41736/2026/7/view.html',
            Path(staging) / 'test.ics', timeout_seconds=5, session=session, auto_period=True,
        )
    print(f'TPU proxy probe succeeded: both group pages and ICS downloaded, {result.event_count} events.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('TPU proxy probe failed: ' + type(error).__name__, flush=True)
        raise SystemExit(1) from None
