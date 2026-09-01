"""
Application Service Layer
Handles command processing, validation, and routing to domain services.
Implements the flow: AI → ProposedCommand → Application Validation → Domain/Scheduler → Persistence
"""
from typing import Any, Dict, List, Optional
from datetime import datetime
import logging

from ..university_service import UniversityService
from ..project_service import ProjectService
from ..knowledge_service import KnowledgeService
from ..preparation.preparation_block_service import PreparationBlockService
from ..calendar.calendar_sync_service import CalendarSyncService
from planning_engine import PlanningEngine
from ids import IDGenerator
from persistence.repository import (
    university_event_repo, preparation_block_repo,
    project_repo, task_repo,
    knowledge_item_repo, tag_repo, knowledge_base_repo
)
from .command import *  # noqa: F401,F403 - command routing uses all command types.

# Set up logging
logger = logging.getLogger(__name__)


class ApplicationService:
    """
    Application service that processes commands from AI services.
    Implements validation and routing to domain services.
    """

    def __init__(
        self,
        university_service: Optional[UniversityService] = None,
        project_service: Optional[ProjectService] = None,
        knowledge_service: Optional[KnowledgeService] = None,
        preparation_service: Optional[PreparationBlockService] = None,
        calendar_service: Optional[CalendarSyncService] = None,
        planning_engine: Optional[PlanningEngine] = None,
        id_generator: Optional[IDGenerator] = None,
    ):
        """Initialize the application service with domain services."""
        # Adapters are injectable for deterministic application tests. The
        # default production wiring is intentionally deferred to factories,
        # because the concrete calendar services require an adapter instance.
        self.university_service = university_service or UniversityService()
        self.project_service = project_service or ProjectService()
        self.knowledge_service = knowledge_service or KnowledgeService()
        if preparation_service is None:
            from services.preparation.preparation_block_service import (
                create_preparation_block_service,
            )
            preparation_service = create_preparation_block_service()
        if calendar_service is None:
            from services.calendar.calendar_sync_service import (
                create_calendar_sync_service,
            )
            calendar_service = create_calendar_sync_service()
        self.preparation_service = preparation_service
        self.calendar_service = calendar_service
        self.planning_engine = planning_engine or PlanningEngine()
        self.id_generator = id_generator or IDGenerator()

        logger.info("ApplicationService initialized")

    async def process_command(self, command: BaseCommand) -> Any:
        """
        Process a command through the application flow.

        Flow: Command → Validation → Domain Service Execution → Result

        Args:
            command: The command to process

        Returns:
            The result of command execution

        Raises:
            ValueError: If command validation fails
            Exception: If command execution fails
        """
        logger.info(f"Processing command: {command.get_command_type()} (ID: {command.command_id})")

        # Step 1: Validate the command
        if not command.validate():
            error_msg = f"Command validation failed: {command.error}"
            logger.error(error_msg)
            command.mark_as_failed(error_msg)
            raise ValueError(error_msg)

        command.mark_as_validated()
        logger.debug(f"Command {command.command_id} validated successfully")

        # Step 2: Mark as in progress
        command.mark_as_in_progress()
        logger.debug(f"Command {command.command_id} marked as in progress")

        # Step 3: Route to appropriate domain service method
        try:
            result = await self._execute_command(command)
            command.mark_as_completed(result)
            logger.info(f"Command {command.command_id} completed successfully")
            return result
        except Exception as e:
            error_msg = f"Command execution failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            command.mark_as_failed(error_msg)
            raise

    async def _execute_command(self, command: BaseCommand) -> Any:
        """
        Execute a validated command by routing to the appropriate domain service.

        Args:
            command: The validated command to execute

        Returns:
            The result of the domain service operation
        """
        command_type = command.get_command_type()

        # Route to university service methods
        if isinstance(command, CreateUniversityEventCommand):
            return await self._handle_create_university_event(command)
        elif isinstance(command, GetUniversityEventCommand):
            return await self._handle_get_university_event(command)
        elif isinstance(command, ListUniversityEventsCommand):
            return await self._handle_list_university_events(command)
        elif isinstance(command, UpdateUniversityEventCommand):
            return await self._handle_update_university_event(command)
        elif isinstance(command, DeleteUniversityEventCommand):
            return await self._handle_delete_university_event(command)

        # Route to preparation block service methods
        elif isinstance(command, CreatePreparationBlockCommand):
            return await self._handle_create_preparation_block(command)
        elif isinstance(command, GetPreparationBlockCommand):
            return await self._handle_get_preparation_block(command)
        elif isinstance(command, ListPreparationBlocksCommand):
            return await self._handle_list_preparation_blocks(command)
        elif isinstance(command, UpdatePreparationBlockCommand):
            return await self._handle_update_preparation_block(command)
        elif isinstance(command, DeletePreparationBlockCommand):
            return await self._handle_delete_preparation_block(command)

        # Route to project service methods
        elif isinstance(command, CreateProjectCommand):
            return await self._handle_create_project(command)
        elif isinstance(command, GetProjectCommand):
            return await self._handle_get_project(command)
        elif isinstance(command, ListProjectsCommand):
            return await self._handle_list_projects(command)
        elif isinstance(command, UpdateProjectCommand):
            return await self._handle_update_project(command)
        elif isinstance(command, DeleteProjectCommand):
            return await self._handle_delete_project(command)

        # Route to task service methods
        elif isinstance(command, CreateTaskCommand):
            return await self._handle_create_task(command)
        elif isinstance(command, GetTaskCommand):
            return await self._handle_get_task(command)
        elif isinstance(command, ListTasksCommand):
            return await self._handle_list_tasks(command)
        elif isinstance(command, UpdateTaskCommand):
            return await self._handle_update_task(command)
        elif isinstance(command, DeleteTaskCommand):
            return await self._handle_delete_task(command)

        # Route to knowledge service methods
        elif isinstance(command, CreateKnowledgeItemCommand):
            return await self._handle_create_knowledge_item(command)
        elif isinstance(command, GetKnowledgeItemCommand):
            return await self._handle_get_knowledge_item(command)
        elif isinstance(command, ListKnowledgeItemsCommand):
            return await self._handle_list_knowledge_items(command)
        elif isinstance(command, UpdateKnowledgeItemCommand):
            return await self._handle_update_knowledge_item(command)
        elif isinstance(command, DeleteKnowledgeItemCommand):
            return await self._handle_delete_knowledge_item(command)

        # Route to tag service methods
        elif isinstance(command, CreateTagCommand):
            return await self._handle_create_tag(command)
        elif isinstance(command, GetTagCommand):
            return await self._handle_get_tag(command)
        elif isinstance(command, ListTagsCommand):
            return await self._handle_list_tags(command)
        elif isinstance(command, UpdateTagCommand):
            return await self._handle_update_tag(command)
        elif isinstance(command, DeleteTagCommand):
            return await self._handle_delete_tag(command)

        # Route to knowledge base service methods
        elif isinstance(command, CreateKnowledgeBaseCommand):
            return await self._handle_create_knowledge_base(command)
        elif isinstance(command, GetKnowledgeBaseCommand):
            return await self._handle_get_knowledge_base(command)
        elif isinstance(command, ListKnowledgeBasesCommand):
            return await self._handle_list_knowledge_bases(command)
        elif isinstance(command, UpdateKnowledgeBaseCommand):
            return await self._handle_update_knowledge_base(command)
        elif isinstance(command, DeleteKnowledgeBaseCommand):
            return await self._handle_delete_knowledge_base(command)

        # Route to association methods
        elif isinstance(command, AddKnowledgeItemTagCommand):
            return await self._handle_add_knowledge_item_tag(command)
        elif isinstance(command, RemoveKnowledgeItemTagCommand):
            return await self._handle_remove_knowledge_item_tag(command)
        elif isinstance(command, AddKnowledgeItemLinkCommand):
            return await self._handle_add_knowledge_item_link(command)
        elif isinstance(command, RemoveKnowledgeItemLinkCommand):
            return await self._handle_remove_knowledge_item_link(command)
        elif isinstance(command, AddKnowledgeItemToBaseCommand):
            return await self._handle_add_knowledge_item_to_base(command)
        elif isinstance(command, RemoveKnowledgeItemFromBaseCommand):
            return await self._handle_remove_knowledge_item_from_base(command)
        elif isinstance(command, AddTagToKnowledgeBaseCommand):
            return await self._handle_add_tag_to_knowledge_base(command)
        elif isinstance(command, RemoveTagFromKnowledgeBaseCommand):
            return await self._handle_remove_tag_from_knowledge_base(command)

        # Route to scheduling methods
        elif isinstance(command, SchedulePreparationBlocksCommand):
            return await self._handle_schedule_preparation_blocks(command)
        elif isinstance(command, ScheduleTasksCommand):
            return await self._handle_schedule_tasks(command)
        elif isinstance(command, ScheduleLearningTasksCommand):
            return await self._handle_schedule_learning_tasks(command)

        # Route to domain-specific operation methods
        elif isinstance(command, CreatePreparationFromEventCommand):
            return await self._handle_create_preparation_from_event(command)
        elif isinstance(command, EventsNeedingPreparationCommand):
            return await self._handle_events_needing_preparation(command)

        else:
            error_msg = f"Unknown command type: {command_type}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    # ===== University Command Handlers =====

    async def _handle_create_university_event(self, command: CreateUniversityEventCommand) -> Any:
        """Handle create university event command."""
        from models import EventType

        # Convert string event type to enum
        event_type_map = {
            'lecture': EventType.LECTURE,
            'lab': EventType.LAB,
            'practical': EventType.PRACTICAL,
            'seminar': EventType.SEMINAR,
            'workshop': EventType.WORKSHOP,
            'exam': EventType.EXAM
        }
        event_type = event_type_map.get(command.event_type.lower(), EventType.LECTURE)

        return self.university_service.create_lecture(
            uid=self.id_generator.generate_uid(),
            summary=command.summary,
            description=command.description,
            location=command.location,
            dtstart=command.dtstart,
            dtend=command.dtend,
            is_group_event=command.is_group_event
        ) if event_type == EventType.LECTURE else (
            self.university_service.create_lab(
                uid=self.id_generator.generate_uid(),
                summary=command.summary,
                description=command.description,
                location=command.location,
                dtstart=command.dtstart,
                dtend=command.dtend,
                is_group_event=command.is_group_event
            ) if event_type == EventType.LAB else (
                self.university_service.create_practical(
                    uid=self.id_generator.generate_uid(),
                    summary=command.summary,
                    description=command.description,
                    location=command.location,
                    dtstart=command.dtstart,
                    dtend=command.dtend,
                    is_group_event=command.is_group_event
                )
            )
        )

    async def _handle_get_university_event(self, command: GetUniversityEventCommand) -> Any:
        """Handle get university event command."""
        return self.university_service.get_event(command.event_id)

    async def _handle_list_university_events(self, command: ListUniversityEventsCommand) -> Any:
        """Handle list university events command."""
        return self.university_service.list_events(
            limit=command.limit,
            offset=command.offset
        )

    async def _handle_update_university_event(self, command: UpdateUniversityEventCommand) -> Any:
        """Handle update university event command."""
        # Get existing event
        event = self.university_service.get_event(command.event_id)
        if not event:
            raise ValueError(f"University event with ID {command.event_id} not found")

        # Update fields if provided
        if command.summary is not None:
            event.summary = command.summary
        if command.description is not None:
            event.description = command.description
        if command.location is not None:
            event.location = command.location
        if command.dtstart is not None:
            event.dtstart = command.dtstart
        if command.dtend is not None:
            event.dtend = command.dtend
        if command.is_group_event is not None:
            event.is_group_event = command.is_group_event

        event.updated_at = datetime.now()
        return self.university_service.update_event(event)

    async def _handle_delete_university_event(self, command: DeleteUniversityEventCommand) -> Any:
        """Handle delete university event command."""
        return self.university_service.delete_event(command.event_id)

    # ===== Preparation Block Command Handlers =====

    async def _handle_create_preparation_block(self, command: CreatePreparationBlockCommand) -> Any:
        """Handle create preparation block command."""
        from models import EventType

        # Convert string event type to enum if provided
        event_type = None
        if command.event_type:
            event_type_map = {
                'lecture': EventType.LECTURE,
                'lab': EventType.LAB,
                'practical': EventType.PRACTICAL,
                'seminar': EventType.SEMINAR,
                'workshop': EventType.WORKSHOP,
                'exam': EventType.EXAM
            }
            event_type = event_type_map.get(command.event_type.lower())

        return self.preparation_service.create_preparation_block(
            uid=self.id_generator.generate_uid(),
            summary=command.summary,
            description=command.description,
            dtstart=command.dtstart,
            dtend=command.dtend,
            preparation_minutes=command.preparation_minutes,
            source_event_uid=command.source_event_uid,
            event_type=event_type,
            calendar_id=command.calendar_id
        )

    async def _handle_get_preparation_block(self, command: GetPreparationBlockCommand) -> Any:
        """Handle get preparation block command."""
        return self.preparation_service.get_preparation_block(command.block_id)

    async def _handle_list_preparation_blocks(self, command: ListPreparationBlocksCommand) -> Any:
        """Handle list preparation blocks command."""
        return self.preparation_service.list_preparation_blocks(
            limit=command.limit,
            offset=command.offset
        )

    async def _handle_update_preparation_block(self, command: UpdatePreparationBlockCommand) -> Any:
        """Handle update preparation block command."""
        # Get existing preparation block
        block = self.preparation_service.get_preparation_block(command.block_id)
        if not block:
            raise ValueError(f"Preparation block with ID {command.block_id} not found")

        # Update fields if provided
        if command.summary is not None:
            block.summary = command.summary
        if command.description is not None:
            block.description = command.description
        if command.dtstart is not None:
            block.dtstart = command.dtstart
        if command.dtend is not None:
            block.dtend = command.dtend
        if command.preparation_minutes is not None:
            block.preparation_minutes = command.preparation_minutes
        if command.source_event_uid is not None:
            block.source_event_uid = command.source_event_uid
        if command.event_type is not None:
            from models import EventType
            event_type_map = {
                'lecture': EventType.LECTURE,
                'lab': EventType.LAB,
                'practical': EventType.PRACTICAL,
                'seminar': EventType.SEMINAR,
                'workshop': EventType.WORKSHOP,
                'exam': EventType.EXAM
            }
            block.event_type = event_type_map.get(command.event_type.lower())
        if command.calendar_id is not None:
            block.calendar_id = command.calendar_id

        block.updated_at = datetime.now()
        return self.preparation_service.update_preparation_block(block)

    async def _handle_delete_preparation_block(self, command: DeletePreparationBlockCommand) -> Any:
        """Handle delete preparation block command."""
        return self.preparation_service.delete_preparation_block(command.block_id)

    # ===== Project Command Handlers =====

    async def _handle_create_project(self, command: CreateProjectCommand) -> Any:
        """Handle create project command."""
        from models import ProjectStatus

        # Convert string status to enum
        status_map = {
            'planning': ProjectStatus.PLANNING,
            'active': ProjectStatus.ACTIVE,
            'on_hold': ProjectStatus.ON_HOLD,
            'completed': ProjectStatus.COMPLETED,
            'cancelled': ProjectStatus.CANCELLED,
            'archived': ProjectStatus.ARCHIVED
        }
        status = status_map.get(command.status.lower(), ProjectStatus.PLANNING)

        return self.project_service.create_project(
            name=command.name,
            description=command.description,
            status=status,
            start_date=command.start_date,
            target_date=command.target_date,
            tags=set(command.tags) if command.tags else None,
            metadata=command.metadata
        )

    async def _handle_get_project(self, command: GetProjectCommand) -> Any:
        """Handle get project command."""
        return self.project_service.get_project(command.project_id)

    async def _handle_list_projects(self, command: ListProjectsCommand) -> Any:
        """Handle list projects command."""
        return self.project_service.list_projects(
            limit=command.limit,
            offset=command.offset
        )

    async def _handle_update_project(self, command: UpdateProjectCommand) -> Any:
        """Handle update project command."""
        # Get existing project
        project = self.project_service.get_project(command.project_id)
        if not project:
            raise ValueError(f"Project with ID {command.project_id} not found")

        # Update fields if provided
        if command.name is not None:
            project.name = command.name
        if command.description is not None:
            project.description = command.description
        if command.status is not None:
            from models import ProjectStatus
            status_map = {
                'planning': ProjectStatus.PLANNING,
                'active': ProjectStatus.ACTIVE,
                'on_hold': ProjectStatus.ON_HOLD,
                'completed': ProjectStatus.COMPLETED,
                'cancelled': ProjectStatus.CANCELLED,
                'archived': ProjectStatus.ARCHIVED
            }
            project.status = status_map.get(command.status.lower(), project.status)
        if command.start_date is not None:
            project.start_date = command.start_date
        if command.target_date is not None:
            project.target_date = command.target_date
        if command.tags is not None:
            # Convert list to set for the project service
            project.tags = set(command.tags)
        if command.metadata is not None:
            project.metadata = command.metadata

        project.updated_at = datetime.now()
        return self.project_service.update_project(project)

    async def _handle_delete_project(self, command: DeleteProjectCommand) -> Any:
        """Handle delete project command."""
        return self.project_service.delete_project(command.project_id)

    # ===== Task Command Handlers =====

    async def _handle_create_task(self, command: CreateTaskCommand) -> Any:
        """Handle create task command."""
        from models import TaskPriority, TaskStatus

        # Convert string priority to enum
        priority_map = {
            'low': TaskPriority.LOW,
            'medium': TaskPriority.MEDIUM,
            'high': TaskPriority.HIGH,
            'urgent': TaskPriority.URGENT
        }
        priority = priority_map.get(command.priority.lower(), TaskPriority.MEDIUM)

        # Convert string status to enum
        status_map = {
            'todo': TaskStatus.INBOX,
            'in_progress': TaskStatus.IN_PROGRESS,
            'done': TaskStatus.COMPLETED,
            'archived': TaskStatus.ARCHIVED
        }
        status = status_map.get(command.status.lower(), TaskStatus.INBOX)

        return self.project_service.create_task(
            title=command.title,
            description=command.description,
            project_id=command.project_id,
            priority=priority,
            status=status,
            due_date=command.due_date,
            estimated_hours=command.estimated_hours,
            assignee=command.assignee,
            tags=set(command.tags) if command.tags else None,
            metadata=command.metadata,
            dependencies=command.dependencies
        )

    async def _handle_get_task(self, command: GetTaskCommand) -> Any:
        """Handle get task command."""
        return self.project_service.get_task(command.task_id)

    async def _handle_list_tasks(self, command: ListTasksCommand) -> Any:
        """Handle list tasks command."""
        return self.project_service.list_tasks(
            limit=command.limit,
            offset=command.offset
        )

    async def _handle_update_task(self, command: UpdateTaskCommand) -> Any:
        """Handle update task command."""
        # Get existing task
        task = self.project_service.get_task(command.task_id)
        if not task:
            raise ValueError(f"Task with ID {command.task_id} not found")

        # Update fields if provided
        if command.title is not None:
            task.title = command.title
        if command.description is not None:
            task.description = command.description
        if command.project_id is not None:
            task.project_id = command.project_id
        if command.priority is not None:
            from models import TaskPriority
            priority_map = {
                'low': TaskPriority.LOW,
                'medium': TaskPriority.MEDIUM,
                'high': TaskPriority.HIGH,
                'urgent': TaskPriority.URGENT
            }
            task.priority = priority_map.get(command.priority.lower(), task.priority)
        if command.status is not None:
            from models import TaskStatus
            status_map = {
                'todo': TaskStatus.INBOX,
                'in_progress': TaskStatus.IN_PROGRESS,
                'done': TaskStatus.COMPLETED,
                'archived': TaskStatus.ARCHIVED
            }
            task.update_status(status_map.get(command.status.lower(), task.status))
        if command.due_date is not None:
            task.due_date = command.due_date
        if command.estimated_hours is not None:
            task.estimated_hours = command.estimated_hours
        if command.assignee is not None:
            task.assignee = command.assignee
        if command.tags is not None:
            task.tags = set(command.tags)
        if command.metadata is not None:
            task.metadata = command.metadata
        if command.dependencies is not None:
            task.dependencies = command.dependencies

        task.updated_at = datetime.now()
        return self.project_service.update_task(task)

    async def _handle_delete_task(self, command: DeleteTaskCommand) -> Any:
        """Handle delete task command."""
        return self.project_service.delete_task(command.task_id)

    # ===== Knowledge Item Command Handlers =====

    async def _handle_create_knowledge_item(self, command: CreateKnowledgeItemCommand) -> Any:
        """Handle create knowledge item command."""
        from models import NoteType, KnowledgeStatus

        # Convert string note_type to enum
        note_type_map = {
            'idea': NoteType.IDEA,
            'reference': NoteType.REFERENCE,
            'learning': NoteType.LEARNING,
            'meeting': NoteType.MEETING,
            'journal': NoteType.JOURNAL
        }
        note_type = note_type_map.get(command.note_type.lower(), NoteType.IDEA)

        # Convert string status to enum
        status_map = {
            'draft': KnowledgeStatus.DRAFT,
            'review': KnowledgeStatus.REVIEW,
            'published': KnowledgeStatus.PUBLISHED,
            'archived': KnowledgeStatus.ARCHIVED
        }
        status = status_map.get(command.status.lower(), KnowledgeStatus.DRAFT)

        return self.knowledge_service.create_knowledge_item(
            title=command.title,
            content=command.content,
            note_type=note_type,
            status=status,
            author=command.author,
            tags=set(command.tags) if command.tags else None,
            links=command.links,
            project_id=command.project_id,
            metadata=command.metadata
        )

    async def _handle_get_knowledge_item(self, command: GetKnowledgeItemCommand) -> Any:
        """Handle get knowledge item command."""
        return self.knowledge_service.get_knowledge_item(command.item_id)

    async def _handle_list_knowledge_items(self, command: ListKnowledgeItemsCommand) -> Any:
        """Handle list knowledge items command."""
        return self.knowledge_service.list_knowledge_items(
            limit=command.limit,
            offset=command.offset
        )

    async def _handle_update_knowledge_item(self, command: UpdateKnowledgeItemCommand) -> Any:
        """Handle update knowledge item command."""
        # Get existing knowledge item
        item = self.knowledge_service.get_knowledge_item(command.item_id)
        if not item:
            raise ValueError(f"Knowledge item with ID {command.item_id} not found")

        # Update fields if provided
        if command.title is not None:
            item.title = command.title
        if command.content is not None:
            item.content = command.content
        if command.note_type is not None:
            from models import NoteType
            note_type_map = {
                'idea': NoteType.IDEA,
                'reference': NoteType.REFERENCE,
                'learning': NoteType.LEARNING,
                'meeting': NoteType.MEETING,
                'journal': NoteType.JOURNAL
            }
            item.note_type = note_type_map.get(command.note_type.lower(), item.note_type)
        if command.status is not None:
            from models import KnowledgeStatus
            status_map = {
                'draft': KnowledgeStatus.DRAFT,
                'review': KnowledgeStatus.REVIEW,
                'published': KnowledgeStatus.PUBLISHED,
                'archived': KnowledgeStatus.ARCHIVED
            }
            item.update_status(status_map.get(command.status.lower(), item.status))
        if command.author is not None:
            item.author = command.author
        if command.project_id is not None:
            item.project_id = command.project_id
        if command.tags is not None:
            # For tags, we'll need to handle them specially since they're persisted
            # For now, we'll update the item's tags and let the service handle persistence
            item.tags = set(command.tags)
        if command.links is not None:
            item.links = command.links
        if command.metadata is not None:
            item.metadata = command.metadata

        item.updated_at = datetime.now()
        return self.knowledge_service.update_knowledge_item(
            item,
            title=command.title,
            content=command.content,
            note_type=command.note_type,
            status=command.status,
            author=command.author,
            project_id=command.project_id
        )

    async def _handle_delete_knowledge_item(self, command: DeleteKnowledgeItemCommand) -> Any:
        """Handle delete knowledge item command."""
        return self.knowledge_service.delete_knowledge_item(command.item_id)

    # ===== Tag Command Handlers =====

    async def _handle_create_tag(self, command: CreateTagCommand) -> Any:
        """Handle create tag command."""
        return self.knowledge_service.create_tag(
            name=command.name,
            color=command.color,
            description=command.description
        )

    async def _handle_get_tag(self, command: GetTagCommand) -> Any:
        """Handle get tag command."""
        return self.knowledge_service.get_tag(command.tag_id)

    async def _handle_list_tags(self, command: ListTagsCommand) -> Any:
        """Handle list tags command."""
        return self.knowledge_service.list_tags(
            limit=command.limit,
            offset=command.offset
        )

    async def _handle_update_tag(self, command: UpdateTagCommand) -> Any:
        """Handle update tag command."""
        # Get existing tag
        tag = self.knowledge_service.get_tag(command.tag_id)
        if not tag:
            raise ValueError(f"Tag with ID {command.tag_id} not found")

        # Update fields if provided
        if command.name is not None:
            tag.name = command.name.lower().strip()
        if command.color is not None:
            tag.color = command.color
        if command.description is not None:
            tag.description = command.description

        tag.updated_at = datetime.now()
        return self.knowledge_service.update_tag(tag)

    async def _handle_delete_tag(self, command: DeleteTagCommand) -> Any:
        """Handle delete tag command."""
        return self.knowledge_service.delete_tag(command.tag_id)

    # ===== Knowledge Base Command Handlers =====

    async def _handle_create_knowledge_base(self, command: CreateKnowledgeBaseCommand) -> Any:
        """Handle create knowledge base command."""
        return self.knowledge_service.create_knowledge_base(
            name=command.name,
            description=command.description,
            tags=set(command.tags) if command.tags else None,
            metadata=command.metadata
        )

    async def _handle_get_knowledge_base(self, command: GetKnowledgeBaseCommand) -> Any:
        """Handle get knowledge base command."""
        return self.knowledge_service.get_knowledge_base(command.kb_id)

    async def _handle_list_knowledge_bases(self, command: ListKnowledgeBasesCommand) -> Any:
        """Handle list knowledge bases command."""
        return self.knowledge_service.list_knowledge_bases(
            limit=command.limit,
            offset=command.offset
        )

    async def _handle_update_knowledge_base(self, command: UpdateKnowledgeBaseCommand) -> Any:
        """Handle update knowledge base command."""
        # Get existing knowledge base
        kb = self.knowledge_service.get_knowledge_base(command.kb_id)
        if not kb:
            raise ValueError(f"Knowledge base with ID {command.kb_id} not found")

        # Update fields if provided
        if command.name is not None:
            kb.name = command.name
        if command.description is not None:
            kb.description = command.description
        if command.tags is not None:
            kb.tags = set(command.tags)
        if command.metadata is not None:
            kb.metadata = command.metadata

        kb.updated_at = datetime.now()
        return self.knowledge_service.update_knowledge_base(kb)

    async def _handle_delete_knowledge_base(self, command: DeleteKnowledgeBaseCommand) -> Any:
        """Handle delete knowledge base command."""
        return self.knowledge_service.delete_knowledge_base(command.kb_id)

    # ===== Association Command Handlers =====

    async def _handle_add_knowledge_item_tag(self, command: AddKnowledgeItemTagCommand) -> Any:
        """Handle add knowledge item tag command."""
        # Get the knowledge item
        item = self.knowledge_service.get_knowledge_item(command.item_id)
        if not item:
            raise ValueError(f"Knowledge item with ID {command.item_id} not found")

        # Add the tag
        return self.knowledge_service.add_knowledge_item_tag(item, command.tag)

    async def _handle_remove_knowledge_item_tag(self, command: RemoveKnowledgeItemTagCommand) -> Any:
        """Handle remove knowledge item tag command."""
        # Get the knowledge item
        item = self.knowledge_service.get_knowledge_item(command.item_id)
        if not item:
            raise ValueError(f"Knowledge item with ID {command.item_id} not found")

        # Remove the tag
        return self.knowledge_service.remove_knowledge_item_tag(item, command.tag)

    async def _handle_add_knowledge_item_link(self, command: AddKnowledgeItemLinkCommand) -> Any:
        """Handle add knowledge item link command."""
        # Get the knowledge item
        item = self.knowledge_service.get_knowledge_item(command.item_id)
        if not item:
            raise ValueError(f"Knowledge item with ID {command.item_id} not found")

        # Add the link
        return self.knowledge_service.add_knowledge_item_link(item, command.linked_item_id)

    async def _handle_remove_knowledge_item_link(self, command: RemoveKnowledgeItemLinkCommand) -> Any:
        """Handle remove knowledge item link command."""
        # Get the knowledge item
        item = self.knowledge_service.get_knowledge_item(command.item_id)
        if not item:
            raise ValueError(f"Knowledge item with ID {command.item_id} not found")

        # Remove the link
        return self.knowledge_service.remove_knowledge_item_link(item, command.linked_item_id)

    async def _handle_add_knowledge_item_to_base(self, command: AddKnowledgeItemToBaseCommand) -> Any:
        """Handle add knowledge item to knowledge base command."""
        # Get the knowledge base
        kb = self.knowledge_service.get_knowledge_base(command.kb_id)
        if not kb:
            raise ValueError(f"Knowledge base with ID {command.kb_id} not found")

        # Get the knowledge item
        item = self.knowledge_service.get_knowledge_item(command.item_id)
        if not item:
            raise ValueError(f"Knowledge item with ID {command.item_id} not found")

        # Add item to base
        return self.knowledge_service.add_knowledge_item_to_base(kb, command.item_id)

    async def _handle_remove_knowledge_item_from_base(self, command: RemoveKnowledgeItemFromBaseCommand) -> Any:
        """Handle remove knowledge item from knowledge base command."""
        # Get the knowledge base
        kb = self.knowledge_service.get_knowledge_base(command.kb_id)
        if not kb:
            raise ValueError(f"Knowledge base with ID {command.kb_id} not found")

        # Get the knowledge item
        item = self.knowledge_service.get_knowledge_item(command.item_id)
        if not item:
            raise ValueError(f"Knowledge item with ID {command.item_id} not found")

        # Remove item from base
        return self.knowledge_service.remove_knowledge_item_from_base(kb, command.item_id)

    async def _handle_add_tag_to_knowledge_base(self, command: AddTagToKnowledgeBaseCommand) -> Any:
        """Handle add tag to knowledge base command."""
        # Get the knowledge base
        kb = self.knowledge_service.get_knowledge_base(command.kb_id)
        if not kb:
            raise ValueError(f"Knowledge base with ID {command.kb_id} not found")

        # Add tag to base
        return self.knowledge_service.add_tag_to_base(kb, command.tag)

    async def _handle_remove_tag_from_knowledge_base(self, command: RemoveTagFromKnowledgeBaseCommand) -> Any:
        """Handle remove tag from knowledge base command."""
        # Get the knowledge base
        kb = self.knowledge_service.get_knowledge_base(command.kb_id)
        if not kb:
            raise ValueError(f"Knowledge base with ID {command.kb_id} not found")

        # Remove tag from base
        return self.knowledge_service.remove_tag_from_base(kb, command.tag)

    # ===== Scheduling Command Handlers =====

    async def _handle_schedule_preparation_blocks(self, command: SchedulePreparationBlocksCommand) -> Any:
        """Handle schedule preparation blocks command."""
        # Get the preparation blocks
        blocks = []
        for block_id in command.preparation_block_ids:
            block = self.preparation_service.get_preparation_block(block_id)
            if not block:
                raise ValueError(f"Preparation block with ID {block_id} not found")
            blocks.append(block)

        return self.preparation_service.schedule_preparation_blocks(
            preparation_blocks=blocks,
            planning_horizon_start=command.planning_horizon_start,
            planning_horizon_end=command.planning_horizon_end,
            granularity_minutes=command.granularity_minutes
        )

    async def _handle_schedule_tasks(self, command: ScheduleTasksCommand) -> Any:
        """Handle schedule tasks command."""
        # Get the tasks
        tasks = []
        for task_id in command.task_ids:
            task = self.project_service.get_task(task_id)
            if not task:
                raise ValueError(f"Task with ID {task_id} not found")
            tasks.append(task)

        return self.project_service.schedule_tasks(
            tasks=tasks,
            planning_horizon_start=command.planning_horizon_start,
            planning_horizon_end=command.planning_horizon_end,
            granularity_minutes=command.granularity_minutes
        )

    async def _handle_schedule_learning_tasks(self, command: ScheduleLearningTasksCommand) -> Any:
        """Handle schedule learning tasks command."""
        # Get the knowledge items
        items = []
        for item_id in command.knowledge_item_ids:
            item = self.knowledge_service.get_knowledge_item(item_id)
            if not item:
                raise ValueError(f"Knowledge item with ID {item_id} not found")
            items.append(item)

        return self.knowledge_service.schedule_learning_tasks(
            knowledge_items=items,
            planning_horizon_start=command.planning_horizon_start,
            planning_horizon_end=command.planning_horizon_end,
            granularity_minutes=command.granularity_minutes,
            review_interval_days=command.review_interval_days
        )

    # ===== Domain-Specific Operation Command Handlers =====

    async def _handle_create_preparation_from_event(self, command: CreatePreparationFromEventCommand) -> Any:
        """Handle create preparation from event command."""
        # Get the university event
        event = self.university_service.get_event(command.event_id)
        if not event:
            raise ValueError(f"University event with ID {command.event_id} not found")

        return self.preparation_service.create_preparation_from_event(
            event=event,
            preparation_minutes=command.preparation_minutes,
            lead_time_hours=command.lead_time_hours
        )

    async def _handle_events_needing_preparation(self, command: EventsNeedingPreparationCommand) -> Any:
        """Handle events needing preparation command."""
        # Get the university events
        events = []
        for event_id in command.event_ids:
            event = self.university_service.get_event(event_id)
            if not event:
                raise ValueError(f"University event with ID {event_id} not found")
            events.append(event)

        return self.preparation_service.events_needing_preparation(
            events=events,
            min_preparation_minutes=command.min_preparation_minutes
        )


# Global application service instance
application_service = ApplicationService()


# Convenience function for processing commands
async def process_command(command: BaseCommand) -> Any:
    """
    Process a command using the global application service.

    Args:
        command: The command to process

    Returns:
        The result of command execution
    """
    return await application_service.process_command(command)
