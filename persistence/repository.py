"""
Base repository pattern implementation for Personal OS AI Calendar.
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Generic, TypeVar
from datetime import datetime
import json

from .database import get_database


T = TypeVar('T')


class BaseRepository(ABC, Generic[T]):
    """Base repository providing common CRUD operations."""

    def __init__(self, table_name: str, primary_key: str = "id"):
        self.table_name = table_name
        self.primary_key = primary_key
        self.db = get_database()

    @abstractmethod
    def _to_dict(self, entity: T) -> Dict[str, Any]:
        """Convert entity to dictionary for database storage."""
        pass

    @abstractmethod
    def _from_dict(self, row: Dict[str, Any]) -> T:
        """Create entity from database row."""
        pass

    def create(self, entity: T) -> T:
        """Create a new entity."""
        data = self._to_dict(entity)
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        values = list(data.values())

        with self.db.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})",
                values
            )
            # Get the ID if it's auto-generated
            # For entities with string IDs, they should already be set

        # Log the change
        self._log_change(entity, None, data, 'INSERT')
        return entity

    def get_by_id(self, id: str) -> Optional[T]:
        """Get entity by ID."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {self.table_name} WHERE {self.primary_key} = ?",
                (id,)
            )
            row = cursor.fetchone()
            if row:
                return self._from_dict(dict(row))
            return None

    def get_all(self, limit: Optional[int] = None, offset: int = 0) -> List[T]:
        """Get all entities with optional pagination."""
        query = f"SELECT * FROM {self.table_name}"
        params = []

        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        with self.db.get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._from_dict(dict(row)) for row in rows]

    def update(self, entity: T) -> T:
        """Update an existing entity."""
        # Get old values for audit log
        entity_id = getattr(entity, self.primary_key, '')
        old_entity = self.get_by_id(entity_id)
        old_data = self._to_dict(old_entity) if old_entity else {}

        data = self._to_dict(entity)
        # Remove primary key from update data as it's used in WHERE clause
        update_data = {k: v for k, v in data.items() if k != self.primary_key}

        if not update_data:
            return entity

        set_clause = ', '.join([f"{k} = ?" for k in update_data.keys()])
        values = list(update_data.values())

        with self.db.get_cursor() as cursor:
            cursor.execute(
                f"UPDATE {self.table_name} SET {set_clause} WHERE {self.primary_key} = ?",
                values + [entity_id]
            )

        # Log the change
        self._log_change(entity, old_data, data, 'UPDATE')
        return entity

    def delete(self, id: str) -> bool:
        """Delete entity by ID."""
        # Get old values for audit log
        old_entity = self.get_by_id(id)
        old_data = self._to_dict(old_entity) if old_entity else {}

        with self.db.get_cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {self.table_name} WHERE {self.primary_key} = ?",
                (id,)
            )
            deleted = cursor.rowcount > 0

        if deleted and old_entity:
            # Log the change
            self._log_change(old_entity, old_data, None, 'DELETE')

        return deleted

    def find_by(self, **kwargs) -> List[T]:
        """Find entities by field values."""
        if not kwargs:
            return self.get_all()

        where_clause = ' AND '.join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values())

        with self.db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {self.table_name} WHERE {where_clause}",
                values
            )
            rows = cursor.fetchall()
            return [self._from_dict(dict(row)) for row in rows]

    def count(self) -> int:
        """Count total entities."""
        with self.db.get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {self.table_name}")
            result = cursor.fetchone()
            return result[0] if result else 0

    def _log_change(self, entity: Optional[T], old_values: Optional[Dict[str, Any]],
                   new_values: Optional[Dict[str, Any]], operation: str):
        """Log change to history table."""
        if not old_values and not new_values:
            return

        entity_id = getattr(entity, self.primary_key, 'unknown') if entity else 'unknown'

        # Determine table name from entity type if not set
        table_name = self.table_name

        old_json = json.dumps(old_values, default=str) if old_values else None
        new_json = json.dumps(new_values, default=str) if new_values else None

        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO change_history
                (table_name, record_id, operation, old_values, new_values)
                VALUES (?, ?, ?, ?, ?)
            """, (table_name, entity_id, operation, old_json, new_json))


class UniversityEventRepository(BaseRepository):
    """Repository for UniversityEvent entities."""

    def __init__(self):
        super().__init__("university_events", "uid")

    def _to_dict(self, entity) -> Dict[str, Any]:
        from models import UniversityEvent, EventType
        return {
            'uid': entity.uid,
            'summary': entity.summary,
            'description': entity.description,
            'location': entity.location,
            'dtstart': entity.dtstart,
            'dtend': entity.dtend,
            'event_type': entity.event_type.value if entity.event_type else None,
            'is_group_event': 1 if entity.is_group_event else 0,
            'summary_normalized': entity.summary_normalized,
            'status': entity.status.value,
            'sequence': entity.sequence
        }

    def _from_dict(self, row: Dict[str, Any]) -> 'UniversityEvent':
        from models import UniversityEvent, EventType, UniversityEventStatus
        # summary_normalized is computed in __post_init__, so don't pass it to constructor
        event = UniversityEvent(
            uid=row['uid'],
            summary=row['summary'],
            description=row['description'],
            location=row['location'],
            dtstart=row['dtstart'],
            dtend=row['dtend'],
            event_type=EventType(row['event_type']) if row['event_type'] else EventType.UNKNOWN,
            is_group_event=bool(row['is_group_event']),
            status=UniversityEventStatus.from_string(row.get('status')),
            sequence=int(row.get('sequence') or 0)
        )
        return event


class PreparationBlockRepository(BaseRepository):
    """Repository for PreparationBlock entities."""

    def __init__(self):
        super().__init__("preparation_blocks", "uid")

    def _to_dict(self, entity) -> Dict[str, Any]:
        from models import PreparationBlock, EventType
        return {
            'uid': entity.uid,
            'summary': entity.summary,
            'description': entity.description,
            'dtstart': entity.dtstart,
            'dtend': entity.dtend,
            'preparation_minutes': entity.preparation_minutes,
            'source_event_uid': entity.source_event_uid,
            'event_type': entity.event_type.value if entity.event_type else None,
            'calendar_id': entity.calendar_id
        }

    def _from_dict(self, row: Dict[str, Any]) -> 'PreparationBlock':
        from models import PreparationBlock, EventType
        return PreparationBlock(
            uid=row['uid'],
            summary=row['summary'],
            description=row['description'],
            dtstart=row['dtstart'],
            dtend=row['dtend'],
            preparation_minutes=row['preparation_minutes'],
            source_event_uid=row['source_event_uid'],
            event_type=EventType(row['event_type']) if row['event_type'] else None
        )


class ProjectRepository(BaseRepository):
    """Repository for Project entities."""

    def __init__(self):
        super().__init__("projects", "id")

    def _to_dict(self, entity) -> Dict[str, Any]:
        from models import Project, ProjectStatus
        return {
            'id': entity.id,
            'name': entity.name,
            'description': entity.description,
            'status': entity.status.value if hasattr(entity.status, 'value') else str(entity.status),
            'created_at': entity.created_at,
            'updated_at': entity.updated_at,
            'start_date': entity.start_date,
            'target_date': entity.target_date
        }

    def _from_dict(self, row: Dict[str, Any]) -> 'Project':
        from models import Project, ProjectStatus
        return Project(
            id=row['id'],
            name=row['name'],
            description=row['description'],
            status=ProjectStatus(row['status']) if row['status'] else ProjectStatus.PLANNING,
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            start_date=row['start_date'],
            target_date=row['target_date']
        )


class TaskRepository(BaseRepository):
    """Repository for Task entities."""

    def __init__(self):
        super().__init__("tasks", "id")

    def _to_dict(self, entity) -> Dict[str, Any]:
        from models import Task, TaskStatus, TaskPriority
        return {
            'id': entity.id,
            'title': entity.title,
            'description': entity.description,
            'project_id': entity.project_id,
            'status': entity.status.value if hasattr(entity.status, 'value') else str(entity.status),
            'priority': entity.priority.value if hasattr(entity.priority, 'value') else str(entity.priority),
            'created_at': entity.created_at,
            'updated_at': entity.updated_at,
            'due_date': entity.due_date,
            'estimated_hours': entity.estimated_hours,
            'actual_hours': entity.actual_hours,
            'assignee': entity.assignee
        }

    def _from_dict(self, row: Dict[str, Any]) -> 'Task':
        from models import Task, TaskStatus, TaskPriority
        return Task(
            id=row['id'],
            title=row['title'],
            description=row['description'],
            project_id=row['project_id'],
            status=TaskStatus(row['status']) if row['status'] else TaskStatus.INBOX,
            priority=TaskPriority(row['priority']) if row['priority'] else TaskPriority.MEDIUM,
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            due_date=row['due_date'],
            estimated_hours=row['estimated_hours'],
            actual_hours=row['actual_hours'],
            assignee=row['assignee']
        )


class KnowledgeItemRepository(BaseRepository):
    """Repository for KnowledgeItem entities."""

    def __init__(self):
        super().__init__("knowledge_items", "id")

    def _to_dict(self, entity) -> Dict[str, Any]:
        from models import KnowledgeItem, NoteType, KnowledgeStatus
        return {
            'id': entity.id,
            'title': entity.title,
            'content': entity.content,
            'note_type': entity.note_type.value if hasattr(entity.note_type, 'value') else str(entity.note_type),
            'status': entity.status.value if hasattr(entity.status, 'value') else str(entity.status),
            'created_at': entity.created_at,
            'updated_at': entity.updated_at,
            'author': entity.author,
            'project_id': entity.project_id
        }

    def _from_dict(self, row: Dict[str, Any]) -> 'KnowledgeItem':
        from models import KnowledgeItem, NoteType, KnowledgeStatus
        return KnowledgeItem(
            id=row['id'],
            title=row['title'],
            content=row['content'],
            note_type=NoteType(row['note_type']) if row['note_type'] else NoteType.IDEA,
            status=KnowledgeStatus(row['status']) if row['status'] else KnowledgeStatus.DRAFT,
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            author=row['author'],
            project_id=row['project_id']
        )


class TagRepository(BaseRepository):
    """Repository for Tag entities."""

    def __init__(self):
        super().__init__("tags", "id")

    def _to_dict(self, entity) -> Dict[str, Any]:
        return {
            'id': entity.id,
            'name': entity.name,
            'color': entity.color,
            'description': entity.description,
            'created_at': entity.created_at
        }

    def _from_dict(self, row: Dict[str, Any]) -> 'Tag':
        from models import Tag
        return Tag(
            id=row['id'],
            name=row['name'],
            color=row['color'],
            description=row['description'],
            created_at=row['created_at']
        )


class KnowledgeBaseRepository(BaseRepository):
    """Repository for KnowledgeBase entities."""

    def __init__(self):
        super().__init__("knowledge_bases", "id")

    def _to_dict(self, entity) -> Dict[str, Any]:
        return {
            'id': entity.id,
            'name': entity.name,
            'description': entity.description,
            'created_at': entity.created_at,
            'updated_at': entity.updated_at
        }

    def _from_dict(self, row: Dict[str, Any]) -> 'KnowledgeBase':
        from models import KnowledgeBase
        return KnowledgeBase(
            id=row['id'],
            name=row['name'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )


# Repository instances
university_event_repo = UniversityEventRepository()
preparation_block_repo = PreparationBlockRepository()
project_repo = ProjectRepository()
task_repo = TaskRepository()
knowledge_item_repo = KnowledgeItemRepository()
tag_repo = TagRepository()
knowledge_base_repo = KnowledgeBaseRepository()
