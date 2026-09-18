from unittest.mock import Mock

from services.telegram_multi_user_runtime import TelegramMultiUserDispatcher
from services.telegram_user_router import TelegramUserRouter
from services.user_registry import UserRegistryStore, UserStatePaths


def dispatcher(tmp_path):
    def factory(_account, _directory):
        handler = Mock()
        handler.handle_text.return_value = {'text': 'ok', 'buttons': []}
        handler.handle_callback.return_value = {'text': 'callback', 'buttons': []}
        return handler
    router = TelegramUserRouter(UserRegistryStore(tmp_path / 'registry.json'), UserStatePaths(tmp_path), factory)
    return TelegramMultiUserDispatcher(router), router


def test_unknown_chat_can_register_only_via_start(tmp_path):
    runtime, router = dispatcher(tmp_path)
    assert runtime.handle_update({'message': {'chat': {'id': 101}, 'text': '/update_all'}}) is None
    assert router.registry.resolve('101') is None
    assert runtime.handle_update({'message': {'chat': {'id': 101}, 'text': '/start'}})[1]['text'] == 'ok'
    assert router.registry.resolve('101') is not None


def test_callback_stays_in_existing_chat_scope(tmp_path):
    runtime, router = dispatcher(tmp_path)
    runtime.handle_update({'message': {'chat': {'id': 101}, 'text': '/start'}})
    assert runtime.handle_update({'callback_query': {'data': 'ob:timezone:Asia/Tomsk', 'message': {'chat': {'id': 101}}}})[1]['text'] == 'callback'
    assert runtime.handle_update({'callback_query': {'data': 'ob:timezone:Asia/Tomsk', 'message': {'chat': {'id': 202}}}}) is None
