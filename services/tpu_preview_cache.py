"""Share validated TPU exports across users during plan previews."""
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from services.preview_timing import preview_stage
from services.tpu_schedule_source import current_tpu_group_page_url, TPU_HOST
from services.university_schedule_source import UniversityScheduleFetchError


reuse_tpu_preview = ContextVar('reuse_tpu_preview', default=False)


class TpuPreviewCache:
    TTL = 15 * 60
    MAX_ENTRIES = 128
    MAX_FETCHES = 4
    FETCH_TIMEOUT = 15

    def __init__(self):
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=self.MAX_FETCHES, thread_name_prefix='tpu-preview')
        self._entries = OrderedDict()
        self._pending = {}

    def get(self, location, load):
        deadline = monotonic() + self.FETCH_TIMEOUT
        now = datetime.now(timezone.utc)
        path = urlsplit(current_tpu_group_page_url(location, now)).path
        page = urlunsplit(('https', TPU_HOST, path, '', ''))
        with self._lock:
            stamp = monotonic()
            while self._entries and next(iter(self._entries.values()))[0] <= stamp:
                self._entries.popitem(last=False)
            if page in self._entries:
                with preview_stage('tpu_cache_hit'):
                    return self._entries[page][1]
            pending = self._pending.get(page)
            wait_stage = 'tpu_cache_wait'
            if pending is None:
                if len(self._pending) >= self.MAX_FETCHES:
                    raise UniversityScheduleFetchError('TPU preview fetch capacity exhausted')
                pending = self._pending[page] = Future()
                try:
                    self._executor.submit(copy_context().run, self._load, page, now, load, pending)
                except RuntimeError as error:
                    del self._pending[page]
                    raise UniversityScheduleFetchError('TPU preview fetch unavailable') from error
                wait_stage = 'tpu_fetch_wait'

        with preview_stage(wait_stage):
            try:
                return pending.result(timeout=max(0, deadline - monotonic()))
            except TimeoutError as error:
                raise UniversityScheduleFetchError('TPU preview fetch exceeded total wait limit') from error

    def _load(self, page, now, load, pending):
        try:
            content = load(page, now)
        except BaseException as error:
            with self._lock:
                del self._pending[page]
                pending.set_exception(error)
        else:
            with self._lock:
                self._entries[page] = (monotonic() + self.TTL, content)
                if len(self._entries) > self.MAX_ENTRIES:
                    self._entries.popitem(last=False)
                del self._pending[page]
                pending.set_result(content)


tpu_preview_cache = TpuPreviewCache()
