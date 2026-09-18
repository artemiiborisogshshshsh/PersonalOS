import pytest

from services.user_registry import UserRegistryStore, UserStatePaths


def test_chat_binding_is_stable_and_does_not_use_raw_chat_id_for_user_state(tmp_path):
    registry = UserRegistryStore(tmp_path / "users.json")

    first = registry.get_or_create("101")
    second = registry.get_or_create("101")

    assert first == second
    assert first.id.startswith("user-")
    assert "101" not in first.id
    assert registry.resolve("101") == first


def test_one_chat_cannot_authorize_another_users_state(tmp_path):
    registry = UserRegistryStore(tmp_path / "users.json")
    first = registry.get_or_create("101")
    second = registry.get_or_create("202")

    assert registry.assert_owner(first.id, "101") == first
    with pytest.raises(PermissionError, match="does not own"):
        registry.assert_owner(first.id, "202")
    assert registry.assert_owner(second.id, "202") == second


def test_scoped_paths_reject_traversal_and_symlink_roots(tmp_path):
    paths = UserStatePaths(tmp_path)
    account = UserRegistryStore(tmp_path / "users.json").get_or_create("101")

    assert paths.directory(account.id) == tmp_path / "users" / account.id
    with pytest.raises(ValueError, match="Invalid"):
        paths.directory("../other-user")
