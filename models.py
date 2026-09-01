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
    SEMINAR = "СЕМ"     # Семинар
    WORKSHOP = "ВШ"     # Workshop
    EXAM = "ЭКЗ"        # Экзамен
    UNKNOWN = ""        # Неизвестный тип

    @classmethod
    def from_string(cls, value: str) -> EventType:
        """Create EventType from string representation."""
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


class UniversityEventStatus(Enum):
    """Status supplied by the university calendar source."""
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    CANCELLED = "cancelled"

    @classmethod
    def from_string(cls, value: Optional[str]) -> UniversityEventStatus:
        if not value:
            return cls.CONFIRMED
        try:
            return cls(value.strip().lower())
        except ValueError:
            return cls.CONFIRMED


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
    status: UniversityEventStatus = UniversityEventStatus.CONFIRMED
    sequence: int = 0
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

    @property
    def is_cancelled(self) -> bool:
        """Whether the source explicitly cancelled this event."""
        return self.status == UniversityEventStatus.CANCELLED


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
    INBOX = "inbox"
    REVIEW = "review"
    DONE = "done"
    COMPLETED = "completed"
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
    time_estimate: Optional[Estimate] = None
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


# ===== PERSONAL EVENT DOMAIN MODELS =====

class PersonalEventState(Enum):
    """State of a personal university event (attendance)."""
    EXPECTED = "expected"       # Student plans to attend
    CONFIRMED = "confirmed"     # Student will attend (high confidence)
    MOVED = "moved"             # Event was rescheduled, student will attend
    CANCELLED = "cancelled"     # Event cancelled
    POSSIBLY_CANCELLED = "possibly_cancelled"  # Possibly cancelled (needs review)
    NEEDS_REVIEW = "needs_review"  # Manual review required


class AttendanceConfidence(Enum):
    """Confidence level in attendance prediction."""
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class MatchType(Enum):
    """Type of match between personal event and university event."""
    EXACT = "exact"             # Exact match on time and location
    PARTIAL_TIME = "partial_time"  # Partial match (time + some other factors)
    TITLE_ONLY = "title_only"     # Match on title only
    TIME_ONLY = "time_only"     # Match on time only
    LOCATION_ONLY = "location_only"  # Match on location only
    FUZZY = "fuzzy"             # Fuzzy match (title, description)
    MANUAL = "manual"           # Manually created personal event
    NO_MATCH = "no_match"       # No match found


@dataclass
class PersonalUniversityEvent:
    """Represents a personal university event (student's attendance)."""
    id: str
    title: str
    description: str
    start_time: datetime
    end_time: datetime
    location: Optional[str] = None
    university_event_uid: Optional[str] = None  # UID of the linked university event
    preparation_block_uid: Optional[str] = None  # UID of the linked preparation block
    task_uid: Optional[str] = None  # UID of the linked task
    source: Optional[str] = None  # Source of the personal event (e.g., telegram, manual)
    state: PersonalEventState = PersonalEventState.EXPECTED
    match_confidence: Optional[AttendanceConfidence] = None
    match_type: Optional[MatchType] = None
    metadata: dict[str, any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure timestamps are set if not provided and validate time ordering."""
        if not self.start_time:
            self.start_time = datetime.now()
        if not self.end_time:
            self.end_time = self.start_time

        # Validate that end_time is after start_time
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")

    @property
    def duration_minutes(self) -> int:
        """Calculate event duration in minutes."""
        return int((self.end_time - self.start_time).total_seconds() / 60)

    def is_matched(self) -> bool:
        """
        Check if the personal event is matched to a university event.

        Returns:
            bool: True if state is CONFIRMED and university_event_uid is set
        """
        return self.state == PersonalEventState.CONFIRMED and self.university_event_uid is not None

    def get_match_quality(self) -> float:
        """
        Calculate match quality score based on confidence and match type.

        Returns:
            float: Quality score between 0.0 and 1.0
        """
        if not self.is_matched():
            return 0.0

        # Confidence values
        confidence_values = {
            AttendanceConfidence.VERY_LOW: 0.0,
            AttendanceConfidence.LOW: 0.25,
            AttendanceConfidence.MEDIUM: 0.5,
            AttendanceConfidence.HIGH: 0.75,
            AttendanceConfidence.VERY_HIGH: 1.0
        }

        # Match type values
        match_type_values = {
            MatchType.EXACT: 1.0,
            MatchType.PARTIAL_TIME: 0.8,
            MatchType.TITLE_ONLY: 0.6,
            MatchType.TIME_ONLY: 0.5,
            MatchType.LOCATION_ONLY: 0.5,
            MatchType.FUZZY: 0.4,
            MatchType.MANUAL: 0.9,
            MatchType.NO_MATCH: 0.0
        }

        confidence_value = confidence_values.get(self.match_confidence, 0.0)
        match_type_value = match_type_values.get(self.match_type, 0.0)

        return confidence_value * match_type_value

    def apply_match_result(self, match_result: AttendanceMatchResult) -> None:
        """
        Apply a match result to update the personal event's state and related fields.
        Implements the state transition logic based on match type and confidence.

        Args:
            match_result: The result of matching this personal event to a university event
        """
        # Update match-related fields
        self.match_confidence = match_result.confidence
        self.match_type = match_result.match_type
        if match_result.personal_event_id:
            self.id = match_result.personal_event_id
        if match_result.university_event_uid:
            self.university_event_uid = match_result.university_event_uid

        # State transition logic
        # If no match, move to POSSIBLY_CANCELLED (unless already in a more severe state?)
        if match_result.match_type == MatchType.NO_MATCH:
            # Only transition to POSSIBLY_CANCELLED if not already in a more severe state
            if self.state in (PersonalEventState.EXPECTED, PersonalEventState.CONFIRMED):
                self.state = PersonalEventState.POSSIBLY_CANCELLED
            # If already POSSIBLY_CANCELLED or NEEDS_REVIEW, stay as is
            return

        # For matches, determine new state based on confidence and match type
        confidence = match_result.confidence
        match_type = match_result.match_type

        # Transition rules derived from tests:
        # EXPECTED + (EXACT, HIGH) -> CONFIRMED
        # EXPECTED + (PARTIAL_TIME, MEDIUM) -> MOVED
        # EXPECTED + (NO_MATCH, LOW) -> POSSIBLY_CANCELLED
        # CONFIRMED + (PARTIAL_TIME, MEDIUM) -> MOVED
        # Any state + (FUZZY, LOW) -> NEEDS_REVIEW

        if self.state == PersonalEventState.EXPECTED:
            if match_type == MatchType.EXACT and confidence in (AttendanceConfidence.HIGH, AttendanceConfidence.VERY_HIGH):
                self.state = PersonalEventState.CONFIRMED
            elif match_type == MatchType.PARTIAL_TIME and confidence == AttendanceConfidence.MEDIUM:
                self.state = PersonalEventState.MOVED
            elif match_type == MatchType.FUZZY and confidence == AttendanceConfidence.LOW:
                self.state = PersonalEventState.NEEDS_REVIEW
            else:
                # Default for other combinations: stay EXPECTED? Or maybe POSSIBLY_CANCELLED?
                # Based on test, only the specific combinations change state; others remain EXPECTED.
                # We'll keep the current state (EXPECTED) for unhandled cases.
                pass
        elif self.state == PersonalEventState.CONFIRMED:
            if match_type == MatchType.PARTIAL_TIME and confidence == AttendanceConfidence.MEDIUM:
                self.state = PersonalEventState.MOVED
            elif match_type == MatchType.FUZZY and confidence == AttendanceConfidence.LOW:
                self.state = PersonalEventState.NEEDS_REVIEW
            # Otherwise, stay CONFIRMED
        elif self.state == PersonalEventState.MOVED:
            # If we get a match, we might stay MOVED or go to CONFIRMED?
            # Tests don't cover this, but we can assume that a good match could reconfirm.
            # For simplicity, we'll only change state for low confidence fuzzy match to NEEDS_REVIEW.
            if match_type == MatchType.FUZZY and confidence == AttendanceConfidence.LOW:
                self.state = PersonalEventState.NEEDS_REVIEW
            # Otherwise, stay MOVED
        # For POSSIBLY_CANCELLED and NEEDS_REVIEW, we don't change state on match (they indicate issues)
        # unless we get a very good match? The tests don't specify. We'll leave as is.

        # Note: The test for is_matched() considers only CONFIRMED with university_event_uid as matched.
        # So we don't need to adjust anything else for that.


# ===== ATTENDANCE DOMAIN MODELS =====

@dataclass
class PersonalAttendanceRule:
    """Rule for matching personal events to university events."""
    id: str
    description: str
    time_tolerance_minutes: int = 15
    title_similarity_threshold: float = 0.8
    min_confidence_for_match: AttendanceConfidence = AttendanceConfidence.MEDIUM
    require_exact_event_type: bool = False
    metadata: dict[str, any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate rule parameters."""
        if self.time_tolerance_minutes < 0:
            raise ValueError("Time tolerance must be non-negative")
        if not 0.0 <= self.title_similarity_threshold <= 1.0:
            raise ValueError("Title similarity threshold must be between 0.0 and 1.0")


@dataclass
class AttendanceMatchResult:
    """Result of matching a personal event to a university event."""
    personal_event_id: Optional[str]
    university_event_uid: Optional[str]
    match_type: MatchType
    confidence: AttendanceConfidence
    explanation: str
    alternatives: List[Tuple[str, MatchType, AttendanceConfidence]] = field(default_factory=list)
    metadata: dict[str, any] = field(default_factory=dict)

    @property
    def is_matched(self) -> bool:
        """Return True if a match was found (match_type is not NO_MATCH)."""
        return self.match_type != MatchType.NO_MATCH


# ===== PREPARATION DOMAIN MODELS =====

class EstimateSource(Enum):
    """Source of an estimate."""
    DEFAULT_RULE = "default_rule"           # Assessment by default rule (based on event type)
    HISTORICAL_DATA = "historical_data"     # Based on historical execution data
    EXPERT_OPINION = "expert_opinion"       # Expert assessment (teacher, mentor)
    USER_INPUT = "user_input"               # Direct user input
    AI_PREDICTION = "ai_prediction"         # AI model prediction
    GROUP_CONSENSUS = "group_consensus"     # Group or team consensus


@dataclass(frozen=True)
class Estimate:
    """Estimate of an uncertain value (duration, workload, etc.)."""
    value: float                            # Estimated value (in minutes for time estimates)
    source: EstimateSource                  # Source of the estimate
    confidence: float = 1.0                 # Confidence in estimate (0.0-1.0)

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")


class PreparationStatus(Enum):
    """Status of a preparation requirement."""
    INBOX = "inbox"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class PreparationPolicy:
    """Policy for determining preparation time estimates."""

    @staticmethod
    def create_preparation_estimate(
        session_type: str,
        source: EstimateSource,
        confidence: float,
        course_id: Optional[str],
        event_id: Optional[str],
        difficulty: float = 1.0,
        priority: int = 2,
        hours_until_deadline: Optional[float] = None,
        course_multiplier: float = 1.0,
    ) -> Estimate:
        """
        Create a preparation time estimate based on policy.
        Simplified implementation: returns a fixed estimate based on session type.
        In a real implementation, this would use lookup tables or ML models.
        """
        # Default preparation times in minutes by session type
        default_times = {
            "lecture": 20,
            "lab": 60,
            "practical": 60,
            "study": 30,
            "review": 20,
            "practice": 25,
        }
        base_minutes = default_times.get(session_type.lower(), 30)
        difficulty = min(2.0, max(0.5, float(difficulty)))
        priority_multiplier = {
            1: 0.85,
            2: 1.0,
            3: 1.2,
            4: 1.35,
            5: 1.5,
        }.get(min(5, max(1, int(priority))), 1.0)
        urgency_multiplier = 1.0
        if hours_until_deadline is not None:
            if hours_until_deadline <= 24:
                urgency_multiplier = 1.25
            elif hours_until_deadline <= 72:
                urgency_multiplier = 1.1
        raw_minutes = (
            base_minutes
            * difficulty
            * priority_multiplier
            * max(0.5, float(course_multiplier))
            * urgency_multiplier
        )
        rounded_minutes = min(240, max(15, int(round(raw_minutes / 5) * 5)))
        return Estimate(value=rounded_minutes, source=source, confidence=confidence)


@dataclass
class PreparationRequirement:
    """Represents a preparation requirement for a personal university event."""
    id: str
    title: str
    description: str
    source_session_id: str  # Link to the personal event (or university event?)
    source_task_id: Optional[str]  # Not derived from a task
    preparation_type: str  # e.g., 'study', 'review', 'practice'
    _time_estimate: Estimate
    due_time: datetime
    required_materials: List[str] = field(default_factory=list)
    prerequisites: List[str] = field(default_factory=list)
    status: PreparationStatus = PreparationStatus.INBOX
    metadata: dict[str, any] = field(default_factory=dict)

    @property
    def time_estimate(self) -> Estimate:
        return self._time_estimate


# ===== WEEKLY CAPACITY MODEL =====


@dataclass
class WeeklyCapacityModel:
    """Model for tracking weekly available capacity after accounting for fixed commitments."""
    # Total available minutes in a week (7 days * 24 hours * 60 minutes)
    total_week_minutes: int = 7 * 24 * 60  # 10080 minutes

    # Fixed commitments that cannot be moved (measured in minutes)
    sleep_block: Optional[int] = None          # Time allocated for sleep
    fixed_commitments: int = 0                 # Other fixed commitments (meetings, appointments, etc.)
    university_load: int = 0                   # University classes, lectures, etc.
    teaching_load: int = 0                     # Teaching responsibilities (if applicable)
    travel_load: int = 0                       # Time spent traveling between activities
    recovery_block: Optional[int] = None       # Time needed for recovery and rest
    buffer: Optional[int] = None               # Buffer time for unexpected events

    # Configurable norms (can be adjusted based on user preferences/policies)
    # These are defaults but can be overridden
    sleep_norm_per_day: int = 8 * 60      # 8 hours per day in minutes
    recovery_norm_per_day: int = 1 * 60   # 1 hour per day for recovery
    buffer_norm_per_day: int = 30         # 30 minutes per day for buffer

    def __post_init__(self):
        """Initialize with default norms if not explicitly set."""
        # If sleep_block is None, apply the sleep norm
        if self.sleep_block is None:
            self.sleep_block = self.sleep_norm_per_day * 7

        # If recovery_block is None, apply the recovery norm
        if self.recovery_block is None:
            self.recovery_block = self.recovery_norm_per_day * 7

        # If buffer is None, apply the buffer norm
        if self.buffer is None:
            self.buffer = self.buffer_norm_per_day * 7

        minute_fields = (
            'total_week_minutes', 'sleep_block', 'fixed_commitments',
            'university_load', 'teaching_load', 'travel_load',
            'recovery_block', 'buffer', 'sleep_norm_per_day',
            'recovery_norm_per_day', 'buffer_norm_per_day',
        )
        for field_name in minute_fields:
            if getattr(self, field_name) < 0:
                raise ValueError(f'{field_name} cannot be negative')

    @property
    def available_capacity(self) -> int:
        """
        Calculate available capacity for flexible scheduling.

        Returns:
            int: Available minutes in the week after subtracting all fixed commitments
        """
        # For fields with norms: None means "apply norm", 0 means "explicitly zero"
        sleep = self.sleep_block if self.sleep_block is not None else 0
        recovery = self.recovery_block if self.recovery_block is not None else 0
        buffer = self.buffer if self.buffer is not None else 0

        # For fields without norms: default to 0 (no time allocated)
        fixed = self.fixed_commitments
        university = self.university_load
        teaching = self.teaching_load
        travel = self.travel_load

        committed_time = (
            sleep +
            fixed +
            university +
            teaching +
            travel +
            recovery +
            buffer
        )

        available = self.total_week_minutes - committed_time
        # Ensure we don't return negative capacity
        return max(0, available)

    @property
    def utilization_ratio(self) -> float:
        """
        Calculate the ratio of utilized capacity to total capacity.

        Returns:
            float: Ratio between 0.0 and 1.0 (or >1.0 if over-capacity)
        """
        if self.total_week_minutes == 0:
            return 0.0
        committed_time = (
            self.sleep_block +
            self.fixed_commitments +
            self.university_load +
            self.teaching_load +
            self.travel_load +
            self.recovery_block +
            self.buffer
        )
        return committed_time / self.total_week_minutes

    def is_over_capacity(self) -> bool:
        """
        Check if the current commitments exceed available capacity.

        Returns:
            bool: True if over capacity, False otherwise
        """
        return self.available_capacity == 0 and (
            self.sleep_block +
            self.fixed_commitments +
            self.university_load +
            self.teaching_load +
            self.travel_load +
            self.recovery_block +
            self.buffer
        ) > self.total_week_minutes
