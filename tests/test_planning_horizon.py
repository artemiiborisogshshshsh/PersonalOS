from datetime import datetime, timedelta, timezone

import pytest

from services.planning_horizon import planning_calendar_horizon
from services.preparation_integrity import calendar_horizon
from services.adaptive_preparation_service import UserPlanningProfile


@pytest.mark.parametrize('now, start, end', [
    ('2026-09-25T23:59:00+07:00', '2026-09-21', '2026-10-05'),
    ('2026-09-26T00:00:00+07:00', '2026-09-21', '2026-10-12'),
    ('2026-09-27T23:59:00+07:00', '2026-09-21', '2026-10-12'),
    ('2026-09-28T00:00:00+07:00', '2026-09-28', '2026-10-12'),
    ('2026-09-25T17:00:00+00:00', '2026-09-21', '2026-10-12'),
    ('2026-12-26T12:00:00+07:00', '2026-12-21', '2027-01-11'),
    ('2026-09-26T12:00:00', '2026-09-21', '2026-10-12'),
])
def test_calendar_and_planner_share_saturday_horizon(now, start, end):
    reference = datetime.fromisoformat(now)
    expected = tuple(datetime.fromisoformat(value + 'T00:00:00+07:00')
                     for value in (start, end))
    assert planning_calendar_horizon(reference, 'Asia/Tomsk') == expected
    assert calendar_horizon(reference, 'Asia/Tomsk') == expected
    planner_end = expected[1] if reference.tzinfo else expected[1].replace(tzinfo=None)
    assert UserPlanningProfile().planning_horizon_end(reference) == planner_end


def test_fixed_offset_work_clock_rolls_over_on_its_own_saturday():
    zone = timezone(timedelta(hours=-4))
    now = datetime(2026, 9, 25, 23, 59, tzinfo=zone)
    assert planning_calendar_horizon(now, zone)[1] == datetime(2026, 10, 5, tzinfo=zone)
    assert planning_calendar_horizon(now + timedelta(minutes=1), zone)[1] == datetime(
        2026, 10, 12, tzinfo=zone)
