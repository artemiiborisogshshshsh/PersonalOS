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

    def _project_desired(
        self,
        plan: WeeklyPlan,
        desired: Dict[str, Dict[str, Any]],
        calendar_summary: str,
    ) -> ProjectionResult:
        calendar_id = self.calendar_adapter._get_or_create_calendar(calendar_summary)
        existing_events = self.calendar_adapter._get_events_in_range(
            plan.week_start.isoformat(), plan.week_end.isoformat()
        )
        existing_by_uid = {
            event.get('iCalUID'): event
            for event in existing_events if event.get('iCalUID')
        }
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

    def project(self, plan: WeeklyPlan) -> ProjectionResult:
        return self._project_desired(
            plan, self._desired_events(plan), self.calendar_summary,
        )


class MultiCalendarPlanProjectionService(WeeklyPlanCalendarProjectionService):
    """Project the full personal plan into the four user-visible calendars."""

    CALENDARS = {
        'study': 'Учёба',
        'work': 'Работа',
        'personal': 'Личное',
        'sleep': 'Сон',
    }

    def _route_for_item(self, item: Any) -> tuple[str, str]:
        session_type = str(item.metadata.get('session_type', '')).lower()
        activity_type = str(item.metadata.get('activity_type', '')).lower()
        domain = str(item.metadata.get('domain', '')).lower()
        commitment = str(item.metadata.get('commitment_type', '')).lower()
        if session_type in {'lecture', 'practical', 'lab'}:
            return 'study', {'lecture': 'ЛК', 'practical': 'ПР', 'lab': 'ЛБ'}[session_type]
        if commitment == 'university':
            return 'study', 'ЛК'
        if commitment == 'sleep' or activity_type == 'sleep':
            return 'sleep', 'SLEEP'
        if activity_type == 'preparation' or item.item_type.value == 'preparation_block':
            return 'work', 'PREPARATION'
        if item.item_type.value == 'project_task' or domain in {'project', 'projects'}:
            return 'work', 'PROJECT_TASK'
        if commitment == 'tutoring' or activity_type == 'tutoring':
            return 'work', 'TUTORING'
        if activity_type == 'reading':
            return 'personal', 'READING'
        if activity_type in {'social', 'friends', 'partner'}:
            return 'personal', 'SOCIAL'
        return 'personal', 'default'

    def project(self, plan: WeeklyPlan) -> ProjectionResult:
        desired = self._desired_events(plan)
        schedule = plan.selected_candidate.schedule if plan.selected_candidate else None
        grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for uid, payload in desired.items():
            item_id = uid.removeprefix(self.UID_PREFIX)
            item = schedule.items[item_id]
            route, event_type = self._route_for_item(item)
            payload = dict(payload)
            payload['event_type'] = event_type
            grouped.setdefault(route, {})[uid] = payload

        result = ProjectionResult()
        for route, route_events in grouped.items():
            projection = self._project_desired(
                plan, route_events, self.CALENDARS[route],
            )
            result.actions.update(projection.actions)
            result.errors.extend(projection.errors)
        return result
