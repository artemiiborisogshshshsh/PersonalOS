"""Tutoring domain: sessions, travel, material preparation and homework review."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, List

from models import WeeklyCapacityModel
from planning_engine import PlanningItem, PlanningItemType
from services.weekly_plan_service import CommitmentType, FixedCommitment


class TutoringMode(Enum):
    ONLINE = "online"
    OFFLINE = "offline"


@dataclass(frozen=True)
class TutoringSession:
    id: str
    student: str
    start: datetime
    duration_minutes: int
    mode: TutoringMode
    location: str = ""
    travel_before_minutes: int = 0
    travel_after_minutes: int = 0
    material_preparation_minutes: int = 0
    homework_review_minutes: int = 0
    weekly_variable_preparation_minutes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.duration_minutes not in (45, 90):
            raise ValueError("Tutoring session duration must be 45 or 90 minutes")
        for value in (
            self.travel_before_minutes,
            self.travel_after_minutes,
            self.material_preparation_minutes,
            self.homework_review_minutes,
            self.weekly_variable_preparation_minutes,
        ):
            if value < 0:
                raise ValueError("Tutoring workload minutes cannot be negative")
        if self.mode == TutoringMode.ONLINE and (
            self.travel_before_minutes or self.travel_after_minutes
        ):
            raise ValueError("Online tutoring cannot include travel")

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_minutes)

    def fixed_commitments(self) -> List[FixedCommitment]:
        commitments: List[FixedCommitment] = []
        if self.travel_before_minutes:
            commitments.append(FixedCommitment(
                id=f"{self.id}:travel-before",
                title=f"Дорога к {self.student}",
                start=self.start - timedelta(minutes=self.travel_before_minutes),
                end=self.start,
                commitment_type=CommitmentType.TRAVEL,
                location=self.location,
                metadata={'tutoring_session_id': self.id},
            ))
        commitments.append(FixedCommitment(
            id=self.id,
            title=f"Репетиторство: {self.student}",
            start=self.start,
            end=self.end,
            commitment_type=CommitmentType.TUTORING,
            location=self.location if self.mode == TutoringMode.OFFLINE else 'online',
            metadata={
                **self.metadata,
                'tutoring_mode': self.mode.value,
                'student': self.student,
            },
        ))
        if self.travel_after_minutes:
            commitments.append(FixedCommitment(
                id=f"{self.id}:travel-after",
                title=f"Дорога от {self.student}",
                start=self.end,
                end=self.end + timedelta(minutes=self.travel_after_minutes),
                commitment_type=CommitmentType.TRAVEL,
                location=self.location,
                metadata={'tutoring_session_id': self.id},
            ))
        return commitments

    def preparation_items(self, week_start: datetime) -> List[PlanningItem]:
        items: List[PlanningItem] = []
        preparation_minutes = (
            self.material_preparation_minutes
            + self.weekly_variable_preparation_minutes
        )
        if preparation_minutes:
            items.append(PlanningItem(
                id=f"{self.id}:materials",
                title=f"Материалы для {self.student}",
                description="Подготовить материалы к занятию",
                item_type=PlanningItemType.PROJECT_TASK,
                duration_minutes=preparation_minutes,
                earliest_start=week_start,
                latest_end=self.start,
                flexible=True,
                priority=3,
                metadata={
                    'domain': 'tutoring',
                    'activity_type': 'material_preparation',
                    'deep_work': True,
                    'tutoring_session_id': self.id,
                },
            ))
        if self.homework_review_minutes:
            items.append(PlanningItem(
                id=f"{self.id}:homework-review",
                title=f"Проверка ДЗ: {self.student}",
                description="Проверить домашнюю работу после занятия",
                item_type=PlanningItemType.PROJECT_TASK,
                duration_minutes=self.homework_review_minutes,
                earliest_start=self.end,
                latest_end=self.end + timedelta(days=2),
                flexible=True,
                priority=2,
                metadata={
                    'domain': 'tutoring',
                    'activity_type': 'homework_review',
                    'tutoring_session_id': self.id,
                },
            ))
        return items


class TutoringService:
    def build_weekly_inputs(
        self,
        sessions: List[TutoringSession],
        week_start: datetime,
    ) -> tuple[List[FixedCommitment], List[PlanningItem]]:
        commitments: List[FixedCommitment] = []
        items: List[PlanningItem] = []
        for session in sessions:
            commitments.extend(session.fixed_commitments())
            items.extend(session.preparation_items(week_start))
        return commitments, items

    def apply_capacity(
        self,
        capacity: WeeklyCapacityModel,
        sessions: List[TutoringSession],
    ) -> WeeklyCapacityModel:
        capacity.teaching_load += sum(session.duration_minutes for session in sessions)
        capacity.travel_load += sum(
            session.travel_before_minutes + session.travel_after_minutes
            for session in sessions
        )
        return capacity
