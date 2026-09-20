"""One-time, conservative migration from a checkout-local runtime directory."""

from __future__ import annotations

from pathlib import Path
import shutil


# No OAuth files, environment files, databases, or schedule sources belong in
# this migration. These files are user choices/operation snapshots only.
PORTABLE_STATE_FILES = (
    'attendance_preferences.json',
    'planning_profile.json',
    'draft_operations.json',
    'work_draft_operations.json',
    'work_planning_state.json',
    'university_calendar_projection.json',
    'shared_preparation.json',
    'runtime_schedule.json',
    'update_all.json',
    'system_edits.json',
    'university_reconciliation.json',
    'product_state.json',
    'onboarding.json',
    'natural_commands.json',
    'natural_text_proposal.json',
)


def migrate_missing_user_state(legacy_dir: Path, target_dir: Path) -> list[str]:
    """Copy only missing portable state, preserving target as authoritative."""
    if legacy_dir.resolve() == target_dir.resolve() or not legacy_dir.is_dir():
        return []
    target_dir.mkdir(parents=True, exist_ok=True)
    migrated: list[str] = []
    for name in PORTABLE_STATE_FILES:
        source = legacy_dir / name
        target = target_dir / name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)
            migrated.append(name)
    return migrated
