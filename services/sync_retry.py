"""Bounded retries for reads; callers must reconcile ambiguous writes first."""

import random
import ssl
import time

import requests


def transient_error(error):
    """Inspect wrapped provider errors without logging credentials or URLs."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        response = getattr(error, 'resp', None)
        status = getattr(response, 'status', None)
        if status is None:
            status = getattr(getattr(error, 'response', None), 'status_code', None)
        if status is not None:
            return status == 429 or status in (500, 502, 503, 504)
        if isinstance(error, (ssl.SSLError, ConnectionError, TimeoutError,
                              requests.ConnectionError, requests.Timeout)):
            return True
        error = error.__cause__
    return False


def retry_read(action, *, reconnect=lambda: None, sleep=time.sleep, jitter=random.random):
    for attempt in range(10):
        try:
            return action()
        except Exception as error:
            if not transient_error(error) or attempt == 9:
                raise
            reconnect()
            sleep(min(30.0, 2 ** attempt) * (0.5 + 0.5 * jitter()))
