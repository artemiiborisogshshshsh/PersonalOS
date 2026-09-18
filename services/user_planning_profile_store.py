"""Portable user planning profile and recurring sleep commitments."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, Iterable, List
import json
import os

from services.adaptive_preparation_service import UserPlanningProfile
from services.weekly_plan_service import CommitmentType, FixedCommitment


DEFAULT_MAINTENANCE_HOURS = (5, 12, 15, 18, 22)


@dataclass
class UserPlanningSettings:
    profile: UserPlanningProfile = field(default_factory=UserPlanningProfile)
    sleep_start: str = '23:00'
    sleep_end: str = '06:40'
    weekend_sleep_end: str = '08:00'
    sleep_exceptions: Dict[str, bool] = field(default_factory=dict)
    maintenance_hours: List[int] = field(default_factory=lambda: list(DEFAULT_MAINTENANCE_HOURS))

    def normalized_maintenance_hours(self) -> tuple[int, ...]:
        """Return safe local wall-clock hours for background source checks."""
        if not all(type(hour) is int and 0 <= hour <= 23 for hour in self.maintenance_hours):
            raise ValueError('maintenance_hours must contain whole hours from 0 through 23')
        return tuple(sorted(set(self.maintenance_hours)))

    def sleep_commitments(self, start: datetime, end: datetime) -> List[FixedCommitment]:
        start_time = time.fromisoformat(self.sleep_start)
        weekday_end_time = time.fromisoformat(self.sleep_end)
        weekend_end_time = time.fromisoformat(self.weekend_sleep_end)
        cursor = start.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        commitments = []
        while cursor < end:
            sleep_start = cursor.replace(hour=start_time.hour, minute=start_time.minute)
            # Friday/Saturday bedtime ends on a weekend morning.
            end_time = weekend_end_time if (cursor + timedelta(days=1)).weekday() >= 5 else weekday_end_time
            sleep_end = cursor.replace(hour=end_time.hour, minute=end_time.minute)
            if sleep_end <= sleep_start:
                sleep_end += timedelta(days=1)
            key = sleep_start.date().isoformat()
            if not self.sleep_exceptions.get(key, False) and sleep_end > start and sleep_start < end:
                commitments.append(FixedCommitment(
                    id=f'sleep:{key}', title='Сон', start=sleep_start, end=sleep_end,
                    commitment_type=CommitmentType.SLEEP,
                    metadata={'calendar_route': 'Сон'},
                ))
            cursor += timedelta(days=1)
        return commitments

    def routine_commitments(
        self,
        start: datetime,
        end: datetime,
        university_events: Iterable = (),
    ) -> List[FixedCommitment]:
        """Reserve wake-up and wind-down routines without overlapping commute."""
        events_by_day = {}
        for event in university_events:
            events_by_day.setdefault(event.start_time.date(), []).append(event)
        cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
        result = []
        while cursor < end:
            if cursor.weekday() < 5:
                morning_start = cursor.replace(hour=6, minute=40)
                day_events = events_by_day.get(cursor.date(), [])
                if day_events:
                    def comparable_start(event):
                        value = event.start_time
                        if cursor.tzinfo is None and value.tzinfo is not None:
                            return value.replace(tzinfo=None)
                        if cursor.tzinfo is not None and value.tzinfo is None:
                            return value.replace(tzinfo=cursor.tzinfo)
                        return value

                    first_start = min(comparable_start(event) for event in day_events)
                    commute_start = first_start - timedelta(
                        minutes=self.profile.travel_minutes_each_way,
                    )
                    morning_end = min(cursor.replace(hour=7, minute=30), commute_start)
                else:
                    morning_end = cursor.replace(hour=7, minute=50)
                if morning_end > morning_start:
                    result.append(FixedCommitment(
                        id=f'morning:{cursor.date().isoformat()}',
                        title='Утренние процедуры', start=morning_start, end=morning_end,
                        commitment_type=CommitmentType.OTHER,
                    ))
            # Evening wind-down precedes fixed 23:00 sleep on all days.
            wind_down_start = cursor.replace(hour=22, minute=20)
            wind_down_end = cursor.replace(hour=23, minute=0)
            if wind_down_end > start and wind_down_start < end:
                result.append(FixedCommitment(
                    id=f'wind-down:{cursor.date().isoformat()}',
                    title='Вечерние процедуры', start=wind_down_start, end=wind_down_end,
                    commitment_type=CommitmentType.OTHER,
                ))
            cursor += timedelta(days=1)
        return result


class UserPlanningProfileStore:
    VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> UserPlanningSettings:
        if not self.path.exists():
            return UserPlanningSettings()
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if payload.get('version') != self.VERSION:
            raise ValueError('Unsupported planning profile version')
        return UserPlanningSettings(
            profile=UserPlanningProfile(**payload.get('profile', {})),
            sleep_start=payload.get('sleep_start', '23:00'),
            sleep_end=payload.get('sleep_end', '06:40'),
            weekend_sleep_end=payload.get('weekend_sleep_end', '08:00'),
            sleep_exceptions=payload.get('sleep_exceptions', {}),
            maintenance_hours=payload.get('maintenance_hours', list(DEFAULT_MAINTENANCE_HOURS)),
        )

    def save(self, settings: UserPlanningSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': self.VERSION,
            'profile': asdict(settings.profile),
            'sleep_start': settings.sleep_start,
            'sleep_end': settings.sleep_end,
            'weekend_sleep_end': settings.weekend_sleep_end,
            'sleep_exceptions': settings.sleep_exceptions,
            'maintenance_hours': list(settings.normalized_maintenance_hours()),
        }
        temporary_path = None
        try:
            with NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.path.parent,
                delete=False, prefix=f'.{self.path.name}.', suffix='.tmp',
            ) as output:
                json.dump(payload, output, ensure_ascii=False, indent=2)
                temporary_path = Path(output.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
