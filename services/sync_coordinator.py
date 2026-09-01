"""Incremental multi-source synchronization with change-triggered replanning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
from typing import Any, Callable, Dict, List, Optional
import copy
import json

from models import PersonalUniversityEvent, UniversityEvent
from services.attendance_service import (
    AttendanceRuleService,
    UniversityEventChangeType,
)


class SyncCadence(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"

    @property
    def interval(self) -> timedelta:
        return timedelta(days=1 if self == SyncCadence.DAILY else 7)


@dataclass
class SyncOutcome:
    source: str
    changed: bool
    replanned: bool
    snapshot_hash: str
    reconciliation: Optional[Dict[str, Any]] = None
    synced_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class RegisteredScheduleSource:
    """A daily/weekly source participating in aggregate synchronization."""
    name: str
    cadence: SyncCadence
    loader: Callable[[], List[Any]]
    university_source: bool = False


class ScheduleSyncCoordinator:
    """Coordinates source snapshots and replans only after a domain change."""

    def __init__(
        self,
        attendance_service: AttendanceRuleService,
        replan_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ):
        self.attendance_service = attendance_service
        self.replan_callback = replan_callback
        self.last_sync_at: Dict[str, datetime] = {}
        self.last_snapshot_hash: Dict[str, str] = {}
        self.pending_missing: Dict[str, List[UniversityEvent]] = {}
        self.last_snapshots: Dict[str, List[Any]] = {}
        self.registered_sources: Dict[str, RegisteredScheduleSource] = {}

    def register_source(self, source: RegisteredScheduleSource) -> None:
        """Register or replace a named schedule source."""
        if not source.name.strip():
            raise ValueError("Schedule source name cannot be empty")
        self.registered_sources[source.name] = source

    def is_due(
        self,
        source: str,
        cadence: SyncCadence,
        now: Optional[datetime] = None,
    ) -> bool:
        now = now or datetime.now()
        last_sync = self.last_sync_at.get(source)
        return last_sync is None or now - last_sync >= cadence.interval

    def compute_snapshot_hash(self, events: List[UniversityEvent]) -> str:
        payload = "\n".join(
            "|".join((
                event.uid,
                event.summary_normalized,
                event.description or '',
                event.location or '',
                event.dtstart.isoformat(),
                event.dtend.isoformat(),
                event.event_type.value,
                event.status.value,
                str(event.sequence),
            ))
            for event in sorted(events, key=lambda item: (item.uid, item.dtstart))
        )
        return sha256(payload.encode('utf-8')).hexdigest()

    def compute_generic_snapshot_hash(self, records: List[Any]) -> str:
        """Hash arbitrary source records with deterministic field ordering."""
        def normalize(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, Enum):
                return value.value
            if is_dataclass(value):
                return normalize(asdict(value))
            if isinstance(value, dict):
                return {
                    str(key): normalize(item)
                    for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                }
            if isinstance(value, (list, tuple, set)):
                normalized = [normalize(item) for item in value]
                return sorted(
                    normalized,
                    key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
                )
            if hasattr(value, '__dict__'):
                return normalize({
                    key: item for key, item in vars(value).items()
                    if not key.startswith('_')
                })
            return value

        payload = json.dumps(
            normalize(records),
            sort_keys=True,
            ensure_ascii=False,
            separators=(',', ':'),
            default=str,
        )
        return sha256(payload.encode('utf-8')).hexdigest()

    def sync_due_sources(
        self,
        personal_events: List[PersonalUniversityEvent],
        now: Optional[datetime] = None,
    ) -> Dict[str, SyncOutcome]:
        """Sync every due source and trigger at most one aggregate replan."""
        now = now or datetime.now()
        outcomes: Dict[str, SyncOutcome] = {}
        original_callback = self.replan_callback
        self.replan_callback = None
        try:
            for source in self.registered_sources.values():
                if not self.is_due(source.name, source.cadence, now):
                    continue
                new_snapshot = list(source.loader())
                old_snapshot = self.last_snapshots.get(source.name, [])
                if source.university_source:
                    outcome = self.sync(
                        source.name,
                        old_snapshot,
                        new_snapshot,
                        personal_events,
                        now,
                    )
                else:
                    new_hash = self.compute_generic_snapshot_hash(new_snapshot)
                    previous_hash = self.last_snapshot_hash.get(source.name)
                    changed = previous_hash != new_hash and (
                        previous_hash is not None or bool(new_snapshot)
                    )
                    reconciliation = None
                    if changed:
                        reconciliation = {
                            'source': source.name,
                            'old_snapshot': old_snapshot,
                            'new_snapshot': new_snapshot,
                        }
                    self.last_snapshot_hash[source.name] = new_hash
                    self.last_sync_at[source.name] = now
                    outcome = SyncOutcome(
                        source=source.name,
                        changed=changed,
                        replanned=changed,
                        snapshot_hash=new_hash,
                        reconciliation=reconciliation,
                        synced_at=now,
                    )
                self.last_snapshots[source.name] = copy.deepcopy(new_snapshot)
                outcomes[source.name] = outcome
        finally:
            self.replan_callback = original_callback

        changed_outcomes = [outcome for outcome in outcomes.values() if outcome.replanned]
        if changed_outcomes and original_callback is not None:
            original_callback({
                'sources': [outcome.source for outcome in changed_outcomes],
                'outcomes': outcomes,
            })
        return outcomes

    def sync(
        self,
        source: str,
        old_events: List[UniversityEvent],
        new_events: List[UniversityEvent],
        personal_events: List[PersonalUniversityEvent],
        now: Optional[datetime] = None,
    ) -> SyncOutcome:
        now = now or datetime.now()
        new_hash = self.compute_snapshot_hash(new_events)
        previous_hash = self.last_snapshot_hash.get(source)
        pending = self.pending_missing.get(source, [])

        if previous_hash == new_hash and not pending:
            self.last_sync_at[source] = now
            return SyncOutcome(source, False, False, new_hash, synced_at=now)

        comparison_old = pending or old_events
        reconciliation = self.attendance_service.reconcile_schedule_snapshots(
            comparison_old,
            new_events,
            personal_events,
        )
        records = reconciliation['changes']['records']
        meaningful_records = [
            record for record in records
            if record.change_type != UniversityEventChangeType.UNCHANGED
        ]

        still_pending: List[UniversityEvent] = []
        for record in records:
            if record.change_type != UniversityEventChangeType.DISAPPEARED or not record.old_event:
                continue
            personal = next(
                (event for event in personal_events
                 if event.university_event_uid == record.old_event.uid),
                None,
            )
            if personal is not None and personal.state.value != 'cancelled':
                still_pending.append(record.old_event)
        self.pending_missing[source] = still_pending

        replanned = bool(reconciliation['affected'] or reconciliation['created'])
        if replanned and self.replan_callback is not None:
            self.replan_callback(reconciliation)

        self.last_snapshot_hash[source] = new_hash
        self.last_sync_at[source] = now
        return SyncOutcome(
            source=source,
            changed=bool(meaningful_records),
            replanned=replanned,
            snapshot_hash=new_hash,
            reconciliation=reconciliation,
            synced_at=now,
        )
