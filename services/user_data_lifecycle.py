"""Explicit export and deletion of one user's application-owned data."""

from __future__ import annotations

from pathlib import Path
from typing import Dict
import json
import re
import shutil


# Export is deliberately an allow-list. A future credential, audio cache or
# manually copied private file must not silently become part of a data export.
EXPORTABLE_STATE_FILES = frozenset({
    'onboarding.json',
    'product_state.json',
    'attendance_preferences.json',
    'planning_profile.json',
    'draft_operations.json',
    'work_draft_operations.json',
    'shared_preparation.json',
    'runtime_schedule.json',
    'system_edits.json',
    'work_planning_state.json',
    'university_calendar_projection.json',
    'update_all.json',
    'university_reconciliation.json',
    'natural_commands.json',
    'natural_text_proposal.json',
    'tutoring_sessions.json',
    'project_tasks.json',
    'task_planning_proposal.json',
})


class UserDataLifecycleService:
    """Never accesses credentials or files outside ``data/users/<user_id>``."""

    def __init__(self, data_root: Path):
        self.data_root = data_root

    def export(self, user_id: str) -> Dict[str, object]:
        directory = self._directory(user_id)
        files = {}
        if directory.exists():
            for name in sorted(EXPORTABLE_STATE_FILES):
                path = directory / name
                # Never follow a user-controlled symlink out of the scoped
                # directory. State files are JSON and must be regular files.
                if path.is_file() and not path.is_symlink():
                    files[name] = path.read_text(encoding='utf-8')
        return {'user_id': user_id, 'files': files}

    def export_json(self, user_id: str) -> str:
        return json.dumps(self.export(user_id), ensure_ascii=False, indent=2)

    def delete(self, user_id: str, *, confirmed: bool = False) -> None:
        if not confirmed:
            raise PermissionError('Explicit deletion confirmation is required')
        directory = self._directory(user_id)
        if directory.exists():
            shutil.rmtree(directory)

    def _directory(self, user_id: str) -> Path:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', user_id):
            raise ValueError('Invalid user ID')
        root = self.data_root / 'users'
        if root.is_symlink():
            raise ValueError('User data root must not be a symlink')
        directory = root / user_id
        if directory.exists() and directory.resolve().parent != root.resolve():
            raise ValueError('User data directory must not escape the data root')
        return directory
