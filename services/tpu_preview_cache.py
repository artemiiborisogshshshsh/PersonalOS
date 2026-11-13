"""Share validated TPU exports across users during plan previews."""
from collections import OrderedDict
from concurrent.futures import Future
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from services.preview_timing import preview_stage
from services.tpu_schedule_source import current_tpu_group_page_url, TPU_HOST


reuse_tpu_preview = ContextVar('reuse_tpu_preview', default=False)


class TpuPreviewCache:
    TTL = 15 * 60
    MAX_ENTRIES = 128

    def __init__(self):
        self._lock = Lock()
        self._entries = OrderedDict()
        self._pending = {}

    def get(self, location, load):
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
            if pending is None:
                pending = self._pending[page] = Future()
                owner = True
            else:
                owner = False

        if not owner:
            with preview_stage('tpu_cache_wait'):
                return pending.result()

        try:
            content = load(page, now)
        except BaseException as error:
            pending.set_exception(error)
            raise
        else:
            with self._lock:
                self._entries[page] = (monotonic() + self.TTL, content)
                if len(self._entries) > self.MAX_ENTRIES:
                    self._entries.popitem(last=False)
            pending.set_result(content)
            return content
        finally:
            with self._lock:
                del self._pending[page]


tpu_preview_cache = TpuPreviewCache()
