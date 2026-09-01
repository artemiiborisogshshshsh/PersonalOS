from datetime import datetime

import pytest

from models import WeeklyCapacityModel
from services.tutoring_service import TutoringMode, TutoringService, TutoringSession
from services.weekly_plan_service import CommitmentType


START = datetime(2026, 9, 8, 16, 0)


def test_offline_tutoring_builds_session_travel_and_flexible_work():
    session = TutoringSession(
        id="lesson-1",
        student="Максим",
        start=START,
        duration_minutes=90,
        mode=TutoringMode.OFFLINE,
        location="Школа",
        travel_before_minutes=30,
        travel_after_minutes=30,
        material_preparation_minutes=45,
        homework_review_minutes=30,
        weekly_variable_preparation_minutes=15,
    )

    commitments, items = TutoringService().build_weekly_inputs([session], START)

    assert [item.commitment_type for item in commitments] == [
        CommitmentType.TRAVEL,
        CommitmentType.TUTORING,
        CommitmentType.TRAVEL,
    ]
    assert {item.duration_minutes for item in items} == {60, 30}
    assert all(item.flexible for item in items)


def test_tutoring_updates_teaching_and_travel_capacity():
    session = TutoringSession(
        id="lesson-1",
        student="Максим",
        start=START,
        duration_minutes=45,
        mode=TutoringMode.OFFLINE,
        travel_before_minutes=20,
        travel_after_minutes=20,
    )
    capacity = WeeklyCapacityModel(sleep_block=0, recovery_block=0, buffer=0)

    TutoringService().apply_capacity(capacity, [session])

    assert capacity.teaching_load == 45
    assert capacity.travel_load == 40


def test_online_tutoring_rejects_travel_and_duration_is_constrained():
    with pytest.raises(ValueError):
        TutoringSession(
            id="online",
            student="Анастасия",
            start=START,
            duration_minutes=60,
            mode=TutoringMode.ONLINE,
        )
    with pytest.raises(ValueError):
        TutoringSession(
            id="online",
            student="Анастасия",
            start=START,
            duration_minutes=45,
            mode=TutoringMode.ONLINE,
            travel_before_minutes=10,
        )
