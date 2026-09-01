"""Application-layer smoke tests with injected deterministic dependencies."""

from datetime import datetime, timedelta
from unittest.mock import Mock

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
