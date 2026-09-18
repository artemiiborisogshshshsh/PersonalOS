from datetime import datetime, timezone
from dataclasses import replace

from services.multi_user_maintenance import MultiUserMaintenanceService
from services.user_planning_profile_store import UserPlanningProfileStore
from services.user_registry import UserRegistryStore, UserStatePaths


def test_maintenance_uses_each_users_profile_timezone_and_is_idempotent(tmp_path):
    registry, paths = UserRegistryStore(tmp_path / 'registry.json'), UserStatePaths(tmp_path)
    tomsk, utc = registry.get_or_create('101'), registry.get_or_create('202')
    for account, zone in ((tomsk, 'Asia/Tomsk'), (utc, 'UTC')):
        settings = UserPlanningProfileStore(paths.directory(account.id) / 'planning_profile.json').load()
        settings.profile, settings.maintenance_hours = replace(settings.profile, timezone=zone), [12]
        UserPlanningProfileStore(paths.directory(account.id) / 'planning_profile.json').save(settings)
    calls = []
    service = MultiUserMaintenanceService(registry, paths, lambda user, hour: calls.append((user, hour)))
    now = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    assert service.tick(now) == [(utc.id, 12)]
    assert service.tick(now) == []
    assert calls == [(utc.id, 12)]
