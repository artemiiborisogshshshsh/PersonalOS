"""Run due maintenance independently for each user-local clock."""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from services.runtime_schedule import RuntimeSchedule
from services.user_planning_profile_store import UserPlanningProfileStore
from services.user_registry import UserRegistryStore, UserStatePaths


class MultiUserMaintenanceService:
    def __init__(self, registry: UserRegistryStore, paths: UserStatePaths,
                 run_update: Callable[[str, int], None]):
        self.registry, self.paths, self.run_update = registry, paths, run_update

    def tick(self, now: datetime) -> list[tuple[str, int]]:
        if now.tzinfo is None:
            raise ValueError('Maintenance clock must be timezone-aware')
        completed = []
        for account in self.registry.accounts():
            directory = self.paths.directory(account.id)
            settings = UserPlanningProfileStore(directory / 'planning_profile.json').load()
            local_now = now.astimezone(ZoneInfo(settings.profile.timezone))
            schedule = RuntimeSchedule(directory / 'runtime_schedule.json', settings.maintenance_hours)
            for hour in schedule.due_hours(local_now):
                self.run_update(account.id, hour)
                schedule.mark_completed(hour, local_now)
                completed.append((account.id, hour))
        return completed
