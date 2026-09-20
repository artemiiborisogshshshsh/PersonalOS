from unittest.mock import Mock

from services.product_analytics import ProductAnalyticsStore
from services.telegram_multi_user_runtime import TelegramMultiUserDispatcher
from services.telegram_user_router import TelegramUserRouter
from services.user_registry import UserRegistryStore, UserStatePaths


def dispatcher(tmp_path, *, analytics=None):
    def factory(_account, _directory):
        handler = Mock()
        handler.handle_text.return_value = {'text': 'ok', 'buttons': []}
        handler.handle_callback.return_value = {'text': 'callback', 'buttons': []}
        return handler
    router = TelegramUserRouter(
        UserRegistryStore(tmp_path / 'registry.json'), UserStatePaths(tmp_path), factory,
        analytics=analytics,
    )
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


def test_registration_analytics_is_recorded_once_only_after_start(tmp_path):
    analytics = ProductAnalyticsStore(tmp_path / 'analytics.json')
    runtime, router = dispatcher(tmp_path, analytics=analytics)

    assert runtime.handle_update({'message': {'chat': {'id': 101}, 'text': '/update_all'}}) is None
    assert analytics.funnel_counts()['registered'] == 0

    runtime.handle_update({'message': {'chat': {'id': 101}, 'text': '/start'}})
    runtime.handle_update({'message': {'chat': {'id': 101}, 'text': '/start'}})

    assert router.registry.resolve('101') is not None
    assert analytics.funnel_counts()['registered'] == 1
    assert '101' not in analytics.path.read_text(encoding='utf-8')
