from datetime import datetime
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

from services.ai.intent_interpreter import IntentInterpreter
from services.application.proposed_command import ProposedCommandService
from services.telegram_natural_text_proposals import TelegramNaturalTextProposalFlow


def flow():
    application = Mock()
    application.process_command = AsyncMock(return_value={'created': True})
    return TelegramNaturalTextProposalFlow(
        owner_chat_id='101', interpreter=IntentInterpreter(),
        proposals=ProposedCommandService(application),
    ), application


def test_natural_text_only_previews_until_matching_owner_confirms():
    instance, application = flow()
    preview = instance.propose(
        '101', 'Создай задачу купить учебник',
        now=datetime(2026, 9, 20, 12, tzinfo=ZoneInfo('Asia/Tomsk')),
    )

    assert 'купить учебник' in preview['text'].lower()
    assert preview['buttons'][0][0]['callback_data'].startswith('nl:confirm:')
    application.process_command.assert_not_awaited()
    callback = preview['buttons'][0][0]['callback_data']
    assert instance.handle_callback('202', callback) is None

    result = instance.handle_callback('101', callback)

    assert 'выполнено' in result['text']
    application.process_command.assert_awaited_once()
    assert 'уже не актуально' in instance.handle_callback('101', callback)['text']


def test_natural_text_reject_and_unknown_input_never_execute():
    instance, application = flow()
    unknown = instance.propose('101', 'удали всё завтра')
    assert 'уточнить' in unknown['text']

    preview = instance.propose('101', 'Создай задачу купить учебник')
    reject = preview['buttons'][0][1]['callback_data']
    assert 'отклонено' in instance.handle_callback('101', reject)['text']
    application.process_command.assert_not_awaited()
