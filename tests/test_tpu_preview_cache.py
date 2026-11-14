from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

import pytest

from services import tpu_preview_cache as module
from services.tpu_schedule_source import _ProxyPoolManager


PAGE = 'https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html'


def test_shared_cache_uses_effective_week_and_expires():
    cache = module.TpuPreviewCache()
    load = Mock(side_effect=[b'first', b'other group', b'fresh'])
    with patch.object(module, 'monotonic', return_value=100) as clock:
        assert cache.get(PAGE, load) == b'first'
        assert cache.get(PAGE.replace('/1/', '/2/'), load) == b'first'
        assert cache.get(PAGE.replace('41736', '41737'), load) == b'other group'
        clock.return_value += 900
        assert cache.get(PAGE, load) == b'fresh'
    assert load.call_count == 3


def test_concurrent_preview_shares_fetch_and_failed_fetch_can_retry():
    cache = module.TpuPreviewCache()
    entered, release = Event(), Event()

    def fetch(_page, _now):
        entered.set()
        assert release.wait(5)
        return b'schedule'

    load = Mock(side_effect=fetch)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(cache.get, PAGE, load)
        assert entered.wait(5)
        second = workers.submit(cache.get, PAGE, load)
        release.set()
        assert first.result(timeout=5) == second.result(timeout=5) == b'schedule'
    load.assert_called_once()

    other = PAGE.replace('41736', '41737')
    with pytest.raises(ValueError, match='offline'):
        cache.get(other, Mock(side_effect=ValueError('offline')))
    assert cache.get(other, Mock(return_value=b'recovered')) == b'recovered'


def test_proxy_warmup_keeps_fresh_pool_usable_during_refresh():
    pool = _ProxyPoolManager()
    old, new = 'http://1.1.1.1:80', 'http://8.8.8.8:80'
    started, release, finished = Event(), Event(), Event()

    def refresh():
        started.set()
        assert release.wait(5)
        return old, new

    with patch('services.tpu_schedule_source.time.monotonic', return_value=100):
        with patch.object(pool, '_refresh', return_value=(old,)):
            assert pool.get() == (old,)
    with patch('services.tpu_schedule_source.time.monotonic', return_value=pool._expires_at - 10):
        with patch.object(pool, '_refresh', side_effect=refresh) as refresh_mock, patch(
            'services.tpu_schedule_source.logger.info', side_effect=lambda *_: finished.set()
        ):
            pool.warm()
            assert started.wait(5)
            pool.warm()
            try:
                with ThreadPoolExecutor(max_workers=1) as workers:
                    assert workers.submit(pool.get).result(timeout=1) == (old,)
                pool.mark_bad(old)
            finally:
                release.set()
            assert finished.wait(5)
            assert pool.get() == (new,)
            refresh_mock.assert_called_once()


def test_total_wait_is_bounded_and_next_preview_can_use_completed_fetch():
    from services.university_schedule_source import UniversityScheduleFetchError

    cache = module.TpuPreviewCache()
    cache.FETCH_TIMEOUT = 0.05
    started, release = Event(), Event()

    def slow_load(_page, _now):
        started.set()
        assert release.wait(5)
        return b'schedule'

    load = Mock(side_effect=slow_load)
    try:
        for _ in range(2):
            with pytest.raises(UniversityScheduleFetchError, match='total wait limit'):
                cache.get(PAGE, load)
            assert started.is_set()
        load.assert_called_once()
    finally:
        release.set()
    cache.FETCH_TIMEOUT = 5
    assert cache.get(PAGE, load) == b'schedule'
    load.assert_called_once()
