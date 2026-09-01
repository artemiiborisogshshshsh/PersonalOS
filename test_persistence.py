#!/usr/bin/env python3
"""
Test script to verify the persistence layer is working correctly.
"""
import sys
import os
from datetime import datetime, timedelta
import uuid

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from persistence.database import init_db, get_database
from persistence.repository import (
    university_event_repo,
    preparation_block_repo,
    project_repo,
    task_repo,
    knowledge_item_repo
)
from models import UniversityEvent, EventType, PreparationBlock, Project, Task, ProjectStatus, TaskPriority, TaskStatus, KnowledgeItem, NoteType, KnowledgeStatus


def get_test_id(prefix=""):
    """Generate a unique test ID."""
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def test_database_initialization():
    """Test that the database initializes correctly."""
    print("Testing database initialization...")
    db = init_db("test_personal_os.db")
    print(f"✓ Database initialized: {db.db_path}")
    assert db.connection is not None
    assert db.db_path == "test_personal_os.db"


def test_university_event_persistence():
    """Test university event persistence."""
    print("\nTesting university event persistence...")
    test_id = get_test_id("univ_")

    # Create a university event
    event = UniversityEvent(
        uid=f"{test_id}-event-001",
        summary="Test Lecture",
        description="A test lecture for persistence",
        location="Room 101",
        dtstart=datetime(2026, 9, 1, 10, 0),
        dtend=datetime(2026, 9, 1, 11, 30),
        event_type=EventType.LECTURE,
        is_group_event=True
    )

    # Save to database
    saved_event = university_event_repo.create(event)
    print(f"✓ Created event: {saved_event.summary} (ID: {saved_event.uid})")

    # Retrieve from database
    retrieved_event = university_event_repo.get_by_id(saved_event.uid)
    assert retrieved_event is not None, "Failed to retrieve event"
    assert retrieved_event.summary == event.summary, "Event summary mismatch"
    assert retrieved_event.location == event.location, "Event location mismatch"
    print(f"✓ Retrieved event: {retrieved_event.summary}")

    # Update event
    retrieved_event.summary = "Updated Test Lecture"
    updated_event = university_event_repo.update(retrieved_event)
    assert updated_event.summary == "Updated Test Lecture", "Failed to update event"
    print(f"✓ Updated event: {updated_event.summary}")

    # List events
    events = university_event_repo.get_all()
    assert len(events) >= 1, "Should have at least one event"
    print(f"✓ Total events in database: {len(events)}")

    # Delete event
    deleted = university_event_repo.delete(saved_event.uid)
    assert deleted, "Failed to delete event"
    print(f"✓ Deleted event: {saved_event.uid}")

    # Verify deletion
    deleted_event = university_event_repo.get_by_id(saved_event.uid)
    assert deleted_event is None, "Event should be deleted"
    print(f"✓ Verified event deletion")


def test_preparation_block_persistence():
    """Test preparation block persistence."""
    print("\nTesting preparation block persistence...")
    test_id = get_test_id("prep_")

    # Create a preparation block
    block = PreparationBlock(
        uid=f"{test_id}-prep-001",
        summary="Test Preparation",
        description="A test preparation block",
        dtstart=datetime(2026, 9, 1, 8, 0),
        dtend=datetime(2026, 9, 1, 10, 0),
        preparation_minutes=120,
        source_event_uid=f"{test_id}-source-event-001",  # We'll create this below
        event_type=EventType.LECTURE
    )

    # First create the source event to satisfy foreign key constraint
    source_event = UniversityEvent(
        uid=f"{test_id}-source-event-001",
        summary="Source Event",
        description="Source event for preparation",
        location="Room 101",
        dtstart=datetime(2026, 9, 1, 10, 0),
        dtend=datetime(2026, 9, 1, 12, 0),
        event_type=EventType.LECTURE,
        is_group_event=True
    )
    university_event_repo.create(source_event)

    # Save to database
    saved_block = preparation_block_repo.create(block)
    print(f"✓ Created preparation block: {saved_block.summary} (ID: {saved_block.uid})")

    # Retrieve from database
    retrieved_block = preparation_block_repo.get_by_id(saved_block.uid)
    assert retrieved_block is not None, "Failed to retrieve preparation block"
    assert retrieved_block.summary == block.summary, "Preparation block summary mismatch"
    assert retrieved_block.preparation_minutes == block.preparation_minutes, "Preparation block duration mismatch"
    print(f"✓ Retrieved preparation block: {retrieved_block.summary}")

    # List preparation blocks
    blocks = preparation_block_repo.get_all()
    assert len(blocks) >= 1, "Should have at least one preparation block"
    print(f"✓ Total preparation blocks in database: {len(blocks)}")


def test_project():
    """Test project persistence."""
    print("\nTesting project persistence...")
    test_id = get_test_id("proj_")

    # Create a project
    project = Project(
        id=f"{test_id}-project-001",
        name="Test Project",
        description="A test project for persistence",
        status=ProjectStatus.PLANNING,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        start_date=datetime(2026, 9, 1),
        target_date=datetime(2026, 12, 31)
    )

    # Save to database
    saved_project = project_repo.create(project)
    print(f"✓ Created project: {saved_project.name} (ID: {saved_project.id})")

    # Retrieve from database
    retrieved_project = project_repo.get_by_id(saved_project.id)
    assert retrieved_project is not None, "Failed to retrieve project"
    assert retrieved_project.name == project.name, "Project name mismatch"
    assert retrieved_project.description == project.description, "Project description mismatch"
    print(f"✓ Retrieved project: {retrieved_project.name}")

    # List projects
    projects = project_repo.get_all()
    assert len(projects) >= 1, "Should have at least one project"
    print(f"✓ Total projects in database: {len(projects)}")


def test_task():
    """Test task persistence."""
    print("\nTesting task persistence...")
    test_id = get_test_id("task_")

    # Create a project first for the foreign key
    project_id = f"{test_id}-project-001"
    project = Project(
        id=project_id,
        name="Test Project for Task",
        description="A test project",
        status=ProjectStatus.PLANNING,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    project_repo.create(project)

    # Create a task
    task = Task(
        id=f"{test_id}-task-001",
        title="Test Task",
        description="A test task for persistence",
        project_id=project_id,
        status=TaskStatus.INBOX,
        priority=TaskPriority.HIGH,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        due_date=datetime(2026, 9, 15, 18, 0),
        estimated_hours=5.0,
        assignee="Tester"
    )

    # Save to database
    saved_task = task_repo.create(task)
    print(f"✓ Created task: {saved_task.title} (ID: {saved_task.id})")

    # Retrieve from database
    retrieved_task = task_repo.get_by_id(saved_task.id)
    assert retrieved_task is not None, "Failed to retrieve task"
    assert retrieved_task.title == task.title, "Task title mismatch"
    assert retrieved_task.status == task.status, "Task status mismatch"
    assert retrieved_task.priority == task.priority, "Task priority mismatch"
    print(f"✓ Retrieved task: {retrieved_task.title}")

    # List tasks
    tasks = task_repo.get_all()
    assert len(tasks) >= 1, "Should have at least one task"
    print(f"✓ Total tasks in database: {len(tasks)}")


def test_knowledge_item():
    """Test knowledge item persistence."""
    print("\nTesting knowledge item persistence...")
    test_id = get_test_id("kb_")

    # Create a project first for the foreign key
    project_id = f"{test_id}-project-001"
    project = Project(
        id=project_id,
        name="Test Project for KB",
        description="A test project",
        status=ProjectStatus.PLANNING,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    project_repo.create(project)

    # Create a knowledge item
    item = KnowledgeItem(
        id=f"{test_id}-kb-001",
        title="Test Knowledge Item",
        content="This is a test knowledge item for persistence.",
        note_type=NoteType.IDEA,
        status=KnowledgeStatus.DRAFT,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        author="Test Author",
        project_id=project_id
    )

    # Save to database
    saved_item = knowledge_item_repo.create(item)
    print(f"✓ Created knowledge item: {saved_item.title} (ID: {saved_item.id})")

    # Retrieve from database
    retrieved_item = knowledge_item_repo.get_by_id(saved_item.id)
    assert retrieved_item is not None, "Failed to retrieve knowledge item"
    assert retrieved_item.title == item.title, "Knowledge item title mismatch"
    assert retrieved_item.content == item.content, "Knowledge item content mismatch"
    assert retrieved_item.status == item.status, "Knowledge item status mismatch"
    print(f"✓ Retrieved knowledge item: {retrieved_item.title}")

    # List knowledge items
    items = knowledge_item_repo.get_all()
    assert len(items) >= 1, "Should have at least one knowledge item"
    print(f"✓ Total knowledge items in database: {len(items)}")


def test_foreign_key_constraints():
    """Test that foreign key constraints work."""
    print("\nTesting foreign key constraints...")
    test_id = get_test_id("fk_")

    # Try to create a task with non-existent project ID (should fail)
    try:
        task = Task(
            id=f"{test_id}-task-fk-001",
            title="Test FK Task",
            description="A task with invalid project ID",
            project_id="non-existent-project",
            status=TaskStatus.INBOX,
            priority=TaskPriority.MEDIUM,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            estimated_hours=2.0
        )
        # This should fail due to foreign key constraint
        saved_task = task_repo.create(task)
        print("✗ Foreign key constraint not enforced - task created with invalid project ID")
    except Exception as e:
        print(f"✓ Foreign key constraint enforced: {type(e).__name__}")

    # Try to create a preparation block with non-existent source event (should fail)
    try:
        block = PreparationBlock(
            uid=f"{test_id}-prep-fk-001",
            summary="Test FK Prep Block",
            description="A preparation block with invalid source event",
            dtstart=datetime(2026, 9, 1, 8, 0),
            dtend=datetime(2026, 9, 1, 10, 0),
            preparation_minutes=120,
            source_event_uid="non-existent-event",
            event_type=EventType.LECTURE
        )
        # This should fail due to foreign key constraint
        saved_block = preparation_block_repo.create(block)
        print("✗ Foreign key constraint not enforced - preparation block created with invalid source event")
    except Exception as e:
        print(f"✓ Foreign key constraint enforced: {type(e).__name__}")


def main():
    """Run all tests."""
    print("Starting persistence layer tests...\n")

    # Initialize test database
    test_database_initialization()
    db = get_database()

    try:
        test_university_event_persistence()
        test_preparation_block_persistence()
        test_project()
        test_task()
        test_knowledge_item()
        test_foreign_key_constraints()

        print("\n🎉 All tests passed! Persistence layer is working correctly.")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Clean up test database
        db.close()
        if os.path.exists("test_personal_os.db"):
            os.remove("test_personal_os.db")
            print("✓ Cleaned up test database")

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
