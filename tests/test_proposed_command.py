from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from services.application.command import CreateUniversityEventCommand
from services.application.proposed_command import (
    ProposalStatus,
    ProposedCommand,
    ProposedCommandService,
)


def command(summary='Лекция'):
    start = datetime(2026, 9, 1, 10, 0)
    return CreateUniversityEventCommand(
        event_type='lecture',
        summary=summary,
        description='Описание',
        location='101',
        dtstart=start,
        dtend=start + timedelta(hours=1),
    )


async def test_ai_proposal_requires_validation_and_explicit_approval():
    application = Mock()
    application.process_command = AsyncMock(return_value={'created': True})
    service = ProposedCommandService(application)
    proposal = ProposedCommand(command(), 'создай лекцию', 'Create lecture', 0.9)

    with pytest.raises(PermissionError):
        await service.execute(proposal)

    assert service.validate(proposal)
    service.approve(proposal, approved_by='artemij')
    result = await service.execute(proposal)

    assert result == {'created': True}
    assert proposal.status == ProposalStatus.EXECUTED
    assert proposal.approved_by == 'artemij'
    application.process_command.assert_awaited_once_with(proposal.command)


def test_invalid_proposal_never_reaches_approval():
    service = ProposedCommandService(Mock())
    proposal = ProposedCommand(command(summary=''), 'пустая команда', 'Invalid', 0.4)

    assert not service.validate(proposal)
    assert proposal.status == ProposalStatus.FAILED
    with pytest.raises(ValueError):
        service.approve(proposal)
