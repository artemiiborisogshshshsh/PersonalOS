"""
Project Domain Service
Encapsulates business logic for project-related entities:
- Project
- Task
- Task dependencies and priorities
"""

from typing import List, Optional, Dict, Any, Set
from datetime import datetime, timedelta
from models import Project, Task, ProjectStatus, TaskPriority, TaskStatus
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType
from ids import IDGenerator
from persistence.repository import project_repo, task_repo


class ProjectService:
    """Service for managing projects and tasks."""

    def __init__(self, planning_engine: Optional[PlanningEngine] = None):
        """
        Initialize the project service.

        Args:
            planning_engine: Optional planning engine for scheduling tasks
        """
        self.planning_engine = planning_engine or PlanningEngine()
        self.IDGenerator = IDGenerator()

    # ===== Project Methods =====

    def create_project(self, name: str, description: str,
                      status: ProjectStatus = ProjectStatus.PLANNING,
                      start_date: Optional[datetime] = None,
                      target_date: Optional[datetime] = None,
                      tags: Optional[Set[str]] = None,
                      metadata: Optional[Dict[str, Any]] = None) -> Project:
        """
        Create a new project.

        Args:
            name: Project name
            description: Project description
            status: Project status (defaults to PLANNING)
            start_date: Project start date
            target_date: Project target completion date
            tags: Set of tags for the project
            metadata: Additional metadata

        Returns:
            Project: Created project
        """
        project_id = self.IDGenerator.generate_uid()

        project = Project(
            id=project_id,
            name=name,
            description=description,
            status=status,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            start_date=start_date,
            target_date=target_date,
            tags=tags or set(),
            metadata=metadata or {}
        )

        # Persist the project
        return project_repo.create(project)

    def get_project(self, id: str) -> Optional[Project]:
        """
        Get a project by ID.

        Args:
            id: Project ID

        Returns:
            Project: Found project or None
        """
        return project_repo.get_by_id(id)

    def list_projects(self, limit: Optional[int] = None, offset: int = 0) -> List[Project]:
        """
        List projects with pagination.

        Args:
            limit: Maximum number of projects to return
            offset: Number of projects to skip

        Returns:
            List[Project]: List of projects
        """
        return project_repo.get_all(limit=limit, offset=offset)

    def update_project(self, project: Project) -> Project:
        """
        Update an existing project.

        Args:
            project: Project to update

        Returns:
            Project: Updated project
        """
        project.updated_at = datetime.now()
        return project_repo.update(project)

    def delete_project(self, id: str) -> bool:
        """
        Delete a project by ID.

        Args:
            id: Project ID

        Returns:
            bool: True if deleted, False if not found
        """
        return project_repo.delete(id)

    def update_project_status(self, project: Project, status: ProjectStatus) -> Project:
        """
        Update project status.

        Args:
            project: Project to update
            status: New status

        Returns:
            Project: Updated project
        """
        project.update_status(status)
        return self.update_project(project)

    def add_project_tag(self, project: Project, tag: str) -> Project:
        """
        Add a tag to a project.

        Args:
            project: Project to modify
            tag: Tag to add

        Returns:
            Project: Modified project
        """
        project.add_tag(tag)
        return self.update_project(project)

    def remove_project_tag(self, project: Project, tag: str) -> Project:
        """
        Remove a tag from a project.

        Args:
            project: Project to modify
            tag: Tag to remove

        Returns:
            Project: Modified project
        """
        project.remove_tag(tag)
        return self.update_project(project)

    def get_projects_by_status(self, projects: List[Project],
                              status: ProjectStatus) -> List[Project]:
        """
        Get projects by status.

        Args:
            projects: List of projects
            status: Status to filter by

        Returns:
            List[Project]: Filtered list of projects
        """
        return [project for project in projects if project.status == status]

    # ===== Task Methods =====

    def create_task(self, title: str, description: str,
                   project_id: Optional[str] = None,
                   priority: TaskPriority = TaskPriority.MEDIUM,
                   status: TaskStatus = TaskStatus.INBOX,
                   due_date: Optional[datetime] = None,
                   estimated_hours: Optional[float] = None,
                   assignee: Optional[str] = None,
                   tags: Optional[Set[str]] = None,
                   metadata: Optional[Dict[str, Any]] = None,
                   dependencies: Optional[List[str]] = None) -> Task:
        """
        Create a new task.

        Args:
            title: Task title
            description: Task description
            project_id: Optional project ID (None for standalone tasks)
            priority: Task priority (defaults to MEDIUM)
            status: Task status (defaults to TODO)
            due_date: Optional due date
            estimated_hours: Estimated hours to complete
            assignee: Person assigned to the task
            tags: Set of tags for the task
            metadata: Additional metadata
            dependencies: List of task IDs that must be completed first

        Returns:
            Task: Created task
        """
        task_id = self.IDGenerator.generate_uid()

        task = Task(
            id=task_id,
            title=title,
            description=description,
            project_id=project_id,
            status=status,
            priority=priority,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            due_date=due_date,
            estimated_hours=estimated_hours,
            actual_hours=None,
            assignee=assignee,
            tags=tags or set(),
            dependencies=dependencies or [],
            metadata=metadata or {}
        )

        # Persist the task
        created_task = task_repo.create(task)

        # Persist task dependencies
        if dependencies:
            self._save_task_dependencies(created_task.id, dependencies)

        return created_task

    def get_task(self, id: str) -> Optional[Task]:
        """
        Get a task by ID.

        Args:
            id: Task ID

        Returns:
            Task: Found task or None
        """
        task = task_repo.get_by_id(id)
        if task:
            # Load dependencies
            task.dependencies = self._get_task_dependencies(task.id)
        return task

    def list_tasks(self, limit: Optional[int] = None, offset: int = 0) -> List[Task]:
        """
        List tasks with pagination.

        Args:
            limit: Maximum number of tasks to return
            offset: Number of tasks to skip

        Returns:
            List[Task]: List of tasks
        """
        tasks = task_repo.get_all(limit=limit, offset=offset)
        # Load dependencies for each task
        for task in tasks:
            task.dependencies = self._get_task_dependencies(task.id)
        return tasks

    def update_task(self, task: Task) -> Task:
        """
        Update an existing task.

        Args:
            task: Task to update

        Returns:
            Task: Updated task
        """
        task.updated_at = datetime.now()
        updated_task = task_repo.update(task)

        # Update task dependencies
        self._save_task_dependencies(task.id, task.dependencies)

        return updated_task

    def delete_task(self, id: str) -> bool:
        """
        Delete a task by ID.

        Args:
            id: Task ID

        Returns:
            bool: True if deleted, False if not found
        """
        # Delete dependencies first
        self._delete_task_dependencies(id)
        # Delete the task
        return task_repo.delete(id)

    def update_task_status(self, task: Task, status: TaskStatus) -> Task:
        """
        Update task status.

        Args:
            task: Task to modify
            status: New status

        Returns:
            Task: Modified task
        """
        task.update_status(status)
        return self.update_task(task)

    def set_task_priority(self, task: Task, priority: TaskPriority) -> Task:
        """
        Set task priority.

        Args:
            task: Task to modify
            priority: New priority

        Returns:
            Task: Modified task
        """
        task.priority = priority
        task.updated_at = datetime.now()
        return self.update_task(task)

    def assign_task(self, task: Task, assignee: str) -> Task:
        """
        Assign task to a person.

        Args:
            task: Task to assign
            assignee: Person to assign task to

        Returns:
            Task: Modified task
        """
        task.assignee = assignee
        task.updated_at = datetime.now()
        return self.update_task(task)

    def set_task_dure_date(self, task: Task, due_date: datetime) -> Task:
        """
        Set task due date.

        Args:
            task: Task to modify
            due_date: New due date

        Returns:
            Task: Modified task
        """
        task.due_date = due_date
        task.updated_at = datetime.now()
        return self.update_task(task)

    def set_task_estimate(self, task: Task, estimated_hours: float) -> Task:
        """
        Set task estimated hours.

        Args:
            task: Task to modify
            estimated_hours: Estimated hours to complete

        Returns:
            Task: Modified task
        """
        task.estimated_hours = estimated_hours
        task.updated_at = datetime.now()
        return self.update_task(task)

    def add_task_dependency(self, task: Task, dependency_id: str) -> Task:
        """
        Add a dependency to a task.

        Args:
            task: Task to modify
            dependency_id: ID of task that must be completed first

        Returns:
            Task: Modified task
        """
        if dependency_id not in task.dependencies:
            task.dependencies.append(dependency_id)
            self._save_task_dependencies(task.id, task.dependencies)
        return task

    def remove_task_dependency(self, task: Task, dependency_id: str) -> Task:
        """
        Remove a dependency from a task.

        Args:
            task: Task to modify
            dependency_id: ID of dependency to remove

        Returns:
            Task: Modified task
        """
        if dependency_id in task.dependencies:
            task.dependencies.remove(dependency_id)
            self._save_task_dependencies(task.id, task.dependencies)
        return task

    def add_task_tag(self, task: Task, tag: str) -> Task:
        """
        Add a tag to a task.

        Args:
            task: Task to modify
            tag: Tag to add

        Returns:
            Task: Modified task
        """
        task.add_tag(tag)
        return self.update_task(task)

    def remove_task_tag(self, task: Task, tag: str) -> Task:
        """
        Remove a tag from a task.

        Args:
            task: Task to modify
            tag: Tag to remove

        Returns:
            Task: Modified task
        """
        task.remove_tag(tag)
        return self.update_task(task)

    def get_overdue_tasks(self, tasks: List[Task]) -> List[Task]:
        """
        Get overdue tasks.

        Args:
            tasks: List of tasks

        Returns:
            List[Task]: List of overdue tasks
        """
        return [task for task in tasks if task.is_overdue]

    def get_high_priority_tasks(self, tasks: List[Task]) -> List[Task]:
        """
        Get high priority tasks.

        Args:
            tasks: List of tasks

        Returns:
            List[Task]: List of high priority tasks
        """
        return [task for task in tasks if task.is_high_priority]

    def get_tasks_by_status(self, tasks: List[Task],
                           status: TaskStatus) -> List[Task]:
        """
        Get tasks by status.

        Args:
            tasks: List of tasks
            status: Status to filter by

        Returns:
            List[Task]: Filtered list of tasks
        """
        return [task for task in tasks if task.status == status]

    def get_tasks_by_project(self, tasks: List[Task],
                            project_id: str) -> List[Task]:
        """
        Get tasks by project ID.

        Args:
            tasks: List of tasks
            project_id: Project ID to filter by

        Returns:
            List[Task]: Filtered list of tasks
        """
        return [task for task in tasks if task.project_id == project_id]

    # ===== Task Scheduling Methods =====

    def schedule_tasks(self, tasks: List[Task],
                      planning_horizon_start: datetime,
                      planning_horizon_end: datetime,
                      granularity_minutes: int = 30) -> Any:
        """
        Schedule tasks using the planning engine.

        Args:
            tasks: List of tasks to schedule
            planning_horizon_start: Start of planning horizon
            planning_horizon_end: End of planning horizon
            granularity_minutes: Time slot granularity in minutes

        Returns:
            Schedule: Result from planning engine
        """
        # Convert tasks to planning items
        planning_items = []
        for task in tasks:
            # Skip tasks that are already done or archived
            if task.status in [TaskStatus.COMPLETED, TaskStatus.ARCHIVED]:
                continue

            planning_item = PlanningItem(
                id=task.id,
                title=task.title,
                description=task.description,
                item_type=PlanningItemType.PROJECT_TASK,
                preferred_start=task.due_date - timedelta(hours=2) if task.due_date else None,
                preferred_end=task.due_date,
                duration_minutes=int(task.estimated_hours * 60) if task.estimated_hours else 60,
                earliest_start=task.created_at,
                latest_end=task.due_date,
                flexible=True,
                priority=3 if task.is_high_priority else 1,
                dependencies=set(task.dependencies),
                metadata={
                    'project_id': task.project_id,
                    'status': task.status.value,
                    'priority': task.priority.value,
                    'actual_hours': task.actual_hours,
                    'estimated_hours': task.estimated_hours
                }
            )
            planning_items.append(planning_item)

        # Schedule using planning engine
        return self.planning_engine.schedule_items(
            planning_items,
            planning_horizon_start,
            planning_horizon_end,
            granularity_minutes
        )

    def resolve_task_dependencies(self, tasks: List[Task]) -> Dict[str, List[str]]:
        """
        Analyze task dependencies and identify any circular dependencies.

        Args:
            tasks: List of tasks to analyze

        Returns:
            Dict with keys:
                - 'ready': List of task IDs with no unmet dependencies
                - 'waiting': Dict mapping task ID to list of unmet dependency IDs
                - 'circular': List of circular dependency chains (if any)
        """
        # Create lookup dictionaries
        task_dict = {task.id: task for task in tasks}
        ready_tasks = []
        waiting_tasks = {}

        # Check each task's dependencies
        for task in tasks:
            unmet_deps = []
            for dep_id in task.dependencies:
                if dep_id not in task_dict:
                    unmet_deps.append(f"Missing task: {dep_id}")
                elif task_dict[dep_id].status not in [TaskStatus.COMPLETED]:
                    unmet_deps.append(dep_id)

            if not unmet_deps:
                ready_tasks.append(task.id)
            else:
                waiting_tasks[task.id] = unmet_deps

        # Simple circular dependency detection (could be enhanced)
        circular_chains = []  # Placeholder for more complex detection

        return {
            'ready': ready_tasks,
            'waiting': waiting_tasks,
            'circular': circular_chains
        }

    # ===== Private Helper Methods =====

    def _save_task_dependencies(self, task_id: str, dependency_ids: List[str]):
        """Save task dependencies to the database."""
        # Delete existing dependencies
        self._delete_task_dependencies(task_id)

        # Insert new dependencies
        if dependency_ids:
            with self.db.get_cursor() as cursor:
                for dep_id in dependency_ids:
                    cursor.execute(
                        "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
                        (task_id, dep_id)
                    )

    def _delete_task_dependencies(self, task_id: str):
        """Delete task dependencies from the database."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM task_dependencies WHERE task_id = ?",
                (task_id,)
            )

    def _get_task_dependencies(self, task_id: str) -> List[str]:
        """Get task dependencies from the database."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
                (task_id,)
            )
            rows = cursor.fetchall()
            return [row[0] for row in rows]

    @property
    def db(self):
        """Get database instance."""
        from persistence.database import get_database
        return get_database()


# ===== Use Case Demonstrations =====

def demonstrate_project_services():
    """Demonstrate use cases for the project service."""
    print("=== Project Domain Service Use Cases ===\n")

    # Initialize service
    service = ProjectService()

    # Use Case 1: Creating projects and tasks
    print("Use Case 1: Creating Projects and Tasks")
    project = service.create_project(
        name="Personal Website Redesign",
        description="Redesign personal portfolio website with modern technologies",
        status=ProjectStatus.PLANNING,
        start_date=datetime(2026, 9, 1),
        target_date=datetime(2026, 12, 31),
        tags={"web", "design", "portfolio"},
        metadata={"client": "personal", "budget": 0}
    )

    print(f"  Created project: {project.name}")
    print(f"  ID: {project.id}")
    print(f"  Status: {project.status.value}")
    print(f"  Tags: {', '.join(project.tags)}")
    print()

    # Add tags to project
    service.add_project_tag(project, "frontend")
    service.add_project_tag(project, "responsive")
    print(f"  After adding tags: {', '.join(sorted(project.tags))}")
    print()

    # Create tasks for the project
    task1 = service.create_task(
        title="Design homepage layout",
        description="Create wireframes and mockups for the homepage",
        project_id=project.id,
        priority=TaskPriority.HIGH,
        status=TaskStatus.INBOX,
        due_date=datetime(2026, 9, 10, 18, 0),
        estimated_hours=8.0,
        assignee="designer",
        tags={"design", "ui"},
        dependencies=[]
    )

    task2 = service.create_task(
        title="Implement frontend components",
        description="Build reusable React components for the website",
        project_id=project.id,
        priority=TaskPriority.HIGH,
        status=TaskStatus.INBOX,
        due_date=datetime(2026, 9, 20, 18, 0),
        estimated_hours=20.0,
        assignee="developer",
        tags={"frontend", "react"},
        dependencies=[task1.id]  # Depends on design
    )

    task3 = service.create_task(
        title="Set up development environment",
        description="Configure local development environment with Node.js and git",
        project_id=project.id,
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.IN_PROGRESS,
        due_date=datetime(2026, 9, 3, 18, 0),
        estimated_hours=4.0,
        assignee="developer",
        tags={"devops", "setup"},
        dependencies=[]
    )

    task4 = service.create_task(
        title="Write content and copy",
        description="Create all text content for the website pages",
        project_id=project.id,
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.INBOX,
        due_date=datetime(2026, 9, 15, 18, 0),
        estimated_hours=12.0,
        assignee="content writer",
        tags={"content", "copywriting"},
        dependencies=[task1.id]  # Depends on design for layout guidance
    )

    task5 = service.create_task(
        title="Integrate analytics",
        description="Set up Google Analytics and tracking",
        project_id=project.id,
        priority=TaskPriority.LOW,
        status=TaskStatus.INBOX,
        due_date=datetime(2026, 9, 25, 18, 0),
        estimated_hours=3.0,
        assignee="developer",
        tags={"analytics", "tracking"},
        dependencies=[task2.id]  # Depends on frontend implementation
    )

    tasks = [task1, task2, task3, task4, task5]
    print(f"  Created {len(tasks)} tasks for project '{project.name}':")
    for task in tasks:
        print(f"  - {task.title} [{task.priority.value}] {task.status.value}")
        if task.assignee:
            print(f"    Assigned to: {task.assignee}")
        if task.dependencies:
            print(f"    Dependencies: {', '.join(task.dependencies)}")
    print()

    # Use Case 2: Persistence demonstration
    print("Use Case 2: Persistence Demonstration")
    # Retrieve the project we just created
    retrieved_project = service.get_project(project.id)
    if retrieved_project:
        print(f"  Retrieved project: {retrieved_project.name}")
    print()

    # Retrieve a task and check dependencies
    retrieved_task = service.get_task(task1.id)
    if retrieved_task:
        print(f"  Retrieved task: {retrieved_task.title}")
        print(f"  Dependencies: {retrieved_task.dependencies}")
    print()

    # Use Case 3: Listing all projects and tasks
    print("Use Case 3: Listing Projects and Tasks")
    all_projects = service.list_projects()
    print(f"  Total projects in database: {len(all_projects)}")
    for proj in all_projects:
        print(f"  - {proj.name}")

    all_tasks = service.list_tasks()
    print(f"  Total tasks in database: {len(all_tasks)}")
    for task in all_tasks:
        print(f"  - {task.title} [{task.status.value}]")
    print()

    # Use Case 4: Updating task status and dependencies
    print("Use Case 4: Managing Task Progress")
    # Mark environment setup as completed
    service.update_task_status(task3, TaskStatus.COMPLETED)
    print(f"  Updated task '{task3.title}' to {task3.status.value}")

    # Start working on homepage design
    service.update_task_status(task1, TaskStatus.IN_PROGRESS)
    print(f"  Updated task '{task1.title}' to {task1.status.value}")

    # Add a new dependency
    service.add_task_dependency(task4, task3.id)  # Content depends on environment setup
    print(f"  Added dependency: {task4.title} depends on {task3.title}")
    print()

    # Verify the dependency was saved
    updated_task4 = service.get_task(task4.id)
    if updated_task4:
        print(f"  Updated task '{task4.title}' dependencies: {updated_task4.dependencies}")
    print()

    # Use Case 5: Filtering and querying tasks
    print("Use Case 5: Task Filtering and Queries")
    overdue_tasks = service.get_overdue_tasks(tasks)
    high_priority_tasks = service.get_high_priority_tasks(tasks)
    todo_tasks = service.get_tasks_by_status(tasks, TaskStatus.INBOX)
    in_progress_tasks = service.get_tasks_by_status(tasks, TaskStatus.IN_PROGRESS)
    done_tasks = service.get_tasks_by_status(tasks, TaskStatus.COMPLETED)
    project_tasks = service.get_tasks_by_project(tasks, project.id)

    print(f"  Overdue tasks: {len(overdue_tasks)}")
    print(f"  High priority tasks: {len(high_priority_tasks)}")
    print(f"  Inbox tasks: {len(todo_tasks)}")
    print(f"  In progress tasks: {len(in_progress_tasks)}")
    print(f"  Completed tasks: {len(done_tasks)}")
    print(f"  Project tasks: {len(project_tasks)}")
    print()

    # Use Case 6: Dependency resolution
    print("Use Case 6: Dependency Resolution")
    dependency_analysis = service.resolve_task_dependencies(tasks)
    print(f"  Tasks ready to start: {len(dependency_analysis['ready'])}")
    print(f"  Tasks waiting on dependencies: {len(dependency_analysis['waiting'])}")
    print(f"  Circular dependencies detected: {len(dependency_analysis['circular'])}")

    if dependency_analysis['ready']:
        print("  Ready tasks:")
        for task_id in dependency_analysis['ready']:
            task = next(t for t in tasks if t.id == task_id)
            print(f"    - {task.title}")

    if dependency_analysis['waiting']:
        print("  Waiting tasks:")
        for task_id, deps in dependency_analysis['waiting'].items():
            task = next(t for t in tasks if t.id == task_id)
            print(f"    - {task.title} waits for: {', '.join(deps)}")
    print()

    # Use Case 7: Scheduling tasks
    print("Use Case 7: Task Scheduling with Planning Engine")
    # Schedule tasks for the next week
    start_time = datetime(2026, 9, 2, 9, 0)  # Tomorrow 9am
    end_time = datetime(2026, 9, 8, 18, 0)   # Next Friday 6pm

    schedule_result = service.schedule_tasks(tasks, start_time, end_time, 60)  # 60-min slots

    scheduled_items = schedule_result.get_scheduled_items()
    unscheduled_items = schedule_result.get_unscheduled_items()

    print(f"  Scheduled {len(scheduled_items)} tasks")
    print(f"  Failed to schedule {len(unscheduled_items)} tasks")

    if unscheduled_items:
        print("  Unscheduled tasks:")
        for item in unscheduled_items:
            estimated_hours = item.metadata.get('estimated_hours') if item.metadata else None
            print(f"    - {item.title} ({estimated_hours or 0}h est.)")

    if scheduled_items:
        print("  Scheduled tasks:")
        for item in scheduled_items:
            # Find the time slots for this item
            item_slots = [slot for slot in schedule_result.slots if slot.scheduled_item_id == item.id]
            if item_slots:
                start_time_slot = min(slot.start for slot in item_slots)
                end_time_slot = max(slot.end for slot in item_slots)
                duration_hours = (end_time_slot - start_time_slot).total_seconds() / 3600
                print(f"    {item.title}: {start_time_slot.strftime('%m/%d %H:%M')} - {end_time_slot.strftime('%H:%M')} ({duration_hours:.1f}h)")
    print()

    # Use Case 8: Project status updates
    print("Use Case 8: Project Lifecycle Management")
    print(f"  Initial project status: {project.status.value}")

    # Move to active when work begins
    service.update_project_status(project, ProjectStatus.ACTIVE)
    print(f"  After starting work: {project.status.value}")

    # Simulate completion
    service.update_project_status(project, ProjectStatus.COMPLETED)
    print(f"  After completion: {project.status.value}")
    print()

    print("✓ All project domain use cases demonstrated successfully!")


if __name__ == "__main__":
    demonstrate_project_services()