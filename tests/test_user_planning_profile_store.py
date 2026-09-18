from datetime import datetime, timezone

from services.user_planning_profile_store import (
    UserPlanningProfileStore,
    UserPlanningSettings,
)
from services.adaptive_preparation_service import UserPlanningProfile


def test_profile_store_persists_preferences_and_projects_sleep_as_fixed_commitment(tmp_path):
    store = UserPlanningProfileStore(tmp_path / 'users' / '42' / 'profile.json')
    settings = UserPlanningSettings(
        profile=UserPlanningProfile(travel_minutes_each_way=60),
        sleep_start='23:30', sleep_end='07:15',
    )

    store.save(settings)
    restored = store.load()
    sleep = restored.sleep_commitments(
        datetime(2026, 9, 7), datetime(2026, 9, 9),
    )

    assert restored.profile.practical_minutes == 60
    assert restored.profile.travel_minutes_each_way == 60
    # The sleep that began the preceding evening overlaps the horizon too.
    assert len(sleep) == 3
    assert sleep[0].start.hour == 23
    assert sleep[0].start.minute == 30
    assert sleep[0].end.hour == 7
    assert sleep[0].end.minute == 15


def test_default_weekend_sleep_and_evening_routine_are_fixed_commitments():
    settings = UserPlanningSettings()
    commitments = settings.sleep_commitments(
        datetime(2026, 9, 4), datetime(2026, 9, 7),
    )
    saturday_sleep = next(item for item in commitments if item.end.date().isoformat() == '2026-09-05')
    routines = settings.routine_commitments(
        datetime(2026, 9, 7), datetime(2026, 9, 8), (),
    )

    assert (saturday_sleep.end.hour, saturday_sleep.end.minute) == (8, 0)
    assert any(item.title == 'Вечерние процедуры' for item in routines)


def test_profile_store_persists_per_user_maintenance_hours(tmp_path):
    store = UserPlanningProfileStore(tmp_path / 'profile.json')
    store.save(UserPlanningSettings(maintenance_hours=[22, 5, 18, 18]))

    assert store.load().normalized_maintenance_hours() == (5, 18, 22)


def test_profile_rejects_invalid_maintenance_hour():
    settings = UserPlanningSettings(maintenance_hours=[5, 24])
    try:
        settings.normalized_maintenance_hours()
    except ValueError as error:
        assert 'maintenance_hours' in str(error)
    else:
        raise AssertionError('invalid hour must not be accepted')


def test_routine_commitments_accept_timezone_aware_university_events():
    class Event:
        start_time = datetime(2026, 9, 7, 2, tzinfo=timezone.utc)

    commitments = UserPlanningSettings().routine_commitments(
        datetime(2026, 9, 7), datetime(2026, 9, 8), [Event()],
    )

    assert commitments
