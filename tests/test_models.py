"""
Unit tests for domain models.
"""

import pytest
from datetime import datetime, timedelta
from models import UniversityEvent, PreparationBlock, EventType, CalendarSyncResult, \
    Project, ProjectStatus, Task, TaskStatus, TaskPriority, \
    KnowledgeItem, NoteType, KnowledgeStatus, Tag, KnowledgeBase


def test_event_type_enum():
    """Test EventType enum functionality."""
    assert EventType.LECTURE.value == "ЛК"
    assert EventType.LAB.value == "ЛБ"
    assert EventType.PRACTICAL.value == "ПР"
    assert EventType.UNKNOWN.value == ""

    # Test from_string method
    assert EventType.from_string("ЛК") == EventType.LECTURE
    assert EventType.from_string("ЛБ") == EventType.LAB
    assert EventType.from_string("ПР") == EventType.PRACTICAL
    assert EventType.from_string("unknown") == EventType.UNKNOWN
    assert EventType.from_string("") == EventType.UNKNOWN


def test_university_event_creation():
    """Test UniversityEvent creation and properties."""
    start_time = datetime(2026, 9, 1, 10, 0)
    end_time = datetime(2026, 9, 1, 12, 0)

    event = UniversityEvent(
        uid="test-uid-123",
        summary="Математика (ЛК)",
        description="Лекция по высшей математике",
        location="Аудитория 101",
        dtstart=start_time,
        dtend=end_time,
        event_type=EventType.LECTURE,
        is_group_event=True
    )

    assert event.uid == "test-uid-123"
    assert event.summary == "Математика (ЛК)"
    assert event.description == "Лекция по высшей математике"
    assert event.location == "Аудитория 101"
    assert event.dtstart == start_time
    assert event.dtend == end_time
    assert event.event_type == EventType.LECTURE
    assert event.is_group_event == True
    assert event.summary_normalized == "Математика (ЛК)"
    assert event.duration_minutes == 120
    assert event.is_lecture == True
    assert event.is_lab == False
    assert event.is_practical == False


def test_university_event_summary_normalization():
    """Test that summary normalization works correctly."""
    event = UniversityEvent(
        uid="test-uid",
        summary="  Математика   (ЛК)  ",
        description="Test",
        location="Room",
        dtstart=datetime(2026, 9, 1, 10, 0),
        dtend=datetime(2026, 9, 1, 12, 0),
        event_type=EventType.LECTURE
    )

    assert event.summary_normalized == "Математика (ЛК)"


def test_university_event_from_parse_ics_dict():
    """Test creating UniversityEvent from parse_ics dictionary format."""
    # This simulates the data structure from parse_ics.py
    event_data = {
        'uid': 'ics-test-uid',
        'summary': 'Физика (ЛБ)',
        'description': 'Лабораторная работа по физике',
        'location': 'Физический кабинет',
        'dtstart': datetime(2026, 9, 1, 14, 0),
        'dtend': datetime(2026, 9, 1, 17, 0),
        'event_type': EventType.LAB,
        'is_group_event': True
    }

    event = UniversityEvent(**event_data)

    assert event.uid == 'ics-test-uid'
    assert event.summary == 'Физика (ЛБ)'
    assert event.event_type == EventType.LAB
    assert event.is_group_event == True
    assert event.description == 'Лабораторная работа по физике'
    assert event.location == 'Физический кабинет'
    assert event.duration_minutes == 180


def test_preparation_block_creation():
    """Test PreparationBlock creation and properties."""
    start_time = datetime(2026, 9, 1, 8, 0)
    end_time = datetime(2026, 9, 1, 10, 0)

    prep_block = PreparationBlock(
        uid="prep-test-uid",
        summary="Подготовка: Математика (ЛК)",
        description="Подготовка к лекции по математике\nТип: ЛК\nДлительность подготовки: 120 мин\nИсходное событие UID: event-123",
        dtstart=start_time,
        dtend=end_time,
        preparation_minutes=120,
        source_event_uid="event-123",
        event_type=EventType.LECTURE
    )

    assert prep_block.uid == "prep-test-uid"
    assert prep_block.summary == "Подготовка: Математика (ЛК)"
    assert "Подготовка к лекции по математике" in prep_block.description
    assert prep_block.dtstart == start_time
    assert prep_block.dtend == end_time
    assert prep_block.preparation_minutes == 120
    assert prep_block.source_event_uid == "event-123"
    assert prep_block.event_type == EventType.LECTURE
    assert prep_block.calendar_id == "University Schedule"
    assert prep_block.duration_minutes == 120
    assert prep_block.source_event_type == "ЛК"


def test_preparation_block_custom_calendar_id():
    """Test PreparationBlock with custom calendar ID."""
    prep_block = PreparationBlock(
        uid="prep-test",
        summary="Test Preparation",
        description="Test description",
        dtstart=datetime(2026, 9, 1, 8, 0),
        dtend=datetime(2026, 9, 1, 10, 0),
        preparation_minutes=60,
        source_event_uid="source-123",
        event_type=EventType.PRACTICAL
    )

    # Test default calendar ID
    assert prep_block.calendar_id == "University Schedule"

    # Test setting calendar ID in __post_init__ is automatic based on default
    # For custom ID, we need to test if it can be overridden
    # Since it's set in __post_init__, we can't override it directly in constructor
    # Let's test that it gets the default value


def test_calendar_sync_result():
    """Test CalendarSyncResult functionality."""
    result = CalendarSyncResult(success=True)

    assert result.success == True
    assert result.events_processed == 0
    assert result.events_created == 0
    assert result.events_updated == 0
    assert result.events_skipped == 0
    assert result.errors == []
    assert result.warnings == []
    assert result.has_errors == False
    assert result.has_warnings == False

    # Test adding errors and warnings
    result.add_error("Test error")
    assert result.has_errors == True
    assert result.success == False  # Adding error should set success to False
    assert len(result.errors) == 1
    assert result.errors[0] == "Test error"

    result.add_warning("Test warning")
    assert result.has_warnings == True
    assert len(result.warnings) == 1
    assert result.warnings[0] == "Test warning"

    # Test successful result with data
    success_result = CalendarSyncResult(
        success=True,
        events_processed=10,
        events_created=5,
        events_updated=3,
        events_skipped=2
    )

    assert success_result.events_processed == 10
    assert success_result.events_created == 5
    assert success_result.events_updated == 3
    assert success_result.events_skipped == 2


# ===== PROJECT MODEL TESTS =====

def test_project_creation():
    """Test Project creation and properties."""
    now = datetime.now()
    project = Project(
        id="proj-123",
        name="Website Redesign",
        description="Redesign company website",
        status=ProjectStatus.PLANNING,
        created_at=now,
        updated_at=now
    )

    assert project.id == "proj-123"
    assert project.name == "Website Redesign"
    assert project.description == "Redesign company website"
    assert project.status == ProjectStatus.PLANNING
    assert project.created_at == now
    assert project.updated_at == now
    assert project.tags == set()
    assert project.metadata == {}


def test_project_status_methods():
    """Test Project status helper methods."""
    project = Project(
        id="proj-123",
        name="Test Project",
        description="Test",
        status=ProjectStatus.ACTIVE,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    assert project.is_active == True
    assert project.is_completed == False

    project.update_status(ProjectStatus.COMPLETED)
    assert project.status == ProjectStatus.COMPLETED
    assert project.is_active == False
    assert project.is_completed == True


def test_project_tags():
    """Test Project tag functionality."""
    project = Project(
        id="proj-123",
        name="Test Project",
        description="Test",
        status=ProjectStatus.ACTIVE,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    # Test adding tags
    project.add_tag("important")
    project.add_tag("web")
    project.add_tag("  ImportAnt  ")  # Test normalization and deduplication

    assert project.has_tag("important")
    assert project.has_tag("web")
    assert len(project.tags) == 2  # "important" added only once

    # Test removing tags
    project.remove_tag("web")
    assert not project.has_tag("web")
    assert project.has_tag("important")

    # Test removing non-existent tag (should not error)
    project.remove_tag("nonexistent")
    assert project.has_tag("important")


def test_task_creation():
    """Test Task creation and properties."""
    now = datetime.now()
    due_date = now + timedelta(days=7)

    task = Task(
        id="task-123",
        title="Implement login feature",
        description="Create user authentication system",
        project_id="proj-123",
        status=TaskStatus.TODO,
        priority=TaskPriority.HIGH,
        created_at=now,
        updated_at=now,
        due_date=due_date,
        estimated_hours=8.0
    )

    assert task.id == "task-123"
    assert task.title == "Implement login feature"
    assert task.description == "Create user authentication system"
    assert task.project_id == "proj-123"
    assert task.status == TaskStatus.TODO
    assert task.priority == TaskPriority.HIGH
    assert task.created_at == now
    assert task.updated_at == now
    assert task.due_date == due_date
    assert task.estimated_hours == 8.0
    assert task.actual_hours is None
    assert task.assignee is None
    assert task.tags == set()
    assert task.dependencies == []
    assert task.metadata == {}


def test_task_status_methods():
    """Test Task status and priority helper methods."""
    task = Task(
        id="task-123",
        title="Test Task",
        description="Test",
        project_id=None,
        status=TaskStatus.IN_PROGRESS,
        priority=TaskPriority.URGENT,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    assert task.is_high_priority == True

    # Test overdue logic
    past_due = datetime.now() - timedelta(days=1)
    task_overdue = Task(
        id="task-overdue",
        title="Overdue Task",
        description="Test",
        project_id=None,
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        due_date=past_due
    )
    assert task_overdue.is_overdue == True

    # Completed tasks should not be overdue even if past due
    task_completed = Task(
        id="task-completed",
        title="Completed Task",
        description="Test",
        project_id=None,
        status=TaskStatus.DONE,
        priority=TaskPriority.MEDIUM,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        due_date=past_due
    )
    assert task_completed.is_overdue == False


def test_task_tags_and_dependencies():
    """Test Task tag and dependency functionality."""
    task = Task(
        id="task-123",
        title="Test Task",
        description="Test",
        project_id="proj-123",
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    # Test tags
    task.add_tag("frontend")
    task.add_tag("ui")
    task.add_tag("  FRONTEND  ")  # Test normalization

    assert task.has_tag("frontend")
    assert task.has_tag("ui")
    assert len(task.tags) == 2

    task.remove_tag("ui")
    assert not task.has_tag("ui")
    assert task.has_tag("frontend")

    # Test dependencies
    task.add_dependency("task-456")
    task.add_dependency("task-789")
    assert len(task.dependencies) == 2
    assert "task-456" in task.dependencies
    assert "task-789" in task.dependencies

    task.remove_dependency("task-456")
    assert len(task.dependencies) == 1
    assert "task-789" in task.dependencies
    assert "task-456" not in task.dependencies


# ===== KNOWLEDGE MODEL TESTS =====

def test_tag_creation():
    """Test Tag creation and properties."""
    now = datetime.now()
    tag = Tag(
        name="important",
        color="#FF0000",
        description="Important items",
        created_at=now
    )

    assert tag.name == "important"
    assert tag.color == "#FF0000"
    assert tag.description == "Important items"
    assert tag.created_at == now

    # Test normalization
    tag_unnorm = Tag(name="  IMPORTANT  ")
    assert tag_unnorm.name == "important"


def test_knowledge_item_creation():
    """Test KnowledgeItem creation and properties."""
    now = datetime.now()
    knowledge = KnowledgeItem(
        id="know-123",
        title="Understanding Quantum Computing",
        content="Quantum computing uses quantum bits or qubits...",
        note_type=NoteType.LEARNING,
        status=KnowledgeStatus.DRAFT,
        created_at=now,
        updated_at=now,
        author="Alice"
    )

    assert knowledge.id == "know-123"
    assert knowledge.title == "Understanding Quantum Computing"
    assert knowledge.content == "Quantum computing uses quantum bits or qubits..."
    assert knowledge.note_type == NoteType.LEARNING
    assert knowledge.status == KnowledgeStatus.DRAFT
    assert knowledge.created_at == now
    assert knowledge.updated_at == now
    assert knowledge.author == "Alice"
    assert knowledge.tags == set()
    assert knowledge.links == []
    assert knowledge.project_id == None
    assert knowledge.metadata == {}


def test_knowledge_item_word_count():
    """Test KnowledgeItem word count calculation."""
    knowledge = KnowledgeItem(
        id="know-123",
        title="Test",
        content="This is a test sentence with seven words.",
        note_type=NoteType.IDEA,
        status=KnowledgeStatus.DRAFT,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    assert knowledge.word_count == 8

    # Test with extra whitespace
    knowledge2 = KnowledgeItem(
        id="know-456",
        title="Test 2",
        content="  Multiple   spaces    and\tabs\nnewlines  ",
        note_type=NoteType.IDEA,
        status=KnowledgeStatus.DRAFT,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    assert knowledge2.word_count == 5  # Multiple, spaces, and, tabs, newlines


def test_knowledge_item_status_methods():
    """Test KnowledgeItem status helper methods."""
    knowledge = KnowledgeItem(
        id="know-123",
        title="Test Knowledge",
        content="Test content",
        note_type=NoteType.REFERENCE,
        status=KnowledgeStatus.DRAFT,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    assert knowledge.is_draft == True
    assert knowledge.is_published == False

    knowledge.update_status(KnowledgeStatus.PUBLISHED)
    assert knowledge.status == KnowledgeStatus.PUBLISHED
    assert knowledge.is_draft == False
    assert knowledge.is_published == True


def test_knowledge_item_tags_and_links():
    """Test KnowledgeItem tag and link functionality."""
    knowledge = KnowledgeItem(
        id="know-123",
        title="Test Knowledge",
        content="Test content",
        note_type=NoteType.IDEA,
        status=KnowledgeStatus.DRAFT,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    # Test tags
    knowledge.add_tag("quantum")
    knowledge.add_tag("physics")
    knowledge.add_tag("  QUANTUM  ")  # Test normalization

    assert knowledge.has_tag("quantum")
    assert knowledge.has_tag("physics")
    assert len(knowledge.tags) == 2

    knowledge.remove_tag("physics")
    assert not knowledge.has_tag("physics")
    assert knowledge.has_tag("quantum")

    # Test links
    knowledge.add_link("know-456")
    knowledge.add_link("know-789")
    assert len(knowledge.links) == 2
    assert "know-456" in knowledge.links
    assert "know-789" in knowledge.links

    knowledge.remove_link("know-456")
    assert len(knowledge.links) == 1
    assert "know-789" in knowledge.links
    assert "know-456" not in knowledge.links


def test_knowledge_base_creation():
    """Test KnowledgeBase creation and properties."""
    now = datetime.now()
    kb = KnowledgeBase(
        id="kb-123",
        name="Personal Knowledge Vault",
        description="My main knowledge repository",
        created_at=now,
        updated_at=now
    )

    assert kb.id == "kb-123"
    assert kb.name == "Personal Knowledge Vault"
    assert kb.description == "My main knowledge repository"
    assert kb.created_at == now
    assert kb.updated_at == now
    assert kb.items == []
    assert kb.tags == set()
    assert kb.metadata == {}


def test_knowledge_base_items_and_tags():
    """Test KnowledgeBase item and tag management."""
    kb = KnowledgeBase(
        id="kb-123",
        name="Test KB",
        description="Test",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    # Test adding items
    kb.add_item("know-123")
    kb.add_item("know-456")
    kb.add_item("know-123")  # Test deduplication

    assert kb.has_item("know-123")
    assert kb.has_item("know-456")
    assert len(kb.items) == 2

    # Test removing items
    kb.remove_item("know-123")
    assert not kb.has_item("know-123")
    assert kb.has_item("know-456")
    assert len(kb.items) == 1

    # Test removing non-existent item (should not error)
    kb.remove_item("know-999")
    assert kb.has_item("know-456")

    # Test tags
    kb.add_tag("important")
    kb.add_tag("reference")
    kb.add_tag("  IMPORTANT  ")  # Test normalization

    assert kb.has_tag("important")
    assert kb.has_tag("reference")
    assert len(kb.tags) == 2

    kb.remove_tag("reference")
    assert not kb.has_tag("reference")
    assert kb.has_tag("important")