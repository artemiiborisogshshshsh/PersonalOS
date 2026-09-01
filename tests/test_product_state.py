import pytest

from services.product_state import (
    ScheduleSource, UserProductState, UserProductStateStore,
)


def test_per_user_state_persists_source_without_raw_secret(tmp_path):
    store = UserProductStateStore(tmp_path / 'user.json')
    source = ScheduleSource('tpu', 'ics_url', 'https://example.test/feed.ics', 'TPU')
    state = UserProductState('user-1', sources={'tpu': source},
                             secret_references={'google': 'keychain:google:user-1'})
    state.activate_source('tpu')
    store.save(state)

    restored = store.load('user-1')
    assert restored.active_source_id == 'tpu'
    assert restored.secret_references['google'].startswith('keychain:')


def test_remote_source_rejects_non_http_urls():
    with pytest.raises(ValueError, match='HTTP'):
        ScheduleSource('bad', 'ics_url', 'file:///schedule.ics', 'Bad')
