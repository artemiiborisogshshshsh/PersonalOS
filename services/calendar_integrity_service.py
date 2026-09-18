"""Safe audit and cleanup of Personal OS calendar projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Any, Iterable, Optional


SYSTEM_PREPARATION_MARKER = 'AI Calendar Block:'
MANUAL_CONFLICT_MARKER = 'Manual conflict: true'


@dataclass
class CalendarIntegrityReport:
    scanned_events: int = 0
    duplicate_preparations: int = 0
    stale_preparations: int = 0
    overlapping_preparations: int = 0
    deleted_event_ids: list[str] = field(default_factory=list)
    violations: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def repaired(self) -> int:
        return len(self.deleted_event_ids)

    def render(self) -> str:
        if not self.violations:
            return (
                f'Проверка Calendar завершена: просмотрено системных событий: '
                f'{self.scanned_events}. Нарушений не найдено.'
            )
        unresolved = len(self.violations) - self.repaired
        if unresolved:
            return (
                f'Проверка Calendar завершена: просмотрено системных событий: {self.scanned_events}.\n'
                f'Обнаружено нарушений: {len(self.violations)}; исправлено: {self.repaired}; '
                f'осталось: {unresolved}.\n'
                'Неисправленные системные preparation-события требуют повторной проверки; '
                'личные события, пары TPU и AlfaCRM не изменялись.'
            )
        return (
            f'Проверка Calendar завершена: просмотрено системных событий: {self.scanned_events}.\n'
            f'Удалено только системных preparation-событий: {self.repaired}.\n'
            f'• дубликаты: {self.duplicate_preparations}\n'
            f'• источник отсутствует: {self.stale_preparations}\n'
            f'• пересечение с парой: {self.overlapping_preparations}\n'
            'Личные события, пары TPU и AlfaCRM не изменялись.'
        )


class CalendarIntegrityService:
    """Repairs only events proven to be Personal OS preparation projections."""

    def __init__(self, calendar_adapter: Any):
        self.calendar_adapter = calendar_adapter

    def inspect_and_cleanup(
        self, university_events: Iterable[Any], work_lessons: Iterable[Any],
        now: Optional[datetime] = None, verify_work_sources: bool = True,
        protected_intervals: Iterable[tuple[datetime, datetime]] = (),
        owned_event_ids: Iterable[str] = (),
        horizon: Optional[tuple[datetime, datetime]] = None,
        verified_work_horizon: Optional[tuple[datetime, datetime]] = None,
    ) -> CalendarIntegrityReport:
        report = self.read_violations(
            university_events, work_lessons, now, verify_work_sources,
            protected_intervals, owned_event_ids, horizon, verified_work_horizon,
        )
        return self.apply_allowed_fixes(report)

    def read_violations(
        self, university_events: Iterable[Any], work_lessons: Iterable[Any],
        now: Optional[datetime] = None, verify_work_sources: bool = True,
        protected_intervals: Iterable[tuple[datetime, datetime]] = (),
        owned_event_ids: Iterable[str] = (),
        horizon: Optional[tuple[datetime, datetime]] = None,
        verified_work_horizon: Optional[tuple[datetime, datetime]] = None,
    ) -> CalendarIntegrityReport:
        """Read only. Absence is evidence only inside explicit source coverage."""
        now = now or datetime.now().astimezone()
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        horizon = horizon or (start, start + timedelta(weeks=2))
        source_intervals = self._source_intervals(university_events, work_lessons)
        protected_intervals = [*source_intervals.values(), *protected_intervals]
        report = CalendarIntegrityReport()
        # Older releases did not always persist the description marker after
        # an update.  An event ID kept in an operation snapshot is equally
        # strong proof of ownership, and lets the health check repair those
        # old drafts without ever guessing from a title or touching a user
        # event.
        owned_event_ids = {str(event_id) for event_id in owned_event_ids if event_id}
        candidates: list[tuple[str, dict, str, Optional[str]]] = []
        for calendar in self.calendar_adapter.list_visible_calendars():
            calendar_id = str(calendar.get('id') or '')
            if not calendar_id:
                continue
            for event in self.calendar_adapter.list_events_in_calendar(
                calendar_id, *horizon,
            ):
                interval = self._interval(event)
                if not interval or not self._overlaps(interval, horizon):
                    continue
                if not (
                    self._is_preparation_projection(event)
                    or str(event.get('id') or '') in owned_event_ids
                ):
                    continue
                report.scanned_events += 1
                block_id = self._block_id(event)
                source_id = self._source_id(event)
                candidates.append((calendar_id, event, block_id, source_id))

        # One stable system block has one projection per calendar. Keep the
        # most recently updated event if an old crash left multiple copies.
        groups: dict[tuple[str, str], list[tuple[str, dict, str, Optional[str]]]] = {}
        for item in candidates:
            groups.setdefault((item[0], item[2]), []).append(item)
        # ``violations`` is diagnostic state. It includes conditions that are
        # unsafe to repair automatically because Calendar may contain a
        # user-selected placement for a previously ordinary preparation.
        violations: dict[tuple[str, str], str] = {}
        for group in groups.values():
            ordered = sorted(group, key=lambda item: str(item[1].get('updated') or ''), reverse=True)
            for calendar_id, event, _, _ in ordered[1:]:
                if event.get('id'):
                    violations[(calendar_id, str(event['id']))] = 'duplicate'

        for calendar_id, event, _, source_id in candidates:
            event_id = str(event.get('id') or '')
            if not event_id or (calendar_id, event_id) in violations:
                continue
            interval = self._interval(event)
            source = source_intervals.get(source_id or '')
            if interval and any(self._overlaps(interval, item) for item in protected_intervals):
                # Explicit conflict proposals must stay visible. A regular
                # owned event in the same situation may be a manual move;
                # Calendar alone cannot prove otherwise. Report both and
                # leave the decision to an explicit replan.
                reason = ('manual_conflict' if self._is_authorized_manual_conflict(event)
                          else 'overlap')
                violations[(calendar_id, event_id)] = reason
            elif source is None:
                # Legacy university PersonalUniversityEvent IDs are derived
                # from an imported ICS snapshot and can change on a harmless
                # refresh. Never delete their preparations based solely on
                # that unstable ID. Work lesson IDs are provider-stable.
                if not source_id or not re.match(r'^\d+:.+', source_id):
                    continue
                if not verify_work_sources or verified_work_horizon is None:
                    continue
                if not self._overlaps(interval, verified_work_horizon):
                    continue
                violations[(calendar_id, event_id)] = 'stale'

        report.violations = [(calendar_id, event_id, reason)
                             for (calendar_id, event_id), reason in violations.items()]
        return report

    def apply_allowed_fixes(self, report: CalendarIntegrityReport) -> CalendarIntegrityReport:
        """Apply only the owned-event fixes selected by a fresh inspection."""
        for calendar_id, event_id, reason in report.violations:
            if reason in {'manual_conflict', 'overlap'}:
                continue
            if event_id in report.deleted_event_ids:
                continue
            if self.calendar_adapter._delete_event(calendar_id, event_id):
                report.deleted_event_ids.append(event_id)
                if reason == 'duplicate':
                    report.duplicate_preparations += 1
                elif reason == 'stale':
                    report.stale_preparations += 1
                else:
                    report.overlapping_preparations += 1
        return report

    @staticmethod
    def _source_intervals(university_events: Iterable[Any], work_lessons: Iterable[Any]):
        result = {}
        for event in university_events:
            identifier = getattr(event, 'id', None)
            start = getattr(event, 'start_time', None)
            end = getattr(event, 'end_time', None)
            if identifier and start and end:
                result[str(identifier)] = (start, end)
        for lesson in work_lessons:
            if getattr(lesson, 'id', None):
                result[str(lesson.id)] = (lesson.start, lesson.end)
        return result

    @staticmethod
    def _is_preparation_projection(event: dict) -> bool:
        if CalendarIntegrityService._block_id(event).startswith('work-lesson:'):
            return False
        return SYSTEM_PREPARATION_MARKER in str(event.get('description') or '')

    @staticmethod
    def _is_authorized_manual_conflict(event: dict) -> bool:
        """Whether an app-owned preparation was intentionally left for a manual move.

        A description line alone is user-editable and must never exempt an
        arbitrary event from overlap repair.  The projector writes both
        private ownership properties; require them to agree with the visible
        projection markers before honoring the explicit fallback marker.
        """
        description = str(event.get('description') or '')
        private = event.get('extendedProperties', {}).get('private', {})
        block_id = str(private.get('personal_os_block_id') or '')
        source_id = str(private.get('personal_os_source_event_id') or '')
        operation_id = str(private.get('personal_os_operation_id') or '')
        lines = set(description.splitlines())
        return (
            CalendarIntegrityService._is_preparation_projection(event)
            and MANUAL_CONFLICT_MARKER in lines
            and bool(block_id) and bool(source_id) and bool(operation_id)
            and f'AI Calendar Block: {block_id}' in lines
            and f'AI Calendar Source: {source_id}' in lines
            and f'AI Calendar Operation: {operation_id}' in lines
        )

    @staticmethod
    def _block_id(event: dict) -> str:
        private = event.get('extendedProperties', {}).get('private', {})
        if private.get('personal_os_block_id'):
            return str(private['personal_os_block_id'])
        match = re.search(r'AI Calendar Block: ([^\n]+)', str(event.get('description') or ''))
        return match.group(1).strip() if match else str(event.get('id') or '')

    @staticmethod
    def _source_id(event: dict) -> Optional[str]:
        private = event.get('extendedProperties', {}).get('private', {})
        if private.get('personal_os_source_event_id'):
            return str(private['personal_os_source_event_id'])
        match = re.search(r'AI Calendar Source: ([^\n]+)', str(event.get('description') or ''))
        return match.group(1).strip() if match else None

    @staticmethod
    def _interval(event: dict) -> Optional[tuple[datetime, datetime]]:
        def parse(value: Any) -> Optional[datetime]:
            if not value:
                return None
            try:
                return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            except ValueError:
                return None
        start = parse(event.get('start', {}).get('dateTime'))
        end = parse(event.get('end', {}).get('dateTime'))
        return (start, end) if start and end else None

    @staticmethod
    def _overlaps(left: tuple[datetime, datetime], right: tuple[datetime, datetime]) -> bool:
        start, end = left
        source_start, source_end = right
        if (start.tzinfo is None) != (source_start.tzinfo is None):
            if start.tzinfo is None:
                start, end = start.replace(tzinfo=source_start.tzinfo), end.replace(tzinfo=source_start.tzinfo)
            else:
                source_start = source_start.replace(tzinfo=start.tzinfo)
                source_end = source_end.replace(tzinfo=start.tzinfo)
        return start < source_end and end > source_start
