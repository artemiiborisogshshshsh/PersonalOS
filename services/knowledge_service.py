"""
Knowledge Domain Service
Encapsulates business logic for knowledge-related entities:
- KnowledgeItem
- Tag
- KnowledgeBase
- Note types and statuses
"""

from typing import List, Optional, Dict, Any, Set
from datetime import datetime, timedelta
from models import KnowledgeItem, Tag, KnowledgeStatus, NoteType, KnowledgeBase, Project, ProjectStatus
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType
from ids import IDGenerator
from persistence.repository import knowledge_item_repo, \
    KnowledgeItemRepository, TagRepository, KnowledgeBaseRepository, BaseRepository, project_repo


# Repository instances are imported from persistence.repository
knowledge_item_repo = knowledge_item_repo
tag_repo = TagRepository()
knowledge_base_repo = KnowledgeBaseRepository()


class KnowledgeService:
    """Service for managing knowledge items, tags, and knowledge bases."""

    def __init__(self, planning_engine: Optional[PlanningEngine] = None):
        """
        Initialize the knowledge service.

        Args:
            planning_engine: Optional planning engine for scheduling learning/review tasks
        """
        self.planning_engine = planning_engine or PlanningEngine()
        self.IDGenerator = IDGenerator()

    # ===== Knowledge Item Methods =====

    def create_knowledge_item(self, title: str, content: str,
                            note_type: NoteType = NoteType.IDEA,
                            status: KnowledgeStatus = KnowledgeStatus.DRAFT,
                            author: Optional[str] = None,
                            tags: Optional[Set[str]] = None,
                            links: Optional[List[str]] = None,
                            project_id: Optional[str] = None,
                            metadata: Optional[Dict[str, Any]] = None) -> KnowledgeItem:
        """
        Create a new knowledge item.

        Args:
            title: Knowledge item title
            content: Knowledge item content
            note_type: Type of knowledge note (defaults to IDEA)
            status: Knowledge status (defaults to DRAFT)
            author: Author of the knowledge item
            tags: Set of tags for categorization
            links: List of related knowledge item IDs
            project_id: Associated project ID (if any)
            metadata: Additional metadata

        Returns:
            KnowledgeItem: Created knowledge item
        """
        item_id = self.IDGenerator.generate_uid()

        item = KnowledgeItem(
            id=item_id,
            title=title,
            content=content,
            note_type=note_type,
            status=status,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            author=author,
            tags=tags or set(),
            links=links or [],
            project_id=project_id,
            metadata=metadata or {}
        )

        # Persist the knowledge item
        created_item = knowledge_item_repo.create(item)

        # Persist tags
        if tags:
            self._save_knowledge_item_tags(created_item.id, tags)

        return created_item

    def get_knowledge_item(self, id: str) -> Optional[KnowledgeItem]:
        """
        Get a knowledge item by ID.

        Args:
            id: Knowledge item ID

        Returns:
            KnowledgeItem: Found knowledge item or None
        """
        item = knowledge_item_repo.get_by_id(id)
        if item:
            # Load tags
            item.tags = self._get_knowledge_item_tags(item.id)
        return item

    def list_knowledge_items(self, limit: Optional[int] = None, offset: int = 0) -> List[KnowledgeItem]:
        """
        List knowledge items with pagination.

        Args:
            limit: Maximum number of items to return
            offset: Number of items to skip

        Returns:
            List[KnowledgeItem]: List of knowledge items
        """
        items = knowledge_item_repo.get_all(limit=limit, offset=offset)
        # Load tags for each item
        for item in items:
            item.tags = self._get_knowledge_item_tags(item.id)
        return items

    def update_knowledge_item(self, item: KnowledgeItem,
                            title: Optional[str] = None,
                            content: Optional[str] = None,
                            note_type: Optional[NoteType] = None,
                            status: Optional[KnowledgeStatus] = None,
                            author: Optional[str] = None,
                            project_id: Optional[str] = None) -> KnowledgeItem:
        """
        Update a knowledge item.

        Args:
            item: Knowledge item to update
            title: New title (optional)
            content: New content (optional)
            note_type: New note type (optional)
            status: New status (optional)
            author: New author (optional)
            project_id: New project ID (optional)

        Returns:
            KnowledgeItem: Updated knowledge item
        """
        # Get old values for audit
        old_item = self.get_knowledge_item(item.id)

        if title is not None:
            item.title = title
        if content is not None:
            item.content = content
        if note_type is not None:
            item.note_type = note_type
        if status is not None:
            item.update_status(status)
        if author is not None:
            item.author = author
        if project_id is not None:
            item.project_id = project_id

        item.updated_at = datetime.now()
        updated_item = knowledge_item_repo.update(item)

        return updated_item

    def delete_knowledge_item(self, id: str) -> bool:
        """
        Delete a knowledge item by ID.

        Args:
            id: Knowledge item ID

        Returns:
            bool: True if deleted, False if not found
        """
        # Delete tags first
        self._delete_knowledge_item_tags(id)
        # Delete the item
        return knowledge_item_repo.delete(id)

    def set_knowledge_item_status(self, item: KnowledgeItem,
                                 status: KnowledgeStatus) -> KnowledgeItem:
        """
        Set knowledge item status.

        Args:
            item: Knowledge item to modify
            status: New status

        Returns:
            KnowledgeItem: Modified knowledge item
        """
        item.update_status(status)
        return self.update_knowledge_item(item)

    def add_knowledge_item_tag(self, item: KnowledgeItem, tag: str) -> KnowledgeItem:
        """
        Add a tag to a knowledge item.

        Args:
            item: Knowledge item to modify
            tag: Tag to add

        Returns:
            KnowledgeItem: Modified knowledge item
        """
        item.add_tag(tag)
        # Persist the tag association
        tag_obj = self._get_or_create_tag(tag)
        self._add_tag_to_item(item.id, tag_obj.id)
        return item

    def remove_knowledge_item_tag(self, item: KnowledgeItem, tag: str) -> KnowledgeItem:
        """
        Remove a tag from a knowledge item.

        Args:
            item: Knowledge item to modify
            tag: Tag to remove

        Returns:
            KnowledgeItem: Modified knowledge item
        """
        item.remove_tag(tag)
        # Remove the tag association
        tag_obj = tag_repo.find_by(name=tag.lower().strip())
        if tag_obj:
            self._remove_tag_from_item(item.id, tag_obj.id)
        return item

    def add_knowledge_item_link(self, item: KnowledgeItem, knowledge_id: str) -> KnowledgeItem:
        """
        Add a link to another knowledge item.

        Args:
            item: Knowledge item to modify
            knowledge_id: ID of knowledge item to link to

        Returns:
            KnowledgeItem: Modified knowledge item
        """
        item.add_link(knowledge_id)
        # Note: We're not persisting links in this simplified version
        # In a full implementation, we'd have a knowledge_item_links table
        return item

    def remove_knowledge_item_link(self, item: KnowledgeItem, knowledge_id: str) -> KnowledgeItem:
        """
        Remove a link to another knowledge item.

        Args:
            item: Knowledge item to modify
            knowledge_id: ID of knowledge item to unlink

        Returns:
            KnowledgeItem: Modified knowledge item
        """
        item.remove_link(knowledge_id)
        # Note: We're not persisting links in this simplified version
        return item

    def get_knowledge_items_by_type(self, items: List[KnowledgeItem],
                                   note_type: NoteType) -> List[KnowledgeItem]:
        """
        Get knowledge items by note type.

        Args:
            items: List of knowledge items
            note_type: Note type to filter by

        Returns:
            List[KnowledgeItem]: Filtered list of knowledge items
        """
        return [item for item in items if item.note_type == note_type]

    def get_knowledge_items_by_status(self, items: List[KnowledgeItem],
                                     status: KnowledgeStatus) -> List[KnowledgeItem]:
        """
        Get knowledge items by status.

        Args:
            items: List of knowledge items
            status: Status to filter by

        Returns:
            List[KnowledgeItem]: Filtered list of knowledge items
        """
        return [item for item in items if item.status == status]

    def get_knowledge_items_by_tag(self, items: List[KnowledgeItem],
                                  tag: str) -> List[KnowledgeItem]:
        """
        Get knowledge items by tag.

        Args:
            items: List of knowledge items
            tag: Tag to filter by

        Returns:
            List[KnowledgeItem]: Filtered list of knowledge items
        """
        return [item for item in items if item.has_tag(tag)]

    def get_knowledge_items_by_project(self, items: List[KnowledgeItem],
                                      project_id: str) -> List[KnowledgeItem]:
        """
        Get knowledge items by project ID.

        Args:
            items: List of knowledge items
            project_id: Project ID to filter by

        Returns:
            List[KnowledgeItem]: Filtered list of knowledge items
        """
        return [item for item in items if item.project_id == project_id]

    # ===== Tag Methods =====

    def create_tag(self, name: str, color: Optional[str] = None,
                  description: Optional[str] = None) -> Tag:
        """
        Create a new tag.

        Args:
            name: Tag name
            color: Hex color code (optional)
            description: Tag description (optional)

        Returns:
            Tag: Created tag
        """
        tag_id = self.IDGenerator.generate_uid()

        tag = Tag(
            id=IDGenerator.generate_uid(),
            name=name.lower().strip(),
            color=color,
            description=description,
            created_at=datetime.now()
        )

        # Persist the tag
        return tag_repo.create(tag)

    def get_tag(self, id: str) -> Optional[Tag]:
        """
        Get a tag by ID.

        Args:
            id: Tag ID

        Returns:
            Tag: Found tag or None
        """
        return tag_repo.get_by_id(id)

    def list_tags(self, limit: Optional[int] = None, offset: int = 0) -> List[Tag]:
        """
        List tags with pagination.

        Args:
            limit: Maximum number of tags to return
            offset: Number of tags to skip

        Returns:
            List[Tag]: List of tags
        """
        return tag_repo.get_all(limit=limit, offset=offset)

    def update_tag(self, tag: Tag) -> Tag:
        """
        Update an existing tag.

        Args:
            tag: Tag to update

        Returns:
            Tag: Updated tag
        """
        tag.updated_at = datetime.now()
        return tag_repo.update(tag)

    def delete_tag(self, id: str) -> bool:
        """
        Delete a tag by ID.

        Args:
            id: Tag ID

        Returns:
            bool: True if deleted, False if not found
        """
        return tag_repo.delete(id)

    def get_tags_by_name(self, tags: List[Tag], name: str) -> List[Tag]:
        """
        Get tags by name.

        Args:
            tags: List of tags
            name: Tag name to search for

        Returns:
            List[Tag]: Matching tags
        """
        return [tag for tag in tags if tag.name == name.lower().strip()]

    # ===== Knowledge Base Methods =====

    def create_knowledge_base(self, name: str, description: str,
                             tags: Optional[Set[str]] = None,
                             metadata: Optional[Dict[str, Any]] = None) -> KnowledgeBase:
        """
        Create a new knowledge base.

        Args:
            name: Knowledge base name
            description: Knowledge base description
            tags: Initial set of available tags
            metadata: Additional metadata

        Returns:
            KnowledgeBase: Created knowledge base
        """
        kb_id = self.IDGenerator.generate_uid()

        kb = KnowledgeBase(
            id=kb_id,
            name=name,
            description=description,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            items=[],
            tags=tags or set(),
            metadata=metadata or {}
        )

        # Persist the knowledge base
        return knowledge_base_repo.create(kb)

    def get_knowledge_base(self, id: str) -> Optional[KnowledgeBase]:
        """
        Get a knowledge base by ID.

        Args:
            id: Knowledge base ID

        Returns:
            KnowledgeBase: Found knowledge base or None
        """
        return knowledge_base_repo.get_by_id(id)

    def list_knowledge_bases(self, limit: Optional[int] = None, offset: int = 0) -> List[KnowledgeBase]:
        """
        List knowledge bases with pagination.

        Args:
            limit: Maximum number of knowledge bases to return
            offset: Number of knowledge bases to skip

        Returns:
            List[KnowledgeBase]: List of knowledge bases
        """
        return knowledge_base_repo.get_all(limit=limit, offset=offset)

    def update_knowledge_base(self, kb: KnowledgeBase) -> KnowledgeBase:
        """
        Update an existing knowledge base.

        Args:
            kb: KnowledgeBase to update

        Returns:
            KnowledgeBase: Updated knowledge base
        """
        kb.updated_at = datetime.now()
        return knowledge_base_repo.update(kb)

    def delete_knowledge_base(self, id: str) -> bool:
        """
        Delete a knowledge base by ID.

        Args:
            id: Knowledge base ID

        Returns:
            bool: True if deleted, False if not found
        """
        # Note: In a full implementation, we'd need to handle the items
        return knowledge_base_repo.delete(id)

    def add_knowledge_item_to_base(self, kb: KnowledgeBase, knowledge_id: str) -> KnowledgeBase:
        """
        Add a knowledge item to a knowledge base.

        Args:
            kb: Knowledge base to modify
            knowledge_id: ID of knowledge item to add

        Returns:
            KnowledgeBase: Modified knowledge base
        """
        kb.add_item(knowledge_id)
        # Note: We're not persisting knowledge base items in this simplified version
        # In a full implementation, we'd have a knowledge_base_items table
        return kb

    def remove_knowledge_item_from_base(self, kb: KnowledgeBase, knowledge_id: str) -> KnowledgeBase:
        """
        Remove a knowledge item from a knowledge base.

        Args:
            kb: Knowledge base to modify
            knowledge_id: ID of knowledge item to remove

        Returns:
            KnowledgeBase: Modified knowledge base
        """
        kb.remove_item(knowledge_id)
        # Note: We're not persisting knowledge base items in this simplified version
        return kb

    def add_tag_to_base(self, kb: KnowledgeBase, tag: str) -> KnowledgeBase:
        """
        Add a tag to a knowledge base's available tags.

        Args:
            kb: Knowledge base to modify
            tag: Tag to add

        Returns:
            KnowledgeBase: Modified knowledge base
        """
        kb.add_tag(tag)
        # Note: We're not persisting knowledge base tags in this simplified version
        return kb

    def remove_tag_from_base(self, kb: KnowledgeBase, tag: str) -> KnowledgeBase:
        """
        Remove a tag from a knowledge base's available tags.

        Args:
            kb: Knowledge base to modify
            tag: Tag to remove

        Returns:
            KnowledgeBase: Modified knowledge base
        """
        kb.remove_tag(tag)
        # Note: We're not persisting knowledge base tags in this simplified version
        return kb

    def get_kb_items_by_tag(self, kb: KnowledgeBase,
                           all_items: List[KnowledgeItem],
                           tag: str) -> List[KnowledgeItem]:
        """
        Get knowledge items in a knowledge base that have a specific tag.

        Args:
            kb: Knowledge base to search in
            all_items: List of all knowledge items (to look up by ID)
            tag: Tag to filter by

        Returns:
            List[KnowledgeItem]: Knowledge items in the base with the specified tag
        """
        # Get items that are in the knowledge base
        kb_item_ids = set(kb.items)
        kb_items = [item for item in all_items if item.id in kb_item_ids]

        # Filter by tag
        return [item for item in kb_items if item.has_tag(tag)]

    def suggest_related_items(self, item: KnowledgeItem,
                            all_items: List[KnowledgeItem],
                            max_suggestions: int = 5) -> List[KnowledgeItem]:
        """
        Suggest related knowledge items based on shared tags.

        Args:
            item: Knowledge item to find related items for
            all_items: List of all knowledge items
            max_suggestions: Maximum number of suggestions to return

        Returns:
            List[KnowledgeItem]: Suggested related knowledge items
        """
        if not item.tags:
            return []

        # Find items that share tags with the given item
        related_items = []
        for other_item in all_items:
            if other_item.id == item.id:
                continue  # Skip the item itself

            # Count shared tags
            shared_tags = item.tags.intersection(other_item.tags)
            if shared_tags:
                related_items.append((other_item, len(shared_tags)))

        # Sort by number of shared tags (descending) and return top suggestions
        related_items.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in related_items[:max_suggestions]]

    # ===== Learning Task Scheduling Methods =====

    def schedule_learning_tasks(self, knowledge_items: List[KnowledgeItem],
                              planning_horizon_start: datetime,
                              planning_horizon_end: datetime,
                              granularity_minutes: int = 30,
                              review_interval_days: int = 7) -> Any:
        """
        Schedule learning/review tasks for knowledge items using the planning engine.

        Args:
            knowledge_items: List of knowledge items to schedule learning for
            planning_horizon_start: Start of planning horizon
            planning_horizon_end: End of planning horizon
            granularity_minutes: Time slot granularity in minutes
            review_interval_days: How often to review items (in days)

        Returns:
            Schedule: Result from planning engine
        """
        # Convert knowledge items to planning items for learning/review tasks
        planning_items = []
        for item in knowledge_items:
            # Skip archived items
            if item.status == KnowledgeStatus.ARCHIVED:
                continue

            # Determine priority based on status and type
            priority = 1  # Default low priority
            if item.status == KnowledgeStatus.DRAFT:
                priority = 2  # Medium priority for drafts
            elif item.status == KnowledgeStatus.REVIEW:
                priority = 3  # High priority for items needing review
            elif item.note_type == NoteType.LEARNING:
                priority = 2  # Medium priority for learning notes

            # Estimate time based on content length (rough estimate: 1 minute per 10 words)
            estimated_minutes = max(15, item.word_count // 10)  # Minimum 15 minutes

            planning_item = PlanningItem(
                id=f"learn-{item.id}",
                title=f"Изучить: {item.title}",
                description=f"Изучить и закрепить знания: {item.content[:100]}...",
                item_type=PlanningItemType.KNOWLEDGE_ACTIVITY,
                preferred_start=planning_horizon_start,
                preferred_end=planning_horizon_end,
                duration_minutes=estimated_minutes,
                earliest_start=planning_horizon_start,
                latest_end=planning_horizon_end,
                flexible=True,
                priority=priority,
                metadata={
                    'knowledge_item_id': item.id,
                    'note_type': item.note_type.value,
                    'status': item.status.value,
                    'word_count': item.word_count,
                    'tags': list(item.tags),
                    'is_review_item': item.status in [KnowledgeStatus.REVIEW, KnowledgeStatus.PUBLISHED]
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

    def create_review_schedule(self, knowledge_items: List[KnowledgeItem],
                              start_date: datetime,
                              weeks_ahead: int = 4) -> List[Dict[str, Any]]:
        """
        Create a review schedule for knowledge items based on spaced repetition.

        Args:
            knowledge_items: List of knowledge items to schedule reviews for
            start_date: Start date for the review schedule
            weeks_ahead: How many weeks ahead to schedule

        Returns:
            List of review sessions with dates and items to review
        """
        review_sessions = []
        current_date = start_date

        # Generate session dates (every few days)
        session_dates = []
        for week in range(weeks_ahead):
            for day in [0, 2, 4]:  # Monday, Wednesday, Friday each week
                session_date = current_date + timedelta(weeks=week, days=day)
                if session_date.weekday() < 5:  # Only weekdays
                    session_dates.append(session_date)

        # Distribute items across sessions based on difficulty/priority
        # Simple approach: rotate through items
        item_index = 0
        for session_date in session_dates:
            # Select items for this session (up to 3 items per session)
            session_items = []
            for _ in range(min(3, len(knowledge_items))):
                if knowledge_items:
                    session_items.append(knowledge_items[item_index % len(knowledge_items)])
                    item_index += 1

            if session_items:
                review_sessions.append({
                    'date': session_date,
                    'items': session_items,
                    'title': f"Review Session: {session_date.strftime('%d.%m.%Y')}"
                })

        return review_sessions

    # ===== Private Helper Methods =====

    def _save_knowledge_item_tags(self, item_id: str, tags: Set[str]):
        """Save knowledge item tags to the database."""
        # Delete existing tags
        self._delete_knowledge_item_tags(item_id)

        # Insert new tags
        if tags:
            with self.db.get_cursor() as cursor:
                for tag_name in tags:
                    tag_obj = self._get_or_create_tag(tag_name)
                    cursor.execute(
                        "INSERT INTO knowledge_item_tags (knowledge_item_id, tag_id) VALUES (?, ?)",
                        (item_id, tag_obj.id)
                    )

    def _delete_knowledge_item_tags(self, item_id: str):
        """Delete knowledge item tags from the database."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM knowledge_item_tags WHERE knowledge_item_id = ?",
                (item_id,)
            )

    def _get_knowledge_item_tags(self, item_id: str) -> Set[str]:
        """Get knowledge item tags from the database."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                SELECT t.name
                FROM tags t
                JOIN knowledge_item_tags kit ON t.id = kit.tag_id
                WHERE kit.knowledge_item_id = ?
            """, (item_id,))
            rows = cursor.fetchall()
            return {row[0] for row in rows}

    def _get_or_create_tag(self, name: str) -> Tag:
        """Get existing tag or create new one."""
        tag_name = name.lower().strip()
        existing_tag = tag_repo.find_by(name=tag_name)
        if existing_tag:
            return existing_tag[0] if isinstance(existing_tag, list) else existing_tag
        else:
            return self.create_tag(tag_name)

    def _add_tag_to_item(self, item_id: str, tag_id: str):
        """Add tag association to knowledge item."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO knowledge_item_tags (knowledge_item_id, tag_id) VALUES (?, ?)",
                (item_id, tag_id)
            )

    def _remove_tag_from_item(self, item_id: str, tag_id: str):
        """Remove tag association from knowledge item."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM knowledge_item_tags WHERE knowledge_item_id = ? AND tag_id = ?",
                (item_id, tag_id)
            )

    @property
    def db(self):
        """Get database instance."""
        from persistence.database import get_database
        return get_database()


# ===== Use Case Demonstrations =====

def demonstrate_knowledge_services():
    """Demonstrate use cases for the knowledge service."""
    print("=== Knowledge Domain Service Use Cases ===\n")

    # Initialize service
    service = KnowledgeService()

    # Use Case 1: Creating different types of knowledge items
    print("Use Case 1: Creating Knowledge Items")

    # First create projects for the knowledge items
    chemistry_project = project_repo.create(Project(
        id=IDGenerator.generate_uid(),
        name="Химия: Квантовая механика",
        description="Проект по изучению квантовой механики в химии",
        status=ProjectStatus.PLANNING,
        created_at=datetime.now(),
        updated_at=datetime.now()
    ))

    math_project = project_repo.create(Project(
        id=IDGenerator.generate_uid(),
        name="Математические методы",
        description="Изучение математических методов в физике и инженерии",
        status=ProjectStatus.PLANNING,
        created_at=datetime.now(),
        updated_at=datetime.now()
    ))

    signals_project = project_repo.create(Project(
        id=IDGenerator.generate_uid(),
        name="Обработка сигналов",
        description="Проект по обработке цифровых сигналов",
        status=ProjectStatus.PLANNING,
        created_at=datetime.now(),
        updated_at=datetime.now()
    ))

    idea = service.create_knowledge_item(
        title="Метод вращающихся рамок для молекул",
        content="Идея о применении метода вращающихся рамок для упрощения расчетов связей в сложных молекулах",
        note_type=NoteType.IDEA,
        status=KnowledgeStatus.DRAFT,
        author="Иванов И.И.",
        tags={"химия", "квазовая механика", "методы расчетов"},
        project_id=chemistry_project.id
    )

    reference = service.create_knowledge_item(
        title="Таблица интеграловGradshteyn и Ryzhik",
        content="Полная таблица интегралов, серий и произведений для быстрого_reference",
        note_type=NoteType.REFERENCE,
        status=KnowledgeStatus.DRAFT,
        author="Собрание авторов",
        tags={"математика", "интегралы", "справочник"},
        project_id=math_project.id
    )

    learning_note = service.create_knowledge_item(
        title="Преобразование Фурье в обработке сигналов",
        content="Основы преобразования Фурье и его применение в обработке цифровых сигналов",
        note_type=NoteType.LEARNING,
        status=KnowledgeStatus.DRAFT,
        author="Петров П.П.",
        tags={"фильтрация", "сигналы", "Фурье"},
        project_id=signals_project.id
    )

    meeting_note = service.create_knowledge_item(
        title="Заседание кафедры физических наук",
        content="""Присутствовали: Иванов И.И., Петров П.П., Сидорова С.С.
Повестка дня:
1. Утверждение учебного плана на следующий семестр
2. Обсуждение грантовых заявок
3. Планирование конференции
Решения:
1. Учебный план одобрен с minor изменениями
2. Одобрена подача двух грантовых заявок
3. Конференция назначена на 15 мая""",
        note_type=NoteType.MEETING,
        status=KnowledgeStatus.DRAFT,
        author="Сидорова С.С.",
        tags={"администрация", "кафедра", "планирование"},
        project_id=None
    )

    items = [idea, reference, learning_note, meeting_note]
    print(f"  Created {len(items)} knowledge items:")
    for item in items:
        print(f"  - {item.title} [{item.note_type.value}] {item.status.value}")
        print(f"    Author: {item.author or 'Unknown'}")
        print(f"    Words: {item.word_count}, Tags: {', '.join(sorted(item.tags)) if item.tags else 'None'}")
    print()

    # Use Case 2: Persistence demonstration
    print("Use Case 2: Persistence Demonstration")
    # Retrieve the idea we just created
    retrieved_idea = service.get_knowledge_item(idea.id)
    if retrieved_idea:
        print(f"  Retrieved idea: {retrieved_idea.title}")
        print(f"  Tags: {', '.join(sorted(retrieved_idea.tags))}")
    print()

    # Use Case 3: Listing all knowledge items
    print("Use Case 3: Listing Knowledge Items")
    all_items = service.list_knowledge_items()
    print(f"  Total knowledge items in database: {len(all_items)}")
    for item in all_items:
        print(f"  - {item.title} [{item.note_type.value}] {item.status.value}")
    print()

    # Use Case 4: Updating knowledge items
    print("Use Case 4: Updating Knowledge Items")
    # Publish the idea after review
    service.set_knowledge_item_status(idea, KnowledgeStatus.REVIEW)
    service.set_knowledge_item_status(idea, KnowledgeStatus.PUBLISHED)
    print(f"  Updated '{idea.title}' to status: {idea.status.value}")

    # Add a tag to the learning note
    service.add_knowledge_item_tag(learning_note, "обучение")
    print(f"  Added tag 'обучение' to '{learning_note.title}'")
    print(f"  Tags now: {', '.join(sorted(learning_note.tags))}")
    print()

    # Use Case 5: Linking knowledge items
    print("Use Case 5: Linking Related Knowledge Items")
    # Link the learning note to the reference (Fourier transform is in the reference table)
    service.add_knowledge_item_link(learning_note, reference.id)
    print(f"  Linked '{learning_note.title}' -> '{reference.title}'")

    # Link the idea to the learning note (idea applies Fourier transform concepts)
    service.add_knowledge_item_link(idea, learning_note.id)
    print(f"  Linked '{idea.title}' -> '{learning_note.title}'")
    print()

    # Use Case 6: Filtering and querying knowledge items
    print("Use Case 6: Knowledge Item Filtering and Queries")
    ideas = service.get_knowledge_items_by_type(items, NoteType.IDEA)
    references = service.get_knowledge_items_by_type(items, NoteType.REFERENCE)
    learning_notes = service.get_knowledge_items_by_type(items, NoteType.LEARNING)
    meeting_notes = service.get_knowledge_items_by_type(items, NoteType.MEETING)

    drafts = service.get_knowledge_items_by_status(items, KnowledgeStatus.DRAFT)
    reviews = service.get_knowledge_items_by_status(items, KnowledgeStatus.REVIEW)
    published = service.get_knowledge_items_by_status(items, KnowledgeStatus.PUBLISHED)
    archived = service.get_knowledge_items_by_status(items, KnowledgeStatus.ARCHIVED)

    chemistry_items = service.get_knowledge_items_by_tag(items, "химия")
    math_items = service.get_knowledge_items_by_tag(items, "математика")
    signal_items = service.get_knowledge_items_by_tag(items, "сигналы")

    print(f"  By type:")
    print(f"    Ideas: {len(ideas)}")
    print(f"    References: {len(references)}")
    print(f"    Learning notes: {len(learning_notes)}")
    print(f"    Meeting notes: {len(meeting_notes)}")
    print()
    print(f"  By status:")
    print(f"    Drafts: {len(drafts)}")
    print(f"    Review: {len(reviews)}")
    print(f"    Published: {len(published)}")
    print(f"    Archived: {len(archived)}")
    print()
    print(f"  By tag:")
    print(f"    Chemistry: {len(chemistry_items)}")
    print(f"    Mathematics: {len(math_items)}")
    print(f"    Signals: {len(signal_items)}")
    print()

    # Use Case 7: Finding related items
    print("Use Case 7: Finding Related Knowledge Items")
    related_to_idea = service.suggest_related_items(idea, items, 3)
    print(f"  Items related to '{idea.title}':")
    for related_item in related_to_idea:
        shared_tags = idea.tags.intersection(related_item.tags)
        print(f"    - '{related_item.title}' (shared tags: {', '.join(shared_tags)})")

    related_to_learning = service.suggest_related_items(learning_note, items, 3)
    print(f"  Items related to '{learning_note.title}':")
    for related_item in related_to_learning:
        shared_tags = learning_note.tags.intersection(related_item.tags)
        print(f"    - '{related_item.title}' (shared tags: {', '.join(shared_tags)})")
    print()

    # Use Case 8: Creating knowledge bases
    print("Use Case 8: Creating Knowledge Bases")
    chemistry_kb = service.create_knowledge_base(
        name="Химия: Квантовая механика",
        description="База знаний по химической физике и квантовой механике",
        tags={"химия", "квазовая механика", "lecture notes"},
        metadata={"course": "Химия 301", "semester": "Fall 2026"}
    )

    math_kb = service.create_knowledge_base(
        name="Математические методы в физике",
        description="Математические методы, используемые в теоретической и experimental физике",
        tags={"математика", "методы", "reference"},
        metadata={"course": "Математические методы физики", "instructor": "Профессор Смирнов"}
    )

    # Add items to knowledge bases
    service.add_knowledge_item_to_base(chemistry_kb, idea.id)
    service.add_knowledge_item_to_base(chemistry_kb, reference.id)  # Assuming reference has chem content
    service.add_knowledge_item_to_base(math_kb, reference.id)
    service.add_knowledge_item_to_base(math_kb, learning_note.id)

    print(f"  Created knowledge base: '{chemistry_kb.name}'")
    print(f"    Description: {chemistry_kb.description}")
    print(f"    Items: {len(chemistry_kb.items)}")
    print(f"    Tags: {', '.join(sorted(chemistry_kb.tags))}")
    print()

    print(f"  Created knowledge base: '{math_kb.name}'")
    print(f"    Description: {math_kb.description}")
    print(f"    Items: {len(math_kb.items)}")
    print(f"    Tags: {', '.join(sorted(math_kb.tags))}")
    print()

    # Use Case 9: Scheduling learning tasks
    print("Use Case 9: Scheduling Learning and Review Tasks")
    # Schedule learning tasks for the next two weeks
    start_time = datetime(2026, 9, 1, 18, 0)  # Today 6pm
    end_time = datetime(2026, 9, 15, 21, 0)   # Two weeks from today 9pm

    schedule_result = service.schedule_learning_tasks(
        items, start_time, end_time, 60, 7  # 60-min slots, weekly review
    )

    scheduled_items = schedule_result.get_scheduled_items()
    unscheduled_items = schedule_result.get_unscheduled_items()

    print(f"  Scheduled {len(scheduled_items)} learning tasks")
    print(f"  Failed to schedule {len(unscheduled_items)} learning tasks")

    if unscheduled_items:
        print("  Unscheduled learning tasks:")
        for item in unscheduled_items:
            # Extract original knowledge item ID from the planning item ID
            kb_item_id = item.id.replace("learn-", "")
            kb_item = next((it for it in items if it.id == kb_item_id), None)
            if kb_item:
                print(f"    - {kb_item.title} ({kb_item.word_count} words)")

    if scheduled_items:
        print("  Scheduled learning tasks:")
        for item in scheduled_items:
            # Find the time slots for this item
            item_slots = [slot for slot in schedule_result.slots if slot.scheduled_item_id == item.id]
            if item_slots:
                start_time_slot = min(slot.start for slot in item_slots)
                end_time_slot = max(slot.end for slot in item_slots)
                duration_hours = (end_time_slot - start_time_slot).total_seconds() / 3600
                # Extract original knowledge item ID
                kb_item_id = item.id.replace("learn-", "")
                kb_item = next((it for it in items if it.id == kb_item_id), None)
                title = kb_item.title if kb_item else item.title
                print(f"    {title}: {start_time_slot.strftime('%m/%d %H:%M')} - {end_time_slot.strftime('%H:%M')} ({duration_hours:.1f}h)")
    print()

    # Use Case 10: Creating review schedule
    print("Use Case 10: Creating Spaced Repetition Review Schedule")
    review_schedule = service.create_review_schedule(
        items, datetime(2026, 9, 1), 4  # Start Sep 1, 4 weeks ahead
    )

    print(f"  Created {len(review_schedule)} review sessions:")
    for session in review_schedule:
        print(f"    {session['date'].strftime('%d.%m.%Y (%A)')}:")
        print(f"      {session['title']}")
        for item in session['items']:
            print(f"        - {item.title}")
    print()

    # Use Case 11: Demonstrating tag-based filtering in knowledge base
    print("Use Case 11: Knowledge Base Tag-Based Operations")
    # Add some tags to the knowledge base
    service.add_tag_to_base(chemistry_kb, "organic")
    service.add_tag_to_base(chemistry_kb, "physical")

    print(f"  Knowledge base '{chemistry_kb.name}' tags: {', '.join(sorted(chemistry_kb.tags))}")

    # Find items in the KB with specific tags
    if chemistry_kb.items:
        all_known_items = items  # In reality, this would come from a database
        organic_items = service.get_kb_items_by_tag(chemistry_kb, all_known_items, "organic")
        print(f"    Items with 'organic' tag: {len(organic_items)}")

    print()

    print("✓ All knowledge domain use cases demonstrated successfully!")


if __name__ == "__main__":
    demonstrate_knowledge_services()