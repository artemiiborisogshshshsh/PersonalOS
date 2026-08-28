"""
Domain models for Personal OS AI Calendar system.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Set
import uuid


class EventType(Enum):
    """Types of university events."""
    LECTURE = "ЛК"      # Лекция
    LAB = "ЛБ"          # Лабораторная работа
    PRACTICAL = "ПР"    # Практическое занятие
    UNKNOWN = ""        # Неизвестный тип

    @classmethod
    def from_string(cls, value: str) -> EventType:
        """Create EventType from string representation."""
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


@dataclass
class UniversityEvent:
    """Represents a university event from ICS calendar."""
    uid: str
    summary: str
    description: str
    location: str
    dtstart: datetime
    dtend: datetime
    event_type: EventType
    is_group_event: bool = False
    summary_normalized: str = field(init=False)

    def __post_init__(self):
        """Normalize summary after initialization."""
        self.summary_normalized = ' '.join(self.summary.split())

    @property
    def duration_minutes(self) -> int:
        """Calculate event duration in minutes."""
        return int((self.dtend - self.dtstart).total_seconds() / 60)

    @property
    def is_lecture(self) -> bool:
        """Check if event is a lecture."""
        return self.event_type == EventType.LECTURE

    @property
    def is_lab(self) -> bool:
        """Check if event is a lab."""
        return self.event_type == EventType.LAB

    @property
    def is_practical(self) -> bool:
        """Check if event is a practical session."""
        return self.event_type == EventType.PRACTICAL


@dataclass
class PreparationBlock:
    """Represents a preparation block for a university event."""
    uid: str
    summary: str
    description: str
    dtstart: datetime
    dtend: datetime
    preparation_minutes: int
    source_event_uid: str
    event_type: Optional[EventType] = None
    calendar_id: str = field(default="", init=False)

    def __post_init__(self):
        """Set default calendar ID if not provided."""
        if not self.calendar_id:
            self.calendar_id = "University Schedule"

    @property
    def duration_minutes(self) -> int:
        """Get preparation block duration in minutes."""
        return self.preparation_minutes

    @property
    def source_event_type(self) -> str:
        """Get source event type as string."""
        return self.event_type.value if self.event_type else ""


@dataclass
class CalendarSyncResult:
    """Result of calendar synchronization operation."""
    success: bool
    events_processed: int = 0
    events_created: int = 0
    events_updated: int = 0
    events_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """Check if result contains errors."""
        return len(self.errors) > 0

    @property
    def has_warnings(self) -> bool:
        """Check if result contains warnings."""
        return len(self.warnings) > 0

    def add_error(self, error: str) -> None:
        """Add error message to result."""
        self.errors.append(error)
        self.success = False

    def add_warning(self, warning: str) -> None:
        """Add warning message to result."""
        self.warnings.append(warning)


# ===== PROJECT DOMAIN MODELS =====

class ProjectStatus(Enum):
    """Status of a project."""
    PLANNING = "planning"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    """Priority level for tasks."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskStatus(Enum):
    """Status of a task."""
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    ARCHIVED = "archived"


@dataclass
class Project:
    """Represents a project in the Personal OS system."""
    id: str
    name: str
    description: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime
    start_date: Optional[datetime] = None
    target_date: Optional[datetime] = None
    tags: Set[str] = field(default_factory=set)
    metadata: dict[str, any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure timestamps are set if not provided."""
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.updated_at:
            self.updated_at = self.created_at

    @property
    def is_active(self) -> bool:
        """Check if project is actively being worked on."""
        return self.status == ProjectStatus.ACTIVE

    @property
    def is_completed(self) -> bool:
        """Check if project is completed."""
        return self.status == ProjectStatus.COMPLETED

    def add_tag(self, tag: str) -> None:
        """Add a tag to the project."""
        self.tags.add(tag.lower().strip())

    def remove_tag(self, tag: str) -> None:
        """Remove a tag from the project."""
        self.tags.discard(tag.lower().strip())

    def has_tag(self, tag: str) -> bool:
        """Check if project has a specific tag."""
        return tag.lower().strip() in self.tags

    def update_status(self, status: ProjectStatus) -> None:
        """Update project status and timestamp."""
        self.status = status
        self.updated_at = datetime.now()


@dataclass
class Task:
    """Represents a task within a project or as a standalone item."""
    id: str
    title: str
    description: str
    project_id: Optional[str]  # None for standalone tasks
    status: TaskStatus
    priority: TaskPriority
    created_at: datetime
    updated_at: datetime
    due_date: Optional[datetime] = None
    estimated_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    assignee: Optional[str] = None
    tags: Set[str] = field(default_factory=set)
    dependencies: List[str] = field(default_factory=list)  # Task IDs that must be completed first
    metadata: dict[str, any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure timestamps are set if not provided."""
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.updated_at:
            self.updated_at = self.created_at

    @property
    def is_overdue(self) -> bool:
        """Check if task is overdue."""
        if not self.due_date or self.status in [TaskStatus.DONE, TaskStatus.ARCHIVED]:
            return False
        return datetime.now() > self.due_date

    @property
    def is_high_priority(self) -> bool:
        """Check if task is high or urgent priority."""
        return self.priority in [TaskPriority.HIGH, TaskPriority.URGENT]

    def add_tag(self, tag: str) -> None:
        """Add a tag to the task."""
        self.tags.add(tag.lower().strip())

    def remove_tag(self, tag: str) -> None:
        """Remove a tag from the task."""
        self.tags.discard(tag.lower().strip())

    def has_tag(self, tag: str) -> bool:
        """Check if task has a specific tag."""
        return tag.lower().strip() in self.tags

    def add_dependency(self, task_id: str) -> None:
        """Add a task dependency."""
        if task_id not in self.dependencies:
            self.dependencies.append(task_id)

    def remove_dependency(self, task_id: str) -> None:
        """Remove a task dependency."""
        if task_id in self.dependencies:
            self.dependencies.remove(task_id)

    def update_status(self, status: TaskStatus) -> None:
        """Update task status and timestamp."""
        self.status = status
        self.updated_at = datetime.now()


# ===== KNOWLEDGE DOMAIN MODELS =====

class NoteType(Enum):
    """Types of knowledge notes."""
    IDEA = "idea"
    REFERENCE = "reference"
    MEETING = "meeting"
    LEARNING = "learning"
    PROJECT = "project"
    PERSONAL = "personal"
    TEMPLATE = "template"


class KnowledgeStatus(Enum):
    """Status of knowledge items."""
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    ARCHIVED = "archived"


@dataclass
class Tag:
    """Represents a tag for categorizing knowledge and projects."""
    name: str
    color: Optional[str] = None  # Hex color code
    description: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """Normalize tag name."""
        self.name = self.name.lower().strip()


@dataclass
class KnowledgeItem:
    """Represents a piece of knowledge (note, document, etc.)."""
    id: str
    title: str
    content: str
    note_type: NoteType
    status: KnowledgeStatus
    created_at: datetime
    updated_at: datetime
    author: Optional[str] = None
    tags: Set[str] = field(default_factory=set)
    links: List[str] = field(default_factory=list)  # IDs of related knowledge items
    project_id: Optional[str] = None  # Associated project, if any
    metadata: dict[str, any] = field(default_factory=dict)

    def __post_init__(self):
        """Normalize fields and calculate derived properties."""
        # Normalize timestamps
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.updated_at:
            self.updated_at = self.created_at

        # Normalize tags
        self.tags = {tag.lower().strip() for tag in self.tags}

        # Word count is calculated property, not stored

    @property
    def word_count(self) -> int:
        """Calculate word count of content."""
        return len(self.content.split())

    @property
    def is_draft(self) -> bool:
        """Check if knowledge item is in draft status."""
        return self.status == KnowledgeStatus.DRAFT

    @property
    def is_published(self) -> bool:
        """Check if knowledge item is published."""
        return self.status == KnowledgeStatus.PUBLISHED

    def add_tag(self, tag: str) -> None:
        """Add a tag to the knowledge item."""
        self.tags.add(tag.lower().strip())

    def remove_tag(self, tag: str) -> None:
        """Remove a tag from the knowledge item."""
        self.tags.discard(tag.lower().strip())

    def has_tag(self, tag: str) -> bool:
        """Check if knowledge item has a specific tag."""
        return tag.lower().strip() in self.tags

    def add_link(self, knowledge_id: str) -> None:
        """Add a link to another knowledge item."""
        if knowledge_id not in self.links:
            self.links.append(knowledge_id)

    def remove_link(self, knowledge_id: str) -> None:
        """Remove a link to another knowledge item."""
        if knowledge_id in self.links:
            self.links.remove(knowledge_id)

    def update_status(self, status: KnowledgeStatus) -> None:
        """Update knowledge item status and timestamp."""
        self.status = status
        self.updated_at = datetime.now()


@dataclass
class KnowledgeBase:
    """Represents a collection of knowledge items (like a vault or notebook)."""
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    items: List[str] = field(default_factory=list)  # KnowledgeItem IDs
    tags: Set[str] = field(default_factory=set)  # Available tags in this knowledge base
    metadata: dict[str, any] = field(default_factory=dict)

    def __post_init__(self):
        """Set timestamps if not provided."""
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.updated_at:
            self.updated_at = self.created_at

    def add_item(self, knowledge_id: str) -> None:
        """Add a knowledge item to the base."""
        if knowledge_id not in self.items:
            self.items.append(knowledge_id)

    def remove_item(self, knowledge_id: str) -> None:
        """Remove a knowledge item from the base."""
        if knowledge_id in self.items:
            self.items.remove(knowledge_id)

    def has_item(self, knowledge_id: str) -> bool:
        """Check if knowledge base contains a specific item."""
        return knowledge_id in self.items

    def has_tag(self, tag: str) -> bool:
        """Check if knowledge base has a specific tag."""
        return tag.lower().strip() in self.tags

    def add_tag(self, tag: str) -> None:
        """Add a tag to the knowledge base's available tags."""
        self.tags.add(tag.lower().strip())

    def remove_tag(self, tag: str) -> None:
        """Remove a tag from the knowledge base's available tags."""
        self.tags.discard(tag.lower().strip())