from pathlib import Path
from unittest.mock import Mock

from services.telegram_user_router import TelegramUserRouter
from services.user_registry import UserRegistryStore, UserStatePaths


def test_router_scopes_a_handler_to_each_authenticated_chat(tmp_path):
    created = []
    def factory(account, directory):
        handler = Mock()
        handler.handle_text.side_effect = lambda chat, text: f'{account.id}:{directory.name}:{chat}:{text}'
        created.append((account, directory, handler))
        return handler
    router = TelegramUserRouter(UserRegistryStore(tmp_path / 'registry.json'), UserStatePaths(tmp_path), factory)

    first = router.handle_text('101', '/start')
    again = router.handle_text('101', '/update_all')
    second = router.handle_text('202', '/start')

    assert len(created) == 2
    assert first.split(':')[0] == again.split(':')[0]
    assert first.split(':')[0] != second.split(':')[0]
    assert created[0][1] != created[1][1]


def test_callback_cannot_select_another_users_handler(tmp_path):
    handlers = {}
    def factory(account, _directory):
        handler = Mock(); handler.handle_callback.return_value = account.id; handlers[account.id] = handler; return handler
    router = TelegramUserRouter(UserRegistryStore(tmp_path / 'registry.json'), UserStatePaths(tmp_path), factory)
    first = router.handle_callback('101', 'ua:any:page:0')
    second = router.handle_callback('202', 'ua:any:page:0')
    assert first != second
