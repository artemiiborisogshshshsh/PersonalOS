from datetime import datetime

from services.runtime_schedule import RuntimeSchedule


def test_runtime_schedule_runs_each_configured_hour_once_per_day(tmp_path):
    schedule = RuntimeSchedule(tmp_path / 'runtime.json', hours=(5, 22))
    morning = datetime(2026, 9, 3, 5, 1)

    assert schedule.due_hours(morning) == [5]
    schedule.mark_completed(5, morning)
    assert schedule.due_hours(datetime(2026, 9, 3, 17)) == []
    assert schedule.due_hours(datetime(2026, 9, 3, 22)) == [22]
    # Restarting at noon does not replay a stale 05:00 job.
    assert RuntimeSchedule(tmp_path / 'runtime.json', hours=(5, 22)).due_hours(
        datetime(2026, 9, 4, 12),
    ) == []
    assert RuntimeSchedule(tmp_path / 'runtime.json', hours=(5, 22)).due_hours(
        datetime(2026, 9, 4, 22),
    ) == [22]


def test_runtime_schedule_can_apply_changed_hours_without_losing_history(tmp_path):
    schedule = RuntimeSchedule(tmp_path / 'runtime.json', hours=(5,))
    schedule.mark_completed(5, datetime(2026, 9, 3, 5))
    schedule.set_hours((18,))

    assert schedule.due_hours(datetime(2026, 9, 3, 18)) == [18]
