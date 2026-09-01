"""
Application Command Pattern
Defines the base command interface and common command types for the application layer.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime
from enum import Enum


class CommandStatus(Enum):
    """Status of command processing."""
    PENDING = "pending"
    VALIDATED = "validated"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BaseCommand(ABC):
    """Base class for all application commands."""

    def __init__(self, command_id: Optional[str] = None):
        """
        Initialize a command.

        Args:
            command_id: Optional unique identifier for the command
        """
        self.command_id = command_id or self._generate_id()
        self.timestamp = datetime.now()
        self.status = CommandStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None

    def _generate_id(self) -> str:
        """Generate a unique command ID."""
        import uuid
        return str(uuid.uuid4())

    @abstractmethod
    def get_command_type(self) -> str:
        """Get the type of this command."""
        pass

    @abstractmethod
    def validate(self) -> bool:
        """
        Validate the command.

        Returns:
            bool: True if command is valid, False otherwise
        """
        pass

    def mark_as_validated(self):
        """Mark command as validated."""
        self.status = CommandStatus.VALIDATED

    def mark_as_in_progress(self):
        """Mark command as in progress."""
        self.status = CommandStatus.IN_PROGRESS

    def mark_as_completed(self, result: Any = None):
        """Mark command as completed."""
        self.status = CommandStatus.COMPLETED
        self.result = result

    def mark_as_failed(self, error: str):
        """Mark command as failed."""
        self.status = CommandStatus.FAILED
        self.error = error

    def mark_as_cancelled(self):
        """Mark command as cancelled."""
        self.status = CommandStatus.CANCELLED

    def is_successful(self) -> bool:
        """Check if command completed successfully."""
        return self.status == CommandStatus.COMPLETED

    def is_failed(self) -> bool:
        """Check if command failed."""
        return self.status == CommandStatus.FAILED


# ===== University Commands =====

class CreateUniversityEventCommand(BaseCommand):
    """Command to create a university event."""

    def __init__(self, event_type: str, summary: str, description: str,
                 location: str, dtstart: datetime, dtend: datetime,
                 is_group_event: bool = False, command_id: Optional[str] = None):
        """
        Initialize create university event command.

        Args:
            event_type: Type of event (lecture, lab, practical, etc.)
            summary: Event title/description
            description: Detailed description
            location: Location (room, building, etc.)
            dtstart: Start datetime
            dtend: End datetime
            is_group_event: Whether this is a group event
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.event_type = event_type
        self.summary = summary
        self.description = description
        self.location = location
        self.dtstart = dtstart
        self.dtend = dtend
        self.is_group_event = is_group_event

    def get_command_type(self) -> str:
        return "create_university_event"

    def validate(self) -> bool:
        """Validate the create university event command."""
        if not self.summary or not self.summary.strip():
            self.error = "Event summary is required"
            return False

        if self.dtstart >= self.dtend:
            self.error = "Event start time must be before end time"
            return False

        # Additional validation could go here
        return True


class GetUniversityEventCommand(BaseCommand):
    """Command to get a university event by ID."""

    def __init__(self, event_id: str, command_id: Optional[str] = None):
        """
        Initialize get university event command.

        Args:
            event_id: ID of the event to retrieve
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.event_id = event_id

    def get_command_type(self) -> str:
        return "get_university_event"

    def validate(self) -> bool:
        """Validate the get university event command."""
        if not self.event_id or not self.event_id.strip():
            self.error = "Event ID is required"
            return False
        return True


class ListUniversityEventsCommand(BaseCommand):
    """Command to list university events with optional pagination."""

    def __init__(self, limit: Optional[int] = None, offset: int = 0,
                 command_id: Optional[str] = None):
        """
        Initialize list university events command.

        Args:
            limit: Maximum number of events to return
            offset: Number of events to skip
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.limit = limit
        self.offset = offset

    def get_command_type(self) -> str:
        return "list_university_events"

    def validate(self) -> bool:
        """Validate the list university events command."""
        if self.limit is not None and self.limit < 0:
            self.error = "Limit must be non-negative"
            return False
        if self.offset < 0:
            self.error = "Offset must be non-negative"
            return False
        return True


class UpdateUniversityEventCommand(BaseCommand):
    """Command to update a university event."""

    def __init__(self, event_id: str, summary: Optional[str] = None,
                 description: Optional[str] = None, location: Optional[str] = None,
                 dtstart: Optional[datetime] = None, dtend: Optional[datetime] = None,
                 is_group_event: Optional[bool] = None,
                 command_id: Optional[str] = None):
        """
        Initialize update university event command.

        Args:
            event_id: ID of the event to update
            summary: New event summary (optional)
            description: New event description (optional)
            location: New location (optional)
            dtstart: New start datetime (optional)
            dtend: New end datetime (optional)
            is_group_event: New group event flag (optional)
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.event_id = event_id
        self.summary = summary
        self.description = description
        self.location = location
        self.dtstart = dtstart
        self.dtend = dtend
        self.is_group_event = is_group_event

    def get_command_type(self) -> str:
        return "update_university_event"

    def validate(self) -> bool:
        """Validate the update university event command."""
        if not self.event_id or not self.event_id.strip():
            self.error = "Event ID is required"
            return False

        # If dtstart or dtend are provided, validate them
        if self.dtstart is not None and self.dtend is not None:
            if self.dtstart >= self.dtend:
                self.error = "Event start time must be before end time"
                return False

        return True


class DeleteUniversityEventCommand(BaseCommand):
    """Command to delete a university event."""

    def __init__(self, event_id: str, command_id: Optional[str] = None):
        """
        Initialize delete university event command.

        Args:
            event_id: ID of the event to delete
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.event_id = event_id

    def get_command_type(self) -> str:
        return "delete_university_event"

    def validate(self) -> bool:
        """Validate the delete university event command."""
        if not self.event_id or not self.event_id.strip():
            self.error = "Event ID is required"
            return False
        return True


# ===== Preparation Block Commands =====

class CreatePreparationBlockCommand(BaseCommand):
    """Command to create a preparation block."""

    def __init__(self, summary: str, description: str, dtstart: datetime,
                 dtend: datetime, preparation_minutes: int,
                 source_event_uid: str, event_type: Optional[str] = None,
                 calendar_id: str = "University Schedule",
                 command_id: Optional[str] = None):
        """
        Initialize create preparation block command.

        Args:
            summary: Title/description
            description: Detailed description
            dtstart: Start datetime
            dtend: End datetime
            preparation_minutes: Duration of preparation in minutes
            source_event_uid: UID of the source university event
            event_type: Type of source event (optional)
            calendar_id: Calendar ID (defaults to "University Schedule")
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.summary = summary
        self.description = description
        self.dtstart = dtstart
        self.dtend = dtend
        self.preparation_minutes = preparation_minutes
        self.source_event_uid = source_event_uid
        self.event_type = event_type
        self.calendar_id = calendar_id

    def get_command_type(self) -> str:
        return "create_preparation_block"

    def validate(self) -> bool:
        """Validate the create preparation block command."""
        if not self.summary or not self.summary.strip():
            self.error = "Preparation block summary is required"
            return False

        if self.preparation_minutes <= 0:
            self.error = "Preparation minutes must be positive"
            return False

        if self.dtstart >= self.dtend:
            self.error = "Preparation block start time must be before end time"
            return False

        if not self.source_event_uid or not self.source_event_uid.strip():
            self.error = "Source event UID is required"
            return False

        return True


class GetPreparationBlockCommand(BaseCommand):
    """Command to get a preparation block by ID."""

    def __init__(self, block_id: str, command_id: Optional[str] = None):
        """
        Initialize get preparation block command.

        Args:
            block_id: ID of the preparation block to retrieve
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.block_id = block_id

    def get_command_type(self) -> str:
        return "get_preparation_block"

    def validate(self) -> bool:
        """Validate the get preparation block command."""
        if not self.block_id or not self.block_id.strip():
            self.error = "Preparation block ID is required"
            return False
        return True


class ListPreparationBlocksCommand(BaseCommand):
    """Command to list preparation blocks with optional pagination."""

    def __init__(self, limit: Optional[int] = None, offset: int = 0,
                 command_id: Optional[str] = None):
        """
        Initialize list preparation blocks command.

        Args:
            limit: Maximum number of blocks to return
            offset: Number of blocks to skip
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.limit = limit
        self.offset = offset

    def get_command_type(self) -> str:
        return "list_preparation_blocks"

    def validate(self) -> bool:
        """Validate the list preparation blocks command."""
        if self.limit is not None and self.limit < 0:
            self.error = "Limit must be non-negative"
            return False
        if self.offset < 0:
            self.error = "Offset must be non-negative"
            return False
        return True


class UpdatePreparationBlockCommand(BaseCommand):
    """Command to update a preparation block."""

    def __init__(self, block_id: str, summary: Optional[str] = None,
                 description: Optional[str] = None, dtstart: Optional[datetime] = None,
                 dtend: Optional[datetime] = None,
                 preparation_minutes: Optional[int] = None,
                 source_event_uid: Optional[str] = None,
                 event_type: Optional[str] = None,
                 calendar_id: Optional[str] = None,
                 command_id: Optional[str] = None):
        """
        Initialize update preparation block command.

        Args:
            block_id: ID of the preparation block to update
            summary: New summary (optional)
            description: New description (optional)
            dtstart: New start datetime (optional)
            dtend: New end datetime (optional)
            preparation_minutes: New preparation minutes (optional)
            source_event_uid: New source event UID (optional)
            event_type: New event type (optional)
            calendar_id: New calendar ID (optional)
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.block_id = block_id
        self.summary = summary
        self.description = description
        self.dtstart = dtstart
        self.dtend = dtend
        self.preparation_minutes = preparation_minutes
        self.source_event_uid = source_event_uid
        self.event_type = event_type
        self.calendar_id = calendar_id

    def get_command_type(self) -> str:
        return "update_preparation_block"

    def validate(self) -> bool:
        """Validate the update preparation block command."""
        if not self.block_id or not self.block_id.strip():
            self.error = "Preparation block ID is required"
            return False

        # If preparation_minutes is provided, validate it
        if self.preparation_minutes is not None and self.preparation_minutes <= 0:
            self.error = "Preparation minutes must be positive"
            return False

        # If dtstart or dtend are provided, validate them
        if self.dtstart is not None and self.dtend is not None:
            if self.dtstart >= self.dtend:
                self.error = "Preparation block start time must be before end time"
                return False

        return True


class DeletePreparationBlockCommand(BaseCommand):
    """Command to delete a preparation block."""

    def __init__(self, block_id: str, command_id: Optional[str] = None):
        """
        Initialize delete preparation block command.

        Args:
            block_id: ID of the preparation block to delete
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.block_id = block_id

    def get_command_type(self) -> str:
        return "delete_preparation_block"

    def validate(self) -> bool:
        """Validate the delete preparation block command."""
        if not self.block_id or not self.block_id.strip():
            self.error = "Preparation block ID is required"
            return False
        return True


# ===== Project Commands =====

class CreateProjectCommand(BaseCommand):
    """Command to create a project."""

    def __init__(self, name: str, description: str,
                 status: str = "planning",
                 start_date: Optional[datetime] = None,
                 target_date: Optional[datetime] = None,
                 tags: Optional[list] = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 command_id: Optional[str] = None):
        """
        Initialize create project command.

        Args:
            name: Project name
            description: Project description
            status: Project status (defaults to "planning")
            start_date: Project start date (optional)
            target_date: Project target completion date (optional)
            tags: List of tags for the project (optional)
            metadata: Additional metadata (optional)
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.name = name
        self.description = description
        self.status = status
        self.start_date = start_date
        self.target_date = target_date
        self.tags = tags or []
        self.metadata = metadata or {}

    def get_command_type(self) -> str:
        return "create_project"

    def validate(self) -> bool:
        """Validate the create project command."""
        if not self.name or not self.name.strip():
            self.error = "Project name is required"
            return False

        return True


class GetProjectCommand(BaseCommand):
    """Command to get a project by ID."""

    def __init__(self, project_id: str, command_id: Optional[str] = None):
        """
        Initialize get project command.

        Args:
            project_id: ID of the project to retrieve
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.project_id = project_id

    def get_command_type(self) -> str:
        return "get_project"

    def validate(self) -> bool:
        """Validate the get project command."""
        if not self.project_id or not self.project_id.strip():
            self.error = "Project ID is required"
            return False
        return True


class ListProjectsCommand(BaseCommand):
    """Command to list projects with optional pagination."""

    def __init__(self, limit: Optional[int] = None, offset: int = 0,
                 command_id: Optional[str] = None):
        """
        Initialize list projects command.

        Args:
            limit: Maximum number of projects to return
            offset: Number of projects to skip
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.limit = limit
        self.offset = offset

    def get_command_type(self) -> str:
        return "list_projects"

    def validate(self) -> bool:
        """Validate the list projects command."""
        if self.limit is not None and self.limit < 0:
            self.error = "Limit must be non-negative"
            return False
        if self.offset < 0:
            self.error = "Offset must be non-negative"
            return False
        return True


class UpdateProjectCommand(BaseCommand):
    """Command to update a project."""

    def __init__(self, project_id: str, name: Optional[str] = None,
                 description: Optional[str] = None,
                 status: Optional[str] = None,
                 start_date: Optional[datetime] = None,
                 target_date: Optional[datetime] = None,
                 tags: Optional[list] = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 command_id: Optional[str] = None):
        """
        Initialize update project command.

        Args:
            project_id: ID of the project to update
            name: New project name (optional)
            description: New project description (optional)
            status: New project status (optional)
            start_date: New project start date (optional)
            target_date: New project target completion date (optional)
            tags: New list of tags (optional)
            metadata: New additional metadata (optional)
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.project_id = project_id
        self.name = name
        self.description = description
        self.status = status
        self.start_date = start_date
        self.target_date = target_date
        self.tags = tags
        self.metadata = metadata

    def get_command_type(self) -> str:
        return "update_project"

    def validate(self) -> bool:
        """Validate the update project command."""
        if not self.project_id or not self.project_id.strip():
            self.error = "Project ID is required"
            return False

        return True


class DeleteProjectCommand(BaseCommand):
    """Command to delete a project."""

    def __init__(self, project_id: str, command_id: Optional[str] = None):
        """
        Initialize delete project command.

        Args:
            project_id: ID of the project to delete
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.project_id = project_id

    def get_command_type(self) -> str:
        return "delete_project"

    def validate(self) -> bool:
        """Validate the delete project command."""
        if not self.project_id or not self.project_id.strip():
            self.error = "Project ID is required"
            return False
        return True


# ===== Task Commands =====

class CreateTaskCommand(BaseCommand):
    """Command to create a task."""

    def __init__(self, title: str, description: str,
                 project_id: Optional[str] = None,
                 priority: str = "medium",
                 status: str = "todo",
                 due_date: Optional[datetime] = None,
                 estimated_hours: Optional[float] = None,
                 assignee: Optional[str] = None,
                 tags: Optional[list] = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 dependencies: Optional[list] = None,
                 command_id: Optional[str] = None):
        """
        Initialize create task command.

        Args:
            title: Task title
            description: Task description
            project_id: Optional project ID (None for standalone tasks)
            priority: Task priority (defaults to "medium")
            status: Task status (defaults to "todo")
            due_date: Optional due date
            estimated_hours: Estimated hours to complete
            assignee: Person assigned to the task
            tags: List of tags for the task
            metadata: Additional metadata
            dependencies: List of task IDs that must be completed first
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.title = title
        self.description = description
        self.project_id = project_id
        self.priority = priority
        self.status = status
        self.due_date = due_date
        self.estimated_hours = estimated_hours
        self.assignee = assignee
        self.tags = tags or []
        self.metadata = metadata or {}
        self.dependencies = dependencies or []

    def get_command_type(self) -> str:
        return "create_task"

    def validate(self) -> bool:
        """Validate the create task command."""
        if not self.title or not self.title.strip():
            self.error = "Task title is required"
            return False

        return True


class GetTaskCommand(BaseCommand):
    """Command to get a task by ID."""

    def __init__(self, task_id: str, command_id: Optional[str] = None):
        """
        Initialize get task command.

        Args:
            task_id: ID of the task to retrieve
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.task_id = task_id

    def get_command_type(self) -> str:
        return "get_task"

    def validate(self) -> bool:
        """Validate the get task command."""
        if not self.task_id or not self.task_id.strip():
            self.error = "Task ID is required"
            return False
        return True


class ListTasksCommand(BaseCommand):
    """Command to list tasks with optional pagination."""

    def __init__(self, limit: Optional[int] = None, offset: int = 0,
                 command_id: Optional[str] = None):
        """
        Initialize list tasks command.

        Args:
            limit: Maximum number of tasks to return
            offset: Number of tasks to skip
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.limit = limit
        self.offset = offset

    def get_command_type(self) -> str:
        return "list_tasks"

    def validate(self) -> bool:
        """Validate the list tasks command."""
        if self.limit is not None and self.limit < 0:
            self.error = "Limit must be non-negative"
            return False
        if self.offset < 0:
            self.error = "Offset must be non-negative"
            return False
        return True


class UpdateTaskCommand(BaseCommand):
    """Command to update a task."""

    def __init__(self, task_id: str, title: Optional[str] = None,
                 description: Optional[str] = None,
                 project_id: Optional[str] = None,
                 priority: Optional[str] = None,
                 status: Optional[str] = None,
                 due_date: Optional[datetime] = None,
                 estimated_hours: Optional[float] = None,
                 assignee: Optional[str] = None,
                 tags: Optional[list] = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 dependencies: Optional[list] = None,
                 command_id: Optional[str] = None):
        """
        Initialize update task command.

        Args:
            task_id: ID of the task to update
            title: New task title (optional)
            description: New task description (optional)
            project_id: New project ID (optional)
            priority: New task priority (optional)
            status: New task status (optional)
            due_date: New due date (optional)
            estimated_hours: New estimated hours (optional)
            assignee: New assignee (optional)
            tags: New list of tags (optional)
            metadata: New additional metadata (optional)
            dependencies: New list of dependencies (optional)
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.task_id = task_id
        self.title = title
        self.description = description
        self.project_id = project_id
        self.priority = priority
        self.status = status
        self.due_date = due_date
        self.estimated_hours = estimated_hours
        self.assignee = assignee
        self.tags = tags
        self.metadata = metadata
        self.dependencies = dependencies

    def get_command_type(self) -> str:
        return "update_task"

    def validate(self) -> bool:
        """Validate the update task command."""
        if not self.task_id or not self.task_id.strip():
            self.error = "Task ID is required"
            return False

        return True


class DeleteTaskCommand(BaseCommand):
    """Command to delete a task."""

    def __init__(self, task_id: str, command_id: Optional[str] = None):
        """
        Initialize delete task command.

        Args:
            task_id: ID of the task to delete
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.task_id = task_id

    def get_command_type(self) -> str:
        return "delete_task"

    def validate(self) -> bool:
        """Validate the delete task command."""
        if not self.task_id or not self.task_id.strip():
            self.error = "Task ID is required"
            return False
        return True


# ===== Knowledge Item Commands =====

class CreateKnowledgeItemCommand(BaseCommand):
    """Command to create a knowledge item."""

    def __init__(self, title: str, content: str,
                 note_type: str = "idea",
                 status: str = "draft",
                 author: Optional[str] = None,
                 tags: Optional[list] = None,
                 links: Optional[list] = None,
                 project_id: Optional[str] = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 command_id: Optional[str] = None):
        """
        Initialize create knowledge item command.

        Args:
            title: Knowledge item title
            content: Knowledge item content
            note_type: Type of knowledge note (defaults to "idea")
            status: Knowledge status (defaults to "draft")
            author: Author of the knowledge item
            tags: List of tags for categorization
            links: List of related knowledge item IDs
            project_id: Associated project ID (if any)
            metadata: Additional metadata
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.title = title
        self.content = content
        self.note_type = note_type
        self.status = status
        self.author = author
        self.tags = tags or []
        self.links = links or []
        self.project_id = project_id
        self.metadata = metadata or {}

    def get_command_type(self) -> str:
        return "create_knowledge_item"

    def validate(self) -> bool:
        """Validate the create knowledge item command."""
        if not self.title or not self.title.strip():
            self.error = "Knowledge item title is required"
            return False

        if not self.content or not self.content.strip():
            self.error = "Knowledge item content is required"
            return False

        return True


class GetKnowledgeItemCommand(BaseCommand):
    """Command to get a knowledge item by ID."""

    def __init__(self, item_id: str, command_id: Optional[str] = None):
        """
        Initialize get knowledge item command.

        Args:
            item_id: ID of the knowledge item to retrieve
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.item_id = item_id

    def get_command_type(self) -> str:
        return "get_knowledge_item"

    def validate(self) -> bool:
        """Validate the get knowledge item command."""
        if not self.item_id or not self.item_id.strip():
            self.error = "Knowledge item ID is required"
            return False
        return True


class ListKnowledgeItemsCommand(BaseCommand):
    """Command to list knowledge items with optional pagination."""

    def __init__(self, limit: Optional[int] = None, offset: int = 0,
                 command_id: Optional[str] = None):
        """
        Initialize list knowledge items command.

        Args:
            limit: Maximum number of items to return
            offset: Number of items to skip
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.limit = limit
        self.offset = offset

    def get_command_type(self) -> str:
        return "list_knowledge_items"

    def validate(self) -> bool:
        """Validate the list knowledge items command."""
        if self.limit is not None and self.limit < 0:
            self.error = "Limit must be non-negative"
            return False
        if self.offset < 0:
            self.error = "Offset must be non-negative"
            return False
        return True


class UpdateKnowledgeItemCommand(BaseCommand):
    """Command to update a knowledge item."""

    def __init__(self, item_id: str, title: Optional[str] = None,
                 content: Optional[str] = None,
                 note_type: Optional[str] = None,
                 status: Optional[str] = None,
                 author: Optional[str] = None,
                 project_id: Optional[str] = None,
                 tags: Optional[list] = None,
                 links: Optional[list] = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 command_id: Optional[str] = None):
        """
        Initialize update knowledge item command.

        Args:
            item_id: ID of the knowledge item to update
            title: New title (optional)
            content: New content (optional)
            note_type: New note type (optional)
            status: New status (optional)
            author: New author (optional)
            project_id: New project ID (optional)
            tags: New list of tags (optional)
            links: New list of related knowledge item IDs (optional)
            metadata: New additional metadata (optional)
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.item_id = item_id
        self.title = title
        self.content = content
        self.note_type = note_type
        self.status = status
        self.author = author
        self.project_id = project_id
        self.tags = tags
        self.links = links
        self.metadata = metadata

    def get_command_type(self) -> str:
        return "update_knowledge_item"

    def validate(self) -> bool:
        """Validate the update knowledge item command."""
        if not self.item_id or not self.item_id.strip():
            self.error = "Knowledge item ID is required"
            return False

        return True


class DeleteKnowledgeItemCommand(BaseCommand):
    """Command to delete a knowledge item."""

    def __init__(self, item_id: str, command_id: Optional[str] = None):
        """
        Initialize delete knowledge item command.

        Args:
            item_id: ID of the knowledge item to delete
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.item_id = item_id

    def get_command_type(self) -> str:
        return "delete_knowledge_item"

    def validate(self) -> bool:
        """Validate the delete knowledge item command."""
        if not self.item_id or not self.item_id.strip():
            self.error = "Knowledge item ID is required"
            return False
        return True


# ===== Tag Commands =====

class CreateTagCommand(BaseCommand):
    """Command to create a tag."""

    def __init__(self, name: str, color: Optional[str] = None,
                 description: Optional[str] = None,
                 command_id: Optional[str] = None):
        """
        Initialize create tag command.

        Args:
            name: Tag name
            color: Hex color code (optional)
            description: Tag description (optional)
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.name = name
        self.color = color
        self.description = description

    def get_command_type(self) -> str:
        return "create_tag"

    def validate(self) -> bool:
        """Validate the create tag command."""
        if not self.name or not self.name.strip():
            self.error = "Tag name is required"
            return False

        return True


class GetTagCommand(BaseCommand):
    """Command to get a tag by ID."""

    def __init__(self, tag_id: str, command_id: Optional[str] = None):
        """
        Initialize get tag command.

        Args:
            tag_id: ID of the tag to retrieve
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.tag_id = tag_id

    def get_command_type(self) -> str:
        return "get_tag"

    def validate(self) -> bool:
        """Validate the get tag command."""
        if not self.tag_id or not self.tag_id.strip():
            self.error = "Tag ID is required"
            return False
        return True


class ListTagsCommand(BaseCommand):
    """Command to list tags with optional pagination."""

    def __init__(self, limit: Optional[int] = None, offset: int = 0,
                 command_id: Optional[str] = None):
        """
        Initialize list tags command.

        Args:
            limit: Maximum number of tags to return
            offset: Number of tags to skip
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.limit = limit
        self.offset = offset

    def get_command_type(self) -> str:
        return "list_tags"

    def validate(self) -> bool:
        """Validate the list tags command."""
        if self.limit is not None and self.limit < 0:
            self.error = "Limit must be non-negative"
            return False
        if self.offset < 0:
            self.error = "Offset must be non-negative"
            return False
        return True


class UpdateTagCommand(BaseCommand):
    """Command to update a tag."""

    def __init__(self, tag_id: str, name: Optional[str] = None,
                 color: Optional[str] = None,
                 description: Optional[str] = None,
                 command_id: Optional[str] = None):
        """
        Initialize update tag command.

        Args:
            tag_id: ID of the tag to update
            name: New tag name (optional)
            color: New hex color code (optional)
            description: New tag description (optional)
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.tag_id = tag_id
        self.name = name
        self.color = color
        self.description = description

    def get_command_type(self) -> str:
        return "update_tag"

    def validate(self) -> bool:
        """Validate the update tag command."""
        if not self.tag_id or not self.tag_id.strip():
            self.error = "Tag ID is required"
            return False

        return True


class DeleteTagCommand(BaseCommand):
    """Command to delete a tag."""

    def __init__(self, tag_id: str, command_id: Optional[str] = None):
        """
        Initialize delete tag command.

        Args:
            tag_id: ID of the tag to delete
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.tag_id = tag_id

    def get_command_type(self) -> str:
        return "delete_tag"

    def validate(self) -> bool:
        """Validate the delete tag command."""
        if not self.tag_id or not self.tag_id.strip():
            self.error = "Tag ID is required"
            return False
        return True


# ===== Knowledge Base Commands =====

class CreateKnowledgeBaseCommand(BaseCommand):
    """Command to create a knowledge base."""

    def __init__(self, name: str, description: str,
                 tags: Optional[list] = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 command_id: Optional[str] = None):
        """
        Initialize create knowledge base command.

        Args:
            name: Knowledge base name
            description: Knowledge base description
            tags: Initial set of available tags
            metadata: Additional metadata
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.name = name
        self.description = description
        self.tags = tags or []
        self.metadata = metadata or {}

    def get_command_type(self) -> str:
        return "create_knowledge_base"

    def validate(self) -> bool:
        """Validate the create knowledge base command."""
        if not self.name or not self.name.strip():
            self.error = "Knowledge base name is required"
            return False

        return True


class GetKnowledgeBaseCommand(BaseCommand):
    """Command to get a knowledge base by ID."""

    def __init__(self, kb_id: str, command_id: Optional[str] = None):
        """
        Initialize get knowledge base command.

        Args:
            kb_id: ID of the knowledge base to retrieve
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.kb_id = kb_id

    def get_command_type(self) -> str:
        return "get_knowledge_base"

    def validate(self) -> bool:
        """Validate the get knowledge base command."""
        if not self.kb_id or not self.kb_id.strip():
            self.error = "Knowledge base ID is required"
            return False
        return True


class ListKnowledgeBasesCommand(BaseCommand):
    """Command to list knowledge bases with optional pagination."""

    def __init__(self, limit: Optional[int] = None, offset: int = 0,
                 command_id: Optional[str] = None):
        """
        Initialize list knowledge bases command.

        Args:
            limit: Maximum number of knowledge bases to return
            offset: Number of knowledge bases to skip
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.limit = limit
        self.offset = offset

    def get_command_type(self) -> str:
        return "list_knowledge_bases"

    def validate(self) -> bool:
        """Validate the list knowledge bases command."""
        if self.limit is not None and self.limit < 0:
            self.error = "Limit must be non-negative"
            return False
        if self.offset < 0:
            self.error = "Offset must be non-negative"
            return False
        return True


class UpdateKnowledgeBaseCommand(BaseCommand):
    """Command to update a knowledge base."""

    def __init__(self, kb_id: str, name: Optional[str] = None,
                 description: Optional[str] = None,
                 tags: Optional[list] = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 command_id: Optional[str] = None):
        """
        Initialize update knowledge base command.

        Args:
            kb_id: ID of the knowledge base to update
            name: New knowledge base name (optional)
            description: New knowledge base description (optional)
            tags: New list of available tags (optional)
            metadata: New additional metadata (optional)
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.kb_id = kb_id
        self.name = name
        self.description = description
        self.tags = tags
        self.metadata = metadata

    def get_command_type(self) -> str:
        return "update_knowledge_base"

    def validate(self) -> bool:
        """Validate the update knowledge base command."""
        if not self.kb_id or not self.kb_id.strip():
            self.error = "Knowledge base ID is required"
            return False

        return True


class DeleteKnowledgeBaseCommand(BaseCommand):
    """Command to delete a knowledge base."""

    def __init__(self, kb_id: str, command_id: Optional[str] = None):
        """
        Initialize delete knowledge base command.

        Args:
            kb_id: ID of the knowledge base to delete
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.kb_id = kb_id

    def get_command_type(self) -> str:
        return "delete_knowledge_base"

    def validate(self) -> bool:
        """Validate the delete knowledge base command."""
        if not self.kb_id or not self.kb_id.strip():
            self.error = "Knowledge base ID is required"
            return False
        return True


# ===== Association Commands =====

class AddKnowledgeItemTagCommand(BaseCommand):
    """Command to add a tag to a knowledge item."""

    def __init__(self, item_id: str, tag: str,
                 command_id: Optional[str] = None):
        """
        Initialize add knowledge item tag command.

        Args:
            item_id: ID of the knowledge item
            tag: Tag to add
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.item_id = item_id
        self.tag = tag

    def get_command_type(self) -> str:
        return "add_knowledge_item_tag"

    def validate(self) -> bool:
        """Validate the add knowledge item tag command."""
        if not self.item_id or not self.item_id.strip():
            self.error = "Knowledge item ID is required"
            return False

        if not self.tag or not self.tag.strip():
            self.error = "Tag is required"
            return False

        return True


class RemoveKnowledgeItemTagCommand(BaseCommand):
    """Command to remove a tag from a knowledge item."""

    def __init__(self, item_id: str, tag: str,
                 command_id: Optional[str] = None):
        """
        Initialize remove knowledge item tag command.

        Args:
            item_id: ID of the knowledge item
            tag: Tag to remove
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.item_id = item_id
        self.tag = tag

    def get_command_type(self) -> str:
        return "remove_knowledge_item_tag"

    def validate(self) -> bool:
        """Validate the remove knowledge item tag command."""
        if not self.item_id or not self.item_id.strip():
            self.error = "Knowledge item ID is required"
            return False

        if not self.tag or not self.tag.strip():
            self.error = "Tag is required"
            return False

        return True


class AddKnowledgeItemLinkCommand(BaseCommand):
    """Command to add a link to another knowledge item."""

    def __init__(self, item_id: str, linked_item_id: str,
                 command_id: Optional[str] = None):
        """
        Initialize add knowledge item link command.

        Args:
            item_id: ID of the knowledge item
            linked_item_id: ID of the knowledge item to link to
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.item_id = item_id
        self.linked_item_id = linked_item_id

    def get_command_type(self) -> str:
        return "add_knowledge_item_link"

    def validate(self) -> bool:
        """Validate the add knowledge item link command."""
        if not self.item_id or not self.item_id.strip():
            self.error = "Knowledge item ID is required"
            return False

        if not self.linked_item_id or not self.linked_item_id.strip():
            self.error = "Linked knowledge item ID is required"
            return False

        # Prevent self-linking
        if self.item_id == self.linked_item_id:
            self.error = "Cannot link a knowledge item to itself"
            return False

        return True


class RemoveKnowledgeItemLinkCommand(BaseCommand):
    """Command to remove a link from a knowledge item."""

    def __init__(self, item_id: str, linked_item_id: str,
                 command_id: Optional[str] = None):
        """
        Initialize remove knowledge item link command.

        Args:
            item_id: ID of the knowledge item
            linked_item_id: ID of the knowledge item to unlink
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.item_id = item_id
        self.linked_item_id = linked_item_id

    def get_command_type(self) -> str:
        return "remove_knowledge_item_link"

    def validate(self) -> bool:
        """Validate the remove knowledge item link command."""
        if not self.item_id or not self.item_id.strip():
            self.error = "Knowledge item ID is required"
            return False

        if not self.linked_item_id or not self.linked_item_id.strip():
            self.error = "Linked knowledge item ID is required"
            return False

        return True


# ===== Knowledge Base Association Commands =====

class AddKnowledgeItemToBaseCommand(BaseCommand):
    """Command to add a knowledge item to a knowledge base."""

    def __init__(self, kb_id: str, item_id: str,
                 command_id: Optional[str] = None):
        """
        Initialize add knowledge item to knowledge base command.

        Args:
            kb_id: ID of the knowledge base
            item_id: ID of the knowledge item to add
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.kb_id = kb_id
        self.item_id = item_id

    def get_command_type(self) -> str:
        return "add_knowledge_item_to_base"

    def validate(self) -> bool:
        """Validate the add knowledge item to knowledge base command."""
        if not self.kb_id or not self.kb_id.strip():
            self.error = "Knowledge base ID is required"
            return False

        if not self.item_id or not self.item_id.strip():
            self.error = "Knowledge item ID is required"
            return False

        return True


class RemoveKnowledgeItemFromBaseCommand(BaseCommand):
    """Command to remove a knowledge item from a knowledge base."""

    def __init__(self, kb_id: str, item_id: str,
                 command_id: Optional[str] = None):
        """
        Initialize remove knowledge item from knowledge base command.

        Args:
            kb_id: ID of the knowledge base
            item_id: ID of the knowledge item to remove
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.kb_id = kb_id
        self.item_id = item_id

    def get_command_type(self) -> str:
        return "remove_knowledge_item_from_base"

    def validate(self) -> bool:
        """Validate the remove knowledge item from knowledge base command."""
        if not self.kb_id or not self.kb_id.strip():
            self.error = "Knowledge base ID is required"
            return False

        if not self.item_id or not self.item_id.strip():
            self.error = "Knowledge item ID is required"
            return False

        return True


class AddTagToKnowledgeBaseCommand(BaseCommand):
    """Command to add a tag to a knowledge base."""

    def __init__(self, kb_id: str, tag: str,
                 command_id: Optional[str] = None):
        """
        Initialize add tag to knowledge base command.

        Args:
            kb_id: ID of the knowledge base
            tag: Tag to add
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.kb_id = kb_id
        self.tag = tag

    def get_command_type(self) -> str:
        return "add_tag_to_knowledge_base"

    def validate(self) -> bool:
        """Validate the add tag to knowledge base command."""
        if not self.kb_id or not self.kb_id.strip():
            self.error = "Knowledge base ID is required"
            return False

        if not self.tag or not self.tag.strip():
            self.error = "Tag is required"
            return False

        return True


class RemoveTagFromKnowledgeBaseCommand(BaseCommand):
    """Command to remove a tag from a knowledge base."""

    def __init__(self, kb_id: str, tag: str,
                 command_id: Optional[str] = None):
        """
        Initialize remove tag from knowledge base command.

        Args:
            kb_id: ID of the knowledge base
            tag: Tag to remove
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.kb_id = kb_id
        self.tag = tag

    def get_command_type(self) -> str:
        return "remove_tag_from_knowledge_base"

    def validate(self) -> bool:
        """Validate the remove tag from knowledge base command."""
        if not self.kb_id or not self.kb_id.strip():
            self.error = "Knowledge base ID is required"
            return False

        if not self.tag or not self.tag.strip():
            self.error = "Tag is required"
            return False

        return True


# ===== Scheduling Commands =====

class SchedulePreparationBlocksCommand(BaseCommand):
    """Command to schedule preparation blocks."""

    def __init__(self, preparation_block_ids: list,
                 planning_horizon_start: datetime,
                 planning_horizon_end: datetime,
                 granularity_minutes: int = 30,
                 command_id: Optional[str] = None):
        """
        Initialize schedule preparation blocks command.

        Args:
            preparation_block_ids: List of preparation block IDs to schedule
            planning_horizon_start: Start of planning horizon
            planning_horizon_end: End of planning horizon
            granularity_minutes: Time slot granularity in minutes
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.preparation_block_ids = preparation_block_ids
        self.planning_horizon_start = planning_horizon_start
        self.planning_horizon_end = planning_horizon_end
        self.granularity_minutes = granularity_minutes

    def get_command_type(self) -> str:
        return "schedule_preparation_blocks"

    def validate(self) -> bool:
        """Validate the schedule preparation blocks command."""
        if not self.preparation_block_ids:
            self.error = "At least one preparation block ID is required"
            return False

        if self.planning_horizon_start >= self.planning_horizon_end:
            self.error = "Planning horizon start must be before end"
            return False

        if self.granularity_minutes <= 0:
            self.error = "Granularity minutes must be positive"
            return False

        return True


class ScheduleTasksCommand(BaseCommand):
    """Command to schedule tasks."""

    def __init__(self, task_ids: list,
                 planning_horizon_start: datetime,
                 planning_horizon_end: datetime,
                 granularity_minutes: int = 30,
                 command_id: Optional[str] = None):
        """
        Initialize schedule tasks command.

        Args:
            task_ids: List of task IDs to schedule
            planning_horizon_start: Start of planning horizon
            planning_horizon_end: End of planning horizon
            granularity_minutes: Time slot granularity in minutes
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.task_ids = task_ids
        self.planning_horizon_start = planning_horizon_start
        self.planning_horizon_end = planning_horizon_end
        self.granularity_minutes = granularity_minutes

    def get_command_type(self) -> str:
        return "schedule_tasks"

    def validate(self) -> bool:
        """Validate the schedule tasks command."""
        if not self.task_ids:
            self.error = "At least one task ID is required"
            return False

        if self.planning_horizon_start >= self.planning_horizon_end:
            self.error = "Planning horizon start must be before end"
            return False

        if self.granularity_minutes <= 0:
            self.error = "Granularity minutes must be positive"
            return False

        return True


class ScheduleLearningTasksCommand(BaseCommand):
    """Command to schedule learning/review tasks for knowledge items."""

    def __init__(self, knowledge_item_ids: list,
                 planning_horizon_start: datetime,
                 planning_horizon_end: datetime,
                 granularity_minutes: int = 30,
                 review_interval_days: int = 7,
                 command_id: Optional[str] = None):
        """
        Initialize schedule learning tasks command.

        Args:
            knowledge_item_ids: List of knowledge item IDs to schedule learning for
            planning_horizon_start: Start of planning horizon
            planning_horizon_end: End of planning horizon
            granularity_minutes: Time slot granularity in minutes
            review_interval_days: How often to review items (in days)
            command_id: Optional command ID
        """

        super().__init__(command_id)
        self.knowledge_item_ids = knowledge_item_ids
        self.planning_horizon_start = planning_horizon_start
        self.planning_horizon_end = planning_horizon_end
        self.granularity_minutes = granularity_minutes
        self.review_interval_days = review_interval_days

    def get_command_type(self) -> str:
        return "schedule_learning_tasks"

    def validate(self) -> bool:
        """Validate the schedule learning tasks command."""
        if not self.knowledge_item_ids:
            self.error = "At least one knowledge item ID is required"
            return False

        if self.planning_horizon_start >= self.planning_horizon_end:
            self.error = "Planning horizon start must be before end"
            return False

        if self.granularity_minutes <= 0:
            self.error = "Granularity minutes must be positive"
            return False

        if self.review_interval_days <= 0:
            self.error = "Review interval days must be positive"
            return False

        return True


# ===== Domain-Specific Operation Commands =====

class CreatePreparationFromEventCommand(BaseCommand):
    """Command to create a preparation block from a university event."""

    def __init__(self, event_id: str, preparation_minutes: int,
                 lead_time_hours: int = 24,
                 command_id: Optional[str] = None):
        """
        Initialize create preparation from event command.

        Args:
            event_id: ID of the source university event
            preparation_minutes: Duration of preparation in minutes
            lead_time_hours: Hours before the event to start preparation
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.event_id = event_id
        self.preparation_minutes = preparation_minutes
        self.lead_time_hours = lead_time_hours

    def get_command_type(self) -> str:
        return "create_preparation_from_event"

    def validate(self) -> bool:
        """Validate the create preparation from event command."""
        if not self.event_id or not self.event_id.strip():
            self.error = "Event ID is required"
            return False

        if self.preparation_minutes <= 0:
            self.error = "Preparation minutes must be positive"
            return False

        if self.lead_time_hours < 0:
            self.error = "Lead time hours must be non-negative"
            return False

        return True


class EventsNeedingPreparationCommand(BaseCommand):
    """Command to determine which events need preparation blocks."""

    def __init__(self, event_ids: list,
                 min_preparation_minutes: int = 30,
                 command_id: Optional[str] = None):
        """
        Initialize events needing preparation command.

        Args:
            event_ids: List of university event IDs to check
            min_preparation_minutes: Minimum preparation time to consider
            command_id: Optional command ID
        """
        super().__init__(command_id)
        self.event_ids = event_ids
        self.min_preparation_minutes = min_preparation_minutes

    def get_command_type(self) -> str:
        return "events_needing_preparation"

    def validate(self) -> bool:
        """Validate the events needing preparation command."""
        if not self.event_ids:
            self.error = "At least one event ID is required"
            return False

        if self.min_preparation_minutes < 0:
            self.error = "Minimum preparation minutes must be non-negative"
            return False

        return True


# Export all command classes for easy importing
__all__ = [
    # Base classes
    "BaseCommand",
    "CommandStatus",

    # University Commands
    "CreateUniversityEventCommand",
    "GetUniversityEventCommand",
    "ListUniversityEventsCommand",
    "UpdateUniversityEventCommand",
    "DeleteUniversityEventCommand",

    # Preparation Block Commands
    "CreatePreparationBlockCommand",
    "GetPreparationBlockCommand",
    "ListPreparationBlocksCommand",
    "UpdatePreparationBlockCommand",
    "DeletePreparationBlockCommand",

    # Project Commands
    "CreateProjectCommand",
    "GetProjectCommand",
    "ListProjectsCommand",
    "UpdateProjectCommand",
    "DeleteProjectCommand",

    # Task Commands
    "CreateTaskCommand",
    "GetTaskCommand",
    "ListTasksCommand",
    "UpdateTaskCommand",
    "DeleteTaskCommand",

    # Knowledge Item Commands
    "CreateKnowledgeItemCommand",
    "GetKnowledgeItemCommand",
    "ListKnowledgeItemsCommand",
    "UpdateKnowledgeItemCommand",
    "DeleteKnowledgeItemCommand",

    # Tag Commands
    "CreateTagCommand",
    "GetTagCommand",
    "ListTagsCommand",
    "UpdateTagCommand",
    "DeleteTagCommand",

    # Knowledge Base Commands
    "CreateKnowledgeBaseCommand",
    "GetKnowledgeBaseCommand",
    "ListKnowledgeBasesCommand",
    "UpdateKnowledgeBaseCommand",
    "DeleteKnowledgeBaseCommand",

    # Association Commands
    "AddKnowledgeItemTagCommand",
    "RemoveKnowledgeItemTagCommand",
    "AddKnowledgeItemLinkCommand",
    "RemoveKnowledgeItemLinkCommand",
    "AddKnowledgeItemToBaseCommand",
    "RemoveKnowledgeItemFromBaseCommand",
    "AddTagToKnowledgeBaseCommand",
    "RemoveTagFromKnowledgeBaseCommand",

    # Scheduling Commands
    "SchedulePreparationBlocksCommand",
    "ScheduleTasksCommand",
    "ScheduleLearningTasksCommand",

    # Domain-Specific Operation Commands
    "CreatePreparationFromEventCommand",
    "EventsNeedingPreparationCommand",
]
# End of command definitions