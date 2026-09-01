"""
Services package for Personal OS AI Calendar system.
Contains application services for each domain.
"""

from .university_service import UniversityService
from .project_service import ProjectService
from .knowledge_service import KnowledgeService
from .preparation.preparation_block_service import PreparationBlockService
from .calendar.calendar_sync_service import CalendarSyncService

__all__ = [
    "UniversityService",
    "ProjectService",
    "KnowledgeService",
    "PreparationBlockService",
    "CalendarSyncService"
]