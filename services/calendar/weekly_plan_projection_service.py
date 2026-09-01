"""Idempotent Google Calendar projection for a committed WeeklyPlan."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any, Dict, List
import re

from services.weekly_plan_service import WeeklyPlan


class ProjectionAction(Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"
    CONFLICT = "conflict"


@dataclass
class ProjectionResult:
    actions: Dict[str, ProjectionAction] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


class WeeklyPlanCalendarProjectionService:
    VERSION = 1
    UID_PREFIX = "personal-os-"
    MARKER_RE = re.compile(
        r'\[PersonalOS projection v(?P<version>\d+) hash=(?P<hash>[a-f0-9]{64})\]'
    )

    def __init__(self, calendar_adapter: Any, calendar_summary: str = 'Personal OS Plan'):
        self.calendar_adapter = calendar_adapter
        self.calendar_summary = calendar_summary

    def _hash(self, payload: Dict[str, Any]) -> str:
        serialized = '|'.join(str(payload.get(key, '')) for key in (
            'uid', 'summary', 'description', 'location', 'dtstart', 'dtend', 'category'
        ))
        return sha256(serialized.encode('utf-8')).hexdigest()

    def _description(self, payload: Dict[str, Any]) -> str:
        event_hash = self._hash(payload)
        return (
            f"{payload['description']}\n"
            f"Категория: {payload['category']}\n"
            f"[PersonalOS projection v{self.VERSION} hash={event_hash}]"
        )

    def _desired_events(self, plan: WeeklyPlan) -> Dict[str, Dict[str, Any]]:
        if plan.selected_candidate is None:
            raise ValueError("Weekly plan has no selected candidate")
        schedule = plan.selected_candidate.schedule
        grouped: Dict[str, List[Any]] = {}
        for slot in schedule.slots:
            if slot.scheduled_item_id:
                grouped.setdefault(slot.scheduled_item_id, []).append(slot)

        desired = {}
        for item_id, slots in grouped.items():
            item = schedule.items[item_id]
            category = (
                item.metadata.get('commitment_type')
                or item.metadata.get('domain')
                or item.metadata.get('activity_type')
                or item.item_type.value
            )
            uid = f"{self.UID_PREFIX}{item.id}"
            payload = {
                'uid': uid,
                'summary': item.title,
                'description': item.description,
                'location': item.metadata.get('location', ''),
                'dtstart': min(slot.start for slot in slots),
                'dtend': max(slot.end for slot in slots),
                'category': category,
            }
            payload['description'] = self._description(payload)
            desired[uid] = payload
        return desired

    def project(self, plan: WeeklyPlan) -> ProjectionResult:
        calendar_id = self.calendar_adapter._get_or_create_calendar(self.calendar_summary)
        existing_events = self.calendar_adapter._get_events_in_range(
            plan.week_start.isoformat(), plan.week_end.isoformat()
        )
        existing_by_uid = {
            event.get('iCalUID'): event
            for event in existing_events if event.get('iCalUID')
        }
        desired = self._desired_events(plan)
        result = ProjectionResult()

        for uid, payload in desired.items():
            existing = existing_by_uid.get(uid)
            event_data = type('EventData', (), payload)()
            if existing is None:
                self.calendar_adapter._insert_event(event_data)
                result.actions[uid] = ProjectionAction.CREATE
                continue

            marker = self.MARKER_RE.search(existing.get('description', ''))
            if marker is None:
                result.actions[uid] = ProjectionAction.CONFLICT
                continue
            if (
                int(marker.group('version')) == self.VERSION
                and marker.group('hash') == self._hash({
                    **payload,
                    'description': payload['description'].split('\nКатегория:', 1)[0],
                })
            ):
                result.actions[uid] = ProjectionAction.NOOP
                continue

            event_id = existing.get('id')
            update_method = getattr(self.calendar_adapter, '_update_event', None)
            if event_id and callable(update_method):
                update_method(calendar_id, event_id, event_data)
                result.actions[uid] = ProjectionAction.UPDATE
            else:
                result.actions[uid] = ProjectionAction.CONFLICT

        for uid, existing in existing_by_uid.items():
            if not uid.startswith(self.UID_PREFIX) or uid in desired:
                continue
            marker = self.MARKER_RE.search(existing.get('description', ''))
            event_id = existing.get('id')
            if marker and event_id:
                self.calendar_adapter._delete_event(calendar_id, event_id)
                result.actions[uid] = ProjectionAction.DELETE

        return result
