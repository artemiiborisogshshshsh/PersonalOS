"""Collect timings for one Telegram preview without logging user data."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from time import monotonic, time


logger = logging.getLogger(__name__)
stages = ContextVar('preview_stages', default=None)
commands = {'/weekly_preview', '/update_schedule'}


@contextmanager
def measure_preview(update):
    message = update.get('message') or {}
    text = message.get('text')
    words = text.split(maxsplit=1) if isinstance(text, str) else []
    command = words[0].split('@', 1)[0] if words else ''
    if command not in commands:
        yield
        return

    timings = {}
    token = stages.set(timings)
    started = monotonic()
    sent = message.get('date')
    delay = round(max(0, time() - sent) * 1000) if type(sent) is int else 'unknown'
    try:
        yield
    finally:
        stages.reset(token)
        logger.info('Preview timing command=%s telegram_to_handler_ms=%s handler_ms=%d %s',
                    command, delay, round((monotonic() - started) * 1000),
                    ' '.join(f'{stage}_ms={round(seconds * 1000)}'
                             for stage, seconds in timings.items()))


@contextmanager
def preview_stage(name):
    timings = stages.get()
    if timings is None:
        yield
        return
    started = monotonic()
    try:
        yield
    finally:
        timings[name] = timings.get(name, 0) + monotonic() - started
