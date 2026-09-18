"""Application-layer smoke tests with injected deterministic dependencies."""

from datetime import datetime, timedelta
from unittest.mock import Mock
import pytest

from services.application.command import CommandStatus, CreateUniversityEventCommand
from services.application.service import ApplicationService


async def test_application_service_routes_validated_command_to_domain_service():
    university = Mock()
    university.create_lecture.return_value = {'uid': 'generated-uid'}
    identifier = Mock()
    identifier.generate_uid.return_value = 'generated-uid'
    application = ApplicationService(
        university_service=university,
        project_service=Mock(),
        knowledge_service=Mock(),
        preparation_service=Mock(),
        calendar_service=Mock(),
        id_generator=identifier,
    )
    start = datetime(2026, 9, 1, 10, 0)
    command = CreateUniversityEventCommand(
        event_type='lecture',
        summary='Математика',
        description='Группа 8И41',
        location='101',
        dtstart=start,
        dtend=start + timedelta(hours=1),
        is_group_event=True,
    )

    result = await application.process_command(command)

    assert result == {'uid': 'generated-uid'}
    assert command.status == CommandStatus.COMPLETED
    university.create_lecture.assert_called_once_with(
        uid='generated-uid',
        summary='Математика',
        description='Группа 8И41',
        location='101',
        dtstart=start,
        dtend=start + timedelta(hours=1),
        is_group_event=True,
    )


async def test_application_service_does_not_log_or_persist_raw_execution_error():
    secret = 'https://private.test/?token=TOP_SECRET'
    university = Mock()
    university.create_lecture.side_effect = RuntimeError(secret)
    application = ApplicationService(
        university_service=university, project_service=Mock(), knowledge_service=Mock(),
        preparation_service=Mock(), calendar_service=Mock(), id_generator=Mock(),
    )
    application.id_generator.generate_uid.return_value = 'generated-uid'
    command = CreateUniversityEventCommand(
        event_type='lecture', summary='Математика', description='', location='',
        dtstart=datetime(2026, 9, 1, 10), dtend=datetime(2026, 9, 1, 11),
    )

    with pytest.raises(RuntimeError, match='TOP_SECRET'), \
            pytest.MonkeyPatch.context() as patcher:
        logger = Mock()
        patcher.setattr('services.application.service.logger', logger)
        await application.process_command(command)

    assert command.status == CommandStatus.FAILED
    assert command.error == 'Command execution failed'
    logger.error.assert_called_once_with('Command execution failed')
