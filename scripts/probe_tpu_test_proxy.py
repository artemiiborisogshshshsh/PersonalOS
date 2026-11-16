"""Check both weekly TPU exports using only the explicitly configured proxy."""
import logging
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import requests

from services.tpu_schedule_source import _error_chain, fetch_tpu_group_schedule


def safe_proxy_failure(error):
    chain = tuple(_error_chain(error))
    for cause in chain:
        match = re.search(r'Tunnel connection failed: ([1-5][0-9]{2})\b', str(cause))
        if match:
            return 'proxy_connect_http_' + match.group(1)
    for cause in reversed(chain):
        if isinstance(cause, OSError) and type(cause.errno) is int:
            return f'{type(cause).__name__} errno={cause.errno}'
    return ','.join(dict.fromkeys(type(cause).__name__ for cause in chain))


def main():
    logging.basicConfig(level=logging.WARNING)
    proxy = os.environ['TPU_TEST_PROXY_URL'].strip()
    endpoint = urlsplit(proxy)
    if endpoint.scheme not in {'http', 'https', 'socks5h'} or not endpoint.hostname or not endpoint.port:
        raise ValueError('Invalid proxy configuration')

    auth = 'configured' if endpoint.username else 'absent'
    print(f'TPU proxy probe starting: scheme={endpoint.scheme}, auth={auth}.', flush=True)
    with requests.Session() as session, TemporaryDirectory() as staging:
        session.trust_env = False
        session.proxies = {'https': proxy, 'http': proxy}
        result = fetch_tpu_group_schedule(
            'https://ro-rasp.tpu.ru/gruppa_41736/2026/7/view.html',
            Path(staging) / 'test.ics', timeout_seconds=12, session=session, auto_period=True,
        )
    print(f'TPU proxy probe succeeded: both group pages and ICS downloaded, {result.event_count} events.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('TPU proxy probe failed: ' + safe_proxy_failure(error), flush=True)
        raise SystemExit(1) from None
