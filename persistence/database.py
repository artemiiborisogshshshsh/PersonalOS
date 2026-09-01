"""
SQLite database connection and schema management for Personal OS AI Calendar.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, Any, Dict, List
from contextlib import contextmanager


# Python 3.12 deprecated sqlite3's implicit datetime adapter/converter.
# Register explicit ISO-8601 handlers so timezone offsets round-trip and the
# persistence suite remains warning-free on supported Python versions.
sqlite3.register_adapter(datetime, lambda value: value.isoformat(' '))
sqlite3.register_converter(
    'timestamp', lambda raw: datetime.fromisoformat(raw.decode('utf-8'))
)


class Database:
    """Manages SQLite database connection and schema."""

    def __init__(self, db_path: str = "personal_os.db"):
        self.db_path = db_path
        self.connection: Optional[sqlite3.Connection] = None
        self._initialize_database()

    def _initialize_database(self):
        """Initialize database connection and create tables if needed."""
        self.connection = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
        self.connection.row_factory = sqlite3.Row
        self._enable_foreign_keys()
        self._create_schema_version_table()
        self._migrate_to_latest()

    def _enable_foreign_keys(self):
        """Enable foreign key constraints."""
        self.connection.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def get_cursor(self):
        """Context manager for database cursor."""
        cursor = self.connection.cursor()
        try:
            yield cursor
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    def _create_schema_version_table(self):
        """Create schema version tracking table."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
            """)

    def _get_current_version(self) -> int:
        """Get current schema version."""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT MAX(version) FROM schema_version")
            result = cursor.fetchone()
            return result[0] if result[0] is not None else 0

    def _migrate_to_latest(self):
        """Apply migrations to bring schema to latest version."""
        current_version = self._get_current_version()
        target_version = len(self._get_migrations())

        for version in range(current_version + 1, target_version + 1):
            migration = self._get_migrations()[version - 1]
            print(f"Applying migration {version}: {migration['description']}")
            with self.get_cursor() as cursor:
                for statement in migration['sql']:
                    cursor.execute(statement)
                cursor.execute(
                    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                    (version, migration['description'])
                )

    def _get_migrations(self) -> List[Dict[str, Any]]:
        """Define schema migrations."""
        return [
            {
                'version': 1,
                'description': 'Initial schema with core tables',
                'sql': [
                    """
                    CREATE TABLE IF NOT EXISTS university_events (
                        uid TEXT PRIMARY KEY,
                        summary TEXT NOT NULL,
                        description TEXT,
                        location TEXT,
                        dtstart TIMESTAMP NOT NULL,
                        dtend TIMESTAMP NOT NULL,
                        event_type TEXT,
                        is_group_event BOOLEAN DEFAULT 0,
                        summary_normalized TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS preparation_blocks (
                        uid TEXT PRIMARY KEY,
                        summary TEXT NOT NULL,
                        description TEXT,
                        dtstart TIMESTAMP NOT NULL,
                        dtend TIMESTAMP NOT NULL,
                        preparation_minutes INTEGER NOT NULL,
                        source_event_uid TEXT NOT NULL,
                        event_type TEXT,
                        calendar_id TEXT DEFAULT 'University Schedule',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (source_event_uid) REFERENCES university_events(uid)
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        status TEXT DEFAULT 'planning',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        start_date TIMESTAMP,
                        target_date TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT,
                        project_id TEXT,
                        status TEXT DEFAULT 'todo',
                        priority TEXT DEFAULT 'medium',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        due_date TIMESTAMP,
                        estimated_hours REAL,
                        actual_hours REAL,
                        assignee TEXT,
                        FOREIGN KEY (project_id) REFERENCES projects(id)
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS task_dependencies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL,
                        depends_on_task_id TEXT NOT NULL,
                        FOREIGN KEY (task_id) REFERENCES tasks(id),
                        FOREIGN KEY (depends_on_task_id) REFERENCES tasks(id),
                        UNIQUE(task_id, depends_on_task_id)
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_items (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        content TEXT,
                        note_type TEXT DEFAULT 'idea',
                        status TEXT DEFAULT 'draft',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        author TEXT,
                        project_id TEXT,
                        FOREIGN KEY (project_id) REFERENCES projects(id)
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS tags (
                        id TEXT PRIMARY KEY,
                        name TEXT UNIQUE NOT NULL,
                        color TEXT,
                        description TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_item_tags (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        knowledge_item_id TEXT NOT NULL,
                        tag_id TEXT NOT NULL,
                        FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id),
                        FOREIGN KEY (tag_id) REFERENCES tags(id),
                        UNIQUE(knowledge_item_id, tag_id)
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_bases (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_base_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        knowledge_base_id TEXT NOT NULL,
                        knowledge_item_id TEXT NOT NULL,
                        FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_bases(id),
                        FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id),
                        UNIQUE(knowledge_base_id, knowledge_item_id)
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS schedule_blocks (
                        id TEXT PRIMARY KEY,
                        item_type TEXT NOT NULL,
                        item_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT,
                        start_time TIMESTAMP NOT NULL,
                        end_time TIMESTAMP NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS execution_records (
                        id TEXT PRIMARY KEY,
                        item_type TEXT NOT NULL,
                        item_id TEXT NOT NULL,
                        planned_start TIMESTAMP,
                        planned_end TIMESTAMP,
                        actual_start TIMESTAMP,
                        actual_end TIMESTAMP,
                        status TEXT DEFAULT 'completed',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ]
            },
            {
                'version': 2,
                'description': 'Add audit/change history tables',
                'sql': [
                    """
                    CREATE TABLE IF NOT EXISTS change_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        table_name TEXT NOT NULL,
                        record_id TEXT NOT NULL,
                        operation TEXT NOT NULL,  -- INSERT, UPDATE, DELETE
                        old_values TEXT,  -- JSON
                        new_values TEXT,  -- JSON
                        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        changed_by TEXT DEFAULT 'system'
                    )
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_change_history_table_record
                    ON change_history(table_name, record_id)
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_change_history_changed_at
                    ON change_history(changed_at)
                    """
                ]
            },
            {
                'version': 3,
                'description': 'Add indexes for performance',
                'sql': [
                    """
                    CREATE INDEX IF NOT EXISTS idx_university_events_dtstart
                    ON university_events(dtstart)
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_preparation_blocks_source
                    ON preparation_blocks(source_event_uid)
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_tasks_project
                    ON tasks(project_id)
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_tasks_status
                    ON tasks(status)
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_knowledge_items_type
                    ON knowledge_items(note_type)
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_schedule_blocks_time
                    ON schedule_blocks(start_time, end_time)
                    """
                ]
            },
            {
                'version': 4,
                'description': 'Add source status and sequence to university events',
                'sql': [
                    "ALTER TABLE university_events ADD COLUMN status TEXT DEFAULT 'confirmed'",
                    "ALTER TABLE university_events ADD COLUMN sequence INTEGER DEFAULT 0"
                ]
            }
        ]

    def close(self):
        """Close database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None


# Global database instance
_db_instance: Optional[Database] = None


def get_database() -> Database:
    """Get or create global database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance


def init_db(db_path: str = "personal_os.db"):
    """Initialize database with custom path."""
    global _db_instance
    _db_instance = Database(db_path)
    return _db_instance
