"""Read-only Google Calendar availability for safe preparation planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List

from planning_engine import PlanningItem, PlanningItemType
from services.weekly_plan_service import CommitmentType, FixedCommitment


SYSTEM_MARKER = 'AI Calendar Block:'
DEFAULT_EXCLUDED_CALENDARS = {
    'personal university schedule', 'university schedule', 'учёба',
}


@dataclass(frozen=True)
class CalendarBusyEvent:
    calendar_id: str
    calendar_name: str
    event_id: str
    title: str
    start: datetime
    end: datetime
    movable_with_confirmation: bool = False
    system_draft: bool = False
    preparation_scope: str = ''


class CalendarAvailabilityService:
    """Classifies visible Google events without mutating any of them."""

    def __init__(
        self,
        calendar_adapter,
        personal_calendar_name: str = 'личное',
        excluded_calendar_names: Iterable[str] = DEFAULT_EXCLUDED_CALENDARS,
    ):
        self.calendar_adapter = calendar_adapter
        self.personal_calendar_name = personal_calendar_name.casefold()
        self.excluded_calendar_names = {
            name.casefold() for name in excluded_calendar_names
        }

    def load(self, start: datetime, end: datetime) -> List[CalendarBusyEvent]:
        result: List[CalendarBusyEvent] = []
        for calendar in self.calendar_adapter.list_visible_calendars():
            calendar_id = calendar.get('id')
            calendar_name = calendar.get('summary', '')
            if not calendar_id:
                continue
            excluded_source_calendar = calendar_name.casefold() in self.excluded_calendar_names
            is_personal = calendar_name.casefold() == self.personal_calendar_name
            for event in self.calendar_adapter.list_events_in_calendar(calendar_id, start, end):
                interval = self._interval(event)
                if interval is None or event.get('status') == 'cancelled':
                    continue
                event_start, event_end = interval
                description = str(event.get('description') or '')
                # TPU lessons in the university calendar are represented by
                # the source schedule as one immutable university-day block.
                # Preparations in that same calendar are different: they are
                # app-owned busy time and must block work preparations (and
                # vice versa). Previously the whole calendar was skipped,
                # which let independent planners place blocks on top of each
                # other.
                if excluded_source_calendar and SYSTEM_MARKER not in description and (
                    calendar_name.casefold() == 'university schedule'
                    or 'Стабильный ID личного события:' in description
                ):
                    continue
                result.append(CalendarBusyEvent(
                    calendar_id=calendar_id,
                    calendar_name=calendar_name,
                    event_id=event.get('id', ''),
                    title=event.get('summary', '(без названия)'),
                    start=event_start,
                    end=event_end,
                    movable_with_confirmation=is_personal,
                    system_draft=SYSTEM_MARKER in description and 'Status: draft' in description,
                    preparation_scope=(
                        'work-preparation' if 'AI Calendar Block: work-prep:' in description
                        else 'university-preparation' if SYSTEM_MARKER in description
                        and calendar_name.casefold() in self.excluded_calendar_names else ''
                    ),
                ))
        return result

    def hard_commitments(
        self, events: Iterable[CalendarBusyEvent],
    ) -> List[FixedCommitment]:
        """Every visible event reserves time for a preparation planner.

        Replanning never has authority to move a Google event.  Its own draft
        is removed transactionally before a replacement is calculated, so it
        does not need a special "movable" exception here.
        """
        return [
            FixedCommitment(
                id=f'google:{event.calendar_id}:{event.event_id}',
                title=event.title,
                start=event.start,
                end=event.end,
                commitment_type=CommitmentType.OTHER,
                metadata={
                    'calendar_id': event.calendar_id,
                    'google_event_id': event.event_id,
                    'external_google_event': True,
                    'preparation_scope': event.preparation_scope,
                },
            )
            for event in events
        ]

    def movable_system_items(
        self, events: Iterable[CalendarBusyEvent],
    ) -> List[PlanningItem]:
        """Deprecated compatibility hook: Calendar events are never displaced."""
        return []

    def personal_commitments(
        self, events: Iterable[CalendarBusyEvent],
    ) -> List[FixedCommitment]:
        """Reserve personal meetings until the user explicitly approves a move."""
        return [
            FixedCommitment(
                id=f'google-personal:{event.calendar_id}:{event.event_id}',
                title=event.title,
                start=event.start,
                end=event.end,
                commitment_type=CommitmentType.OTHER,
                metadata={
                    'calendar_id': event.calendar_id,
                    'google_event_id': event.event_id,
                    'movable_with_confirmation': True,
                },
            )
            for event in events if event.movable_with_confirmation and not event.system_draft
        ]

    @staticmethod
    def _interval(event: dict) -> tuple[datetime, datetime] | None:
        start_raw = event.get('start', {}).get('dateTime')
        end_raw = event.get('end', {}).get('dateTime')
        # All-day items are deliberately deferred: they are surfaced in the
        # daily report but must not yet make a whole day unavailable.
        if not start_raw or not end_raw:
            return None
        return (
            datetime.fromisoformat(start_raw.replace('Z', '+00:00')),
            datetime.fromisoformat(end_raw.replace('Z', '+00:00')),
        )
