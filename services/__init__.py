"""Domain services, imported lazily to avoid opening the legacy database at startup."""
from importlib import import_module

_EXPORTS = {
    'UniversityService': '.university_service',
    'ProjectService': '.project_service',
    'KnowledgeService': '.knowledge_service',
    'PreparationBlockService': '.preparation.preparation_block_service',
    'CalendarSyncService': '.calendar.calendar_sync_service',
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
