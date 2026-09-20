"""Idempotent Google Calendar projection for a committed WeeklyPlan."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any, Dict, List
import re
from datetime import datetime

from services.calendar.projection_state import CalendarProjectionState
from services.sync_retry import transient_error

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

    def __init__(self, calendar_adapter: Any, calendar_summary: str = 'Personal OS Plan',
                 projection_state: CalendarProjectionState | None = None):
        self.calendar_adapter = calendar_adapter
        self.calendar_summary = calendar_summary
        self.projection_state = projection_state or CalendarProjectionState()

    @staticmethod
    def _owned(event: dict, uid: str) -> bool:
        private = event.get('extendedProperties', {}).get('private', {})
        if private.get('personal_os_block_id') == uid:
            return True
        return (event.get('iCalUID') == uid
                and bool(WeeklyPlanCalendarProjectionService.MARKER_RE.search(
                    str(event.get('description', '')))))

    @staticmethod
    def _same_time(event: dict, payload: dict) -> bool:
        try:
            start = datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
            expected_start, expected_end = payload['dtstart'], payload['dtend']
            if start.tzinfo is not None and expected_start.tzinfo is not None:
                return start == expected_start and end == expected_end
            return (start.replace(tzinfo=None) == expected_start.replace(tzinfo=None)
                    and end.replace(tzinfo=None) == expected_end.replace(tzinfo=None))
        except (KeyError, TypeError, ValueError):
            return False

    def _write(self, calendar_id: str, uid: str, data: Any, action: str,
               event_id: str | None, key: str) -> str:
        reader = getattr(self.calendar_adapter, 'get_event_by_uid', None)
        if callable(reader):
            try:
                current = reader(calendar_id, uid, strict=True)
            except Exception:
                raise RuntimeError('Calendar: чтение не подтверждено.') from None
            if isinstance(current, dict):
                if not self._owned(current, uid):
                    raise RuntimeError('Calendar: владелец события не подтверждён.')
                if action == 'insert':
                    if (current.get('summary') == data.summary
                            and current.get('description') == data.description
                            and self._same_time(current, {'dtstart': data.dtstart,
                                                          'dtend': data.dtend})):
                        self.projection_state.put(key, event_id=current['id'],
                                                  start=data.dtstart.isoformat(),
                                                  end=data.dtend.isoformat())
                        return current['id']
                    raise RuntimeError('Calendar: существующая запись требует проверки.')
                if current.get('id') != event_id:
                    raise RuntimeError('Calendar: прежняя запись не подтверждена.')
                checkpoint = self.projection_state.get(key)
                if checkpoint.get('start') and checkpoint.get('end'):
                    baseline = {'dtstart': datetime.fromisoformat(checkpoint['start']),
                                'dtend': datetime.fromisoformat(checkpoint['end'])}
                    if not self._same_time(current, baseline):
                        self.projection_state.put(key, override='moved')
                        raise RuntimeError('Calendar: ручной перенос сохранён.')
            elif current is None and action == 'update':
                raise RuntimeError('Calendar: прежняя запись не подтверждена.')
        self.projection_state.put(key, pending=action)
        for attempt in range(3):
            try:
                try:
                    if action == 'insert':
                        result = self.calendar_adapter._insert_event(data, calendar_id, strict=True)
                    else:
                        result = self.calendar_adapter._update_event(
                            calendar_id, event_id, data, strict=True)
                except TypeError as error:
                    if 'strict' not in str(error):
                        raise
                    result = (self.calendar_adapter._insert_event(data, calendar_id)
                              if action == 'insert' else
                              self.calendar_adapter._update_event(calendar_id, event_id, data))
                if result:
                    self.projection_state.put(key, pending=None, event_id=result,
                                              start=data.dtstart.isoformat(),
                                              end=data.dtend.isoformat())
                    return result
                error = None
            except Exception as exc:
                error = exc
            reader = getattr(self.calendar_adapter, 'get_event_by_uid', None)
            try:
                remote = reader(calendar_id, uid, strict=True) if callable(reader) else None
            except Exception:
                raise RuntimeError('Calendar: чтение не подтверждено.') from None
            if (isinstance(remote, dict) and self._owned(remote, uid)
                    and remote.get('summary') == data.summary
                    and remote.get('description') == data.description
                    and self._same_time(remote, {'dtstart': data.dtstart, 'dtend': data.dtend})):
                self.projection_state.put(key, pending=None, event_id=remote['id'],
                                          start=data.dtstart.isoformat(),
                                          end=data.dtend.isoformat())
                return remote['id']
            if error is None or not transient_error(error) or attempt == 2:
                raise RuntimeError('Calendar: запись не подтверждена.') from None
        raise RuntimeError('Calendar: запись не подтверждена.')

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
        scoped_reader = getattr(self.calendar_adapter, 'list_events_in_calendar', None)
        scoped = (scoped_reader(calendar_id, plan.week_start, plan.week_end)
                  if callable(scoped_reader) else None)
        existing_events = (scoped if isinstance(scoped, list) else
                           self.calendar_adapter._get_events_in_range(
                               plan.week_start.isoformat(), plan.week_end.isoformat()))
        existing_by_uid = {
            event.get('iCalUID'): event
            for event in existing_events if event.get('iCalUID')
        }
        result = ProjectionResult()

        for uid, payload in desired.items():
            existing = existing_by_uid.get(uid)
            event_data = type('EventData', (), {
                **payload, 'system_block_id': uid,
                'system_source_event_id': uid,
                'system_operation_id': 'weekly-plan',
            })()
            key = f'{calendar_summary}:{uid}'
            checkpoint = self.projection_state.get(key)
            if existing is None:
                if checkpoint.get('event_id') and checkpoint.get('pending') == 'update':
                    raise RuntimeError('Calendar: прежняя запись не подтверждена.')
                if checkpoint.get('event_id') and not checkpoint.get('pending'):
                    self.projection_state.put(key, override='deleted')
                    result.actions[uid] = ProjectionAction.CONFLICT
                    continue
                if checkpoint.get('override') == 'deleted':
                    result.actions[uid] = ProjectionAction.CONFLICT
                    continue
                self._write(calendar_id, uid, event_data, 'insert', None, key)
                result.actions[uid] = ProjectionAction.CREATE
                continue

            if not self._owned(existing, uid):
                result.actions[uid] = ProjectionAction.CONFLICT
                continue
            if checkpoint.get('override') == 'moved':
                result.actions[uid] = ProjectionAction.CONFLICT
                continue
            if checkpoint.get('start') and checkpoint.get('end'):
                baseline = {'dtstart': datetime.fromisoformat(checkpoint['start']),
                            'dtend': datetime.fromisoformat(checkpoint['end'])}
                if not self._same_time(existing, baseline):
                    self.projection_state.put(key, override='moved')
                    result.actions[uid] = ProjectionAction.CONFLICT
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
                self.projection_state.put(key, event_id=existing.get('id'),
                                          start=payload['dtstart'].isoformat(),
                                          end=payload['dtend'].isoformat())
                result.actions[uid] = ProjectionAction.NOOP
                continue

            event_id = existing.get('id')
            update_method = getattr(self.calendar_adapter, '_update_event', None)
            if event_id and callable(update_method):
                self._write(calendar_id, uid, event_data, 'update', event_id, key)
                result.actions[uid] = ProjectionAction.UPDATE
            else:
                result.actions[uid] = ProjectionAction.CONFLICT

        for uid, existing in existing_by_uid.items():
            if not uid.startswith(self.UID_PREFIX) or uid in desired:
                continue
            # An omitted plan item does not prove cancellation. Keep the
            # previous projection until an explicit, source-aware cleanup.
            result.actions[uid] = ProjectionAction.CONFLICT

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
