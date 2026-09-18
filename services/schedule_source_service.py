"""Portable schedule-source onboarding with validation before activation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable, List

import requests
from icalendar import Calendar

from services.product_state import (
    ScheduleSource,
    UserProductState,
    UserProductStateStore,
)
from services.tpu_schedule_source import discover_tpu_ical_url, validate_tpu_group_page_url


@dataclass(frozen=True)
class ScheduleSourcePreview:
    source_id: str
    display_name: str
    event_count: int
    sample_titles: List[str]


class ScheduleSourceService:
    """Validates a source first; activation never discards the previous source."""

    def __init__(
        self,
        state_store: UserProductStateStore,
        remote_reader: Callable[[str], bytes] | None = None,
        tpu_discoverer: Callable[[str], str] = discover_tpu_ical_url,
    ):
        self.state_store = state_store
        self.remote_reader = remote_reader or self._read_remote
        self.tpu_discoverer = tpu_discoverer

    def connect_and_preview(
        self, user_id: str, source: ScheduleSource,
    ) -> ScheduleSourcePreview:
        content = self._read(source)
        preview = self._preview(source, content)
        state = self.state_store.load(user_id)
        state.sources[source.id] = source
        self.state_store.save(state)
        return preview

    def activate(self, user_id: str, source_id: str) -> UserProductState:
        state = self.state_store.load(user_id)
        # Only a successfully previewed source can have reached this registry.
        state.activate_source(source_id)
        self.state_store.save(state)
        return state

    def _read(self, source: ScheduleSource) -> bytes:
        if source.kind == 'local_file':
            return Path(source.location).read_bytes()
        if source.kind == 'ics_url':
            return self.remote_reader(source.location)
        if source.kind == 'tpu_group_page':
            validate_tpu_group_page_url(source.location)
            # The generated export URL can include a short-lived key. It is
            # read only in memory and is never written into UserProductState.
            return self.remote_reader(self.tpu_discoverer(source.location))
        raise ValueError('provider_api requires a provider-specific connector')

    @staticmethod
    def _read_remote(location: str) -> bytes:
        response = requests.get(location, timeout=20)
        response.raise_for_status()
        return response.content

    @staticmethod
    def _preview(source: ScheduleSource, content: bytes) -> ScheduleSourcePreview:
        try:
            calendar = Calendar.from_ical(content)
        except (TypeError, ValueError) as error:
            raise ValueError('Источник не содержит корректный iCalendar (ICS) файл') from error
        events = [component for component in calendar.walk() if component.name == 'VEVENT']
        if not events:
            raise ValueError('Источник не содержит событий')
        for component in events:
            if (not component.get('UID') or component.get('DTSTART') is None
                    or component.get('DTEND') is None):
                raise ValueError('Источник содержит неполное событие')
            try:
                start = component.decoded('DTSTART')
                end = component.decoded('DTEND')
                if isinstance(start, date) and not isinstance(start, datetime):
                    start = datetime.combine(start, time.min)
                if isinstance(end, date) and not isinstance(end, datetime):
                    end = datetime.combine(end, time.min)
            except (TypeError, ValueError) as error:
                raise ValueError('Источник содержит событие с нечитаемым временем') from error
            if end <= start:
                raise ValueError('Источник содержит событие с неверным интервалом')
        return ScheduleSourcePreview(
            source_id=source.id,
            display_name=source.display_name,
            event_count=len(events),
            sample_titles=[
                str(component.get('summary', 'Без названия')) for component in events[:5]
            ],
        )
