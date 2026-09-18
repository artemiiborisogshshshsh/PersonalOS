import json
import pytest

from services.product_analytics import ProductAnalyticsStore


def test_lifecycle_funnel_is_idempotent_and_contains_no_product_content(tmp_path):
    store = ProductAnalyticsStore(tmp_path / 'analytics.json')
    assert store.record_once('user-a', 'registered')
    assert not store.record_once('user-a', 'registered')
    assert store.record_once('user-a', 'source_connected')
    assert store.record_once('user-b', 'registered')
    assert store.funnel_counts()['registered'] == 2
    assert store.funnel_counts()['source_connected'] == 1
    assert set(json.loads((tmp_path / 'analytics.json').read_text())['events'][0]) == {
        'user_id', 'name', 'occurred_at',
    }


def test_analytics_rejects_arbitrary_payload_event_names(tmp_path):
    with pytest.raises(ValueError, match='Unsupported'):
        ProductAnalyticsStore(tmp_path / 'analytics.json').record_once('user-a', 'Math lecture at 10:00')
