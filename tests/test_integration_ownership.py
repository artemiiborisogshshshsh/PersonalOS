import pytest
from services.integration_ownership import IntegrationOwnershipService
from services.product_state import UserProductStateStore


def test_integration_reference_is_scoped_to_one_user_and_never_contains_raw_token(tmp_path):
    store = UserProductStateStore(tmp_path / 'state.json')
    service = IntegrationOwnershipService(store)
    service.bind('user-a', 'google_calendar', 'keychain:google:user-a')
    assert service.reference_for('user-a', 'google_calendar') == 'keychain:google:user-a'
    with pytest.raises(ValueError, match='opaque'):
        service.bind('user-a', 'google_calendar', 'ya29.real-token')
    with pytest.raises(ValueError, match='different user'):
        store.load('user-b')
