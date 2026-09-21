"""Durable, per-user confirmed project and task state."""

from __future__ import annotations

from datetime import datetime
from graphlib import CycleError, TopologicalSorter
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from models import Project, ProjectStatus, Task, TaskPriority, TaskStatus


class UserProjectStore:
    """Persist the operational subset of confirmed projects and tasks for one user."""

    VERSION = 1
    _PROJECT_FIELDS = frozenset({
        "id", "name", "description", "status", "created_at", "updated_at",
        "start_date", "target_date",
    })
    _TASK_FIELDS = frozenset({
        "id", "title", "description", "project_id", "status", "priority",
        "created_at", "updated_at", "due_date", "estimated_hours", "dependencies",
    })

    def __init__(self, state_directory: Path):
        self.path = Path(state_directory) / "project_tasks.json"

    def load(self) -> tuple[list[Project], list[Task]]:
        """Load and validate the complete local state without creating directories."""
        if self.path.is_symlink():
            raise ValueError("Invalid project task state")
        if not self.path.exists():
            return [], []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict)
                    or set(payload) != {"version", "projects", "tasks"}
                    or type(payload["version"]) is not int
                    or payload["version"] != self.VERSION
                    or not isinstance(payload["projects"], list)
                    or not isinstance(payload["tasks"], list)):
                raise ValueError
            projects = [self._project_from_record(record) for record in payload["projects"]]
            tasks = [self._task_from_record(record) for record in payload["tasks"]]
            self._validate_state(projects, tasks)
            return sorted(projects, key=lambda project: project.id), sorted(tasks, key=lambda task: task.id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("Invalid project task state") from None

    def upsert_project(self, project: Project, *, confirmed: bool = False) -> None:
        """Store a confirmed project after validating the complete prospective state."""
        self._require_confirmation(confirmed)
        project = self._project_from_record(self._project_record(project))
        projects, tasks = self.load()
        by_id = {item.id: item for item in projects}
        by_id[project.id] = project
        prospective_projects = sorted(by_id.values(), key=lambda item: item.id)
        self._validate_state(prospective_projects, tasks)
        self._write(prospective_projects, tasks)

    def upsert_task(self, task: Task, *, confirmed: bool = False) -> None:
        """Store a confirmed task after validating the complete prospective state."""
        self._require_confirmation(confirmed)
        task = self._task_from_record(self._task_record(task))
        projects, tasks = self.load()
        by_id = {item.id: item for item in tasks}
        by_id[task.id] = task
        prospective_tasks = sorted(by_id.values(), key=lambda item: item.id)
        self._validate_state(projects, prospective_tasks)
        self._write(projects, prospective_tasks)

    @staticmethod
    def _require_confirmation(confirmed: bool) -> None:
        if confirmed is not True:
            raise PermissionError("Project or task must be confirmed")

    @classmethod
    def _project_from_record(cls, record: object) -> Project:
        if not isinstance(record, dict) or set(record) != cls._PROJECT_FIELDS:
            raise ValueError
        identifier = cls._required_text(record["id"])
        name = cls._required_text(record["name"])
        description = record["description"]
        if not isinstance(description, str):
            raise ValueError
        return Project(
            id=identifier,
            name=name,
            description=description,
            status=cls._project_status(record["status"]),
            created_at=cls._required_datetime(record["created_at"]),
            updated_at=cls._required_datetime(record["updated_at"]),
            start_date=cls._optional_datetime(record["start_date"]),
            target_date=cls._optional_datetime(record["target_date"]),
        )

    @classmethod
    def _task_from_record(cls, record: object) -> Task:
        if not isinstance(record, dict) or set(record) != cls._TASK_FIELDS:
            raise ValueError
        identifier = cls._required_text(record["id"])
        title = cls._required_text(record["title"])
        description = record["description"]
        project_id = record["project_id"]
        if (not isinstance(description, str)
                or (project_id is not None and (not isinstance(project_id, str) or not project_id.strip()))):
            raise ValueError
        dependencies = record["dependencies"]
        if (not isinstance(dependencies, list)
                or any(not isinstance(item, str) or not item.strip() for item in dependencies)
                or len(set(dependencies)) != len(dependencies)
                or identifier in dependencies):
            raise ValueError
        status = cls._task_status(record["status"])
        estimated_hours = cls._estimated_hours(record["estimated_hours"], status)
        return Task(
            id=identifier,
            title=title,
            description=description,
            project_id=project_id,
            status=status,
            priority=cls._task_priority(record["priority"]),
            created_at=cls._required_datetime(record["created_at"]),
            updated_at=cls._required_datetime(record["updated_at"]),
            due_date=cls._optional_datetime(record["due_date"]),
            estimated_hours=estimated_hours,
            dependencies=list(dependencies),
        )

    @staticmethod
    def _required_text(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError
        return value

    @staticmethod
    def _required_datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError
        return UserProjectStore._aware_datetime(value)

    @staticmethod
    def _optional_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError
        return UserProjectStore._aware_datetime(value)

    @staticmethod
    def _aware_datetime(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed

    @staticmethod
    def _project_status(value: object) -> ProjectStatus:
        if not isinstance(value, str):
            raise ValueError
        try:
            return ProjectStatus(value)
        except ValueError:
            raise ValueError from None

    @staticmethod
    def _task_status(value: object) -> TaskStatus:
        if not isinstance(value, str):
            raise ValueError
        try:
            return TaskStatus(value)
        except ValueError:
            raise ValueError from None

    @staticmethod
    def _task_priority(value: object) -> TaskPriority:
        if not isinstance(value, str):
            raise ValueError
        try:
            return TaskPriority(value)
        except ValueError:
            raise ValueError from None

    @staticmethod
    def _estimated_hours(value: object, status: TaskStatus) -> float | int | None:
        if value is None:
            if status in {TaskStatus.TODO, TaskStatus.IN_PROGRESS}:
                raise ValueError
            return None
        if type(value) not in {int, float} or value <= 0:
            raise ValueError
        try:
            hours = float(value)
            minutes = hours * 60
        except OverflowError:
            raise ValueError from None
        if not math.isfinite(hours) or not math.isfinite(minutes) or not minutes.is_integer():
            raise ValueError
        return value

    @classmethod
    def _project_record(cls, project: object) -> dict:
        if not isinstance(project, Project):
            raise ValueError("Invalid project task state")
        record = {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "status": project.status.value if isinstance(project.status, ProjectStatus) else None,
            "created_at": cls._datetime_text(project.created_at),
            "updated_at": cls._datetime_text(project.updated_at),
            "start_date": cls._datetime_text(project.start_date),
            "target_date": cls._datetime_text(project.target_date),
        }
        cls._project_from_record(record)
        return record

    @classmethod
    def _task_record(cls, task: object) -> dict:
        if not isinstance(task, Task):
            raise ValueError("Invalid project task state")
        record = {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "project_id": task.project_id,
            "status": task.status.value if isinstance(task.status, TaskStatus) else None,
            "priority": task.priority.value if isinstance(task.priority, TaskPriority) else None,
            "created_at": cls._datetime_text(task.created_at),
            "updated_at": cls._datetime_text(task.updated_at),
            "due_date": cls._datetime_text(task.due_date),
            "estimated_hours": task.estimated_hours,
            "dependencies": task.dependencies,
        }
        cls._task_from_record(record)
        return record

    @staticmethod
    def _datetime_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise ValueError
        return value.isoformat()

    @staticmethod
    def _validate_state(projects: list[Project], tasks: list[Task]) -> None:
        if len({project.id for project in projects}) != len(projects):
            raise ValueError
        if len({task.id for task in tasks}) != len(tasks):
            raise ValueError
        project_ids = {project.id for project in projects}
        task_ids = {task.id for task in tasks}
        for task in tasks:
            if task.project_id is not None and task.project_id not in project_ids:
                raise ValueError
            if any(dependency not in task_ids for dependency in task.dependencies):
                raise ValueError
        try:
            tuple(TopologicalSorter({task.id: task.dependencies for task in tasks}).static_order())
        except CycleError:
            raise ValueError from None

    def _write(self, projects: list[Project], tasks: list[Task]) -> None:
        if self.path.is_symlink():
            raise ValueError("Invalid project task state")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False,
                prefix=f".{self.path.name}.", suffix=".tmp",
            ) as output:
                temporary = Path(output.name)
                json.dump(
                    {
                        "version": self.VERSION,
                        "projects": [self._project_record(project) for project in sorted(projects, key=lambda item: item.id)],
                        "tasks": [self._task_record(task) for task in sorted(tasks, key=lambda item: item.id)],
                    },
                    output, ensure_ascii=False, indent=2, sort_keys=True,
                )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
