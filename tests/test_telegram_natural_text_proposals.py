from datetime import datetime
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

from services.ai.intent_interpreter import IntentInterpreter
from services.application.proposed_command import ProposedCommandService
from services.natural_text_application import (
    NaturalTextProposalStore, PerUserNaturalCommandExecutor,
)
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


def test_pending_proposal_survives_restart_and_executes_once_into_local_user_state(tmp_path):
    proposal_store = NaturalTextProposalStore(tmp_path / 'proposal.json')
    executor = PerUserNaturalCommandExecutor(tmp_path / 'commands.json')
    first = TelegramNaturalTextProposalFlow(
        '101', IntentInterpreter(), ProposedCommandService(executor), proposal_store,
    )
    preview = first.propose('101', 'Создай задачу купить учебник')
    callback = preview['buttons'][0][0]['callback_data']

    restarted = TelegramNaturalTextProposalFlow(
        '101', IntentInterpreter(), ProposedCommandService(executor), proposal_store,
    )
    assert 'выполнено' in restarted.handle_callback('101', callback)['text']
    assert len(executor.snapshot()['tasks']) == 1
    assert executor.snapshot()['personal_events'] == []
    assert 'Created from:' not in (tmp_path / 'commands.json').read_text(encoding='utf-8')
    assert proposal_store.load() is None

    assert 'уже не актуально' in restarted.handle_callback('101', callback)['text']
    assert len(executor.snapshot()['tasks']) == 1


def test_personal_event_is_internal_state_and_not_a_group_or_calendar_write(tmp_path):
    proposal_store = NaturalTextProposalStore(tmp_path / 'proposal.json')
    executor = PerUserNaturalCommandExecutor(tmp_path / 'commands.json')
    instance = TelegramNaturalTextProposalFlow(
        '101', IntentInterpreter(), ProposedCommandService(executor), proposal_store,
    )
    preview = instance.propose(
        '101', 'Запланируй встречу завтра в 18:30',
        now=datetime(2026, 9, 20, 12, tzinfo=ZoneInfo('Asia/Tomsk')),
    )
    instance.handle_callback('101', preview['buttons'][0][0]['callback_data'])

    state = executor.snapshot()
    assert state['tasks'] == []
    assert len(state['personal_events']) == 1
    assert state['personal_events'][0]['start'].startswith('2026-09-21T18:30')
    assert not (tmp_path / 'calendar').exists()
