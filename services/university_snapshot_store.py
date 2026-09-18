"""Durable, per-user university snapshots and personal-event reconciliation.

The store owns only application state.  The official schedule is read into a
verified snapshot; Google Calendar is neither read nor written here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable
import json
import os

from models import (
    AttendanceConfidence,
    EventType,
    MatchType,
    PersonalEventState,
    PersonalUniversityEvent,
    UniversityEvent,
    UniversityEventStatus,
)
from services.attendance_service import AttendanceRuleService


@dataclass(frozen=True)
class UniversityReconciliationResult:
    changed: bool
    personal_events: list[PersonalUniversityEvent]
    review_events: list[PersonalUniversityEvent]
    created_count: int
    affected_count: int


class UniversitySnapshotStore:
    """Atomically persist the last verified source snapshot for one user."""

    VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    def personal_events(self) -> list[PersonalUniversityEvent]:
        return self._load()["personal_events"]

    def reconcile(
        self,
        verified_events: Iterable[UniversityEvent],
        attendance_service: AttendanceRuleService,
        required_horizon: tuple[datetime, datetime] | None = None,
    ) -> UniversityReconciliationResult:
        """Apply a verified source snapshot without mutating the source.

        The caller must validate/download the ICS before this method.  An
        exception before ``_save`` leaves the previous snapshot intact.
        """
        new_events = list(verified_events)
        if not new_events:
            raise ValueError("A verified university snapshot must contain events")
        if len({event.uid for event in new_events}) != len(new_events):
            raise ValueError("A university snapshot contains duplicate UIDs")
        if required_horizon is not None:
            horizon_start, horizon_end = required_horizon
            if horizon_end <= horizon_start:
                raise ValueError("A university snapshot has an invalid required horizon")
            try:
                intersects_horizon = any(
                    event.dtstart < horizon_end and event.dtend > horizon_start
                    for event in new_events
                )
            except TypeError as error:
                raise ValueError(
                    "University snapshot and required horizon must use one timezone timeline"
                ) from error
            if not intersects_horizon:
                # An ICS cannot prove that a holiday is intentional, but a
                # structurally valid response containing only past/far-future
                # events must never replace the current planning snapshot.
                raise ValueError(
                    "University source snapshot does not cover the requested planning horizon"
                )

        state = self._load()
        old_events = state["source_events"]
        personal_events = state["personal_events"]
        self._apply_attendance_choices(new_events, personal_events, attendance_service)
        ambiguous = self._ambiguous_replacement_candidates(old_events, new_events)
        outcome = attendance_service.reconcile_schedule_snapshots(
            old_events,
            new_events,
            personal_events,
        )
        created = outcome["created"]
        for event in created:
            # The source UID is immutable only for the first appearance.  A
            # later move keeps this personal ID, so Calendar overrides and
            # preparation links survive regenerated source UIDs.
            event.id = f"personal-university:{event.university_event_uid}"

        # The generic diff deliberately leaves an ambiguous same-slot change
        # as DISAPPEARED + ADDED.  At the application boundary we must not
        # turn that into a second attended class: retain the original
        # personal identity and ask the user which candidate replaces it.
        ambiguous_candidate_uids = {
            candidate.uid for candidates in ambiguous.values() for candidate in candidates
        }
        if ambiguous_candidate_uids:
            ambiguous_created_ids = {
                event.id for event in created
                if event.university_event_uid in ambiguous_candidate_uids
            }
            created[:] = [
                event for event in created
                if event.university_event_uid not in ambiguous_candidate_uids
            ]
            personal_events[:] = [
                event for event in personal_events
                if not (
                    event.university_event_uid in ambiguous_candidate_uids
                    and event.id in ambiguous_created_ids
                )
            ]
            for old_uid, candidates in ambiguous.items():
                personal = next(
                    (event for event in personal_events
                     if event.university_event_uid == old_uid), None,
                )
                if personal is None:
                    continue
                personal.state = PersonalEventState.NEEDS_REVIEW
                personal.metadata["change_reason"] = "ambiguous_replacement_requires_confirmation"
                personal.metadata["replacement_candidates"] = [
                    {"uid": event.uid, "title": event.summary,
                     "start_time": event.dtstart.isoformat(),
                     "end_time": event.dtend.isoformat(), "location": event.location}
                    for event in candidates
                ]

        # A class can disappear from one snapshot while other classes remain,
        # then reappear after the intermediate snapshot has become ``old``.
        # UID matching alone in a pairwise diff cannot see that history.  The
        # durable personal projection can, so restore that exact identity
        # instead of adding a second event or leaving it cancelled forever.
        restored_count = 0
        for source_event in new_events:
            personal = next(
                (event for event in personal_events
                 if event.university_event_uid == source_event.uid
                 and event.state in {
                     PersonalEventState.POSSIBLY_CANCELLED,
                     PersonalEventState.CANCELLED,
                 }
                 and event.metadata.get("change_reason") == "missing_from_university_schedule"),
                None,
            )
            if personal is None or source_event.is_cancelled:
                continue
            previous = personal.metadata.pop("state_before_disappearance", None)
            personal.metadata.pop("missing_sync_count", None)
            try:
                personal.state = PersonalEventState(previous) if previous else PersonalEventState.NEEDS_REVIEW
            except ValueError:
                personal.state = PersonalEventState.NEEDS_REVIEW
            attendance_service._apply_source_event(personal, source_event)
            restored_count += 1

        changed = self._snapshot_payload(old_events) != self._snapshot_payload(new_events)
        self._save(new_events, personal_events)
        review_events = [
            event for event in personal_events
            if event.state in {
                PersonalEventState.NEEDS_REVIEW,
                PersonalEventState.POSSIBLY_CANCELLED,
            }
        ]
        return UniversityReconciliationResult(
            changed=changed,
            personal_events=list(personal_events),
            review_events=review_events,
            created_count=len(created),
            affected_count=len(outcome["affected"]) + restored_count,
        )

    def accept_replacement(
        self, personal_event_id: str, candidate_uid: str,
    ) -> PersonalUniversityEvent:
        """Apply an explicitly selected replacement without creating a duplicate."""
        state = self._load()
        personal = next(
            (event for event in state["personal_events"] if event.id == personal_event_id),
            None,
        )
        if personal is None:
            raise KeyError("Unknown personal university event")
        if personal.state != PersonalEventState.NEEDS_REVIEW:
            raise ValueError("This university event does not require replacement review")
        candidates = personal.metadata.get("replacement_candidates")
        if not isinstance(candidates, list):
            candidate = personal.metadata.get("replacement_candidate")
            candidates = [candidate] if candidate else []
        if candidate_uid not in {
            str(candidate.get("uid")) for candidate in candidates if isinstance(candidate, dict)
        }:
            raise ValueError("Replacement candidate is no longer available")
        source = next((event for event in state["source_events"] if event.uid == candidate_uid), None)
        if source is None or source.is_cancelled:
            raise ValueError("Replacement candidate is no longer active in the verified source")

        previous_uid = personal.university_event_uid
        personal.title = source.summary
        personal.description = source.description
        personal.location = source.location
        personal.start_time = source.dtstart
        personal.end_time = source.dtend
        personal.university_event_uid = source.uid
        personal.state = PersonalEventState.MOVED
        personal.metadata["change_reason"] = "replacement_confirmed_by_user"
        personal.metadata["replacement_confirmed_from_uid"] = previous_uid
        personal.metadata.pop("replacement_candidate", None)
        personal.metadata.pop("replacement_candidates", None)
        self._save(state["source_events"], state["personal_events"])
        return personal

    @staticmethod
    def _ambiguous_replacement_candidates(
        old_events: Iterable[UniversityEvent], new_events: Iterable[UniversityEvent],
    ) -> dict[str, list[UniversityEvent]]:
        """Find removed classes with multiple equally plausible same-slot replacements."""
        previous = list(old_events)
        current = list(new_events)
        current_uids = {event.uid for event in current}
        result: dict[str, list[UniversityEvent]] = {}
        for old in previous:
            if old.uid in current_uids:
                continue
            candidates = [
                new for new in current
                if new.uid not in {event.uid for event in previous}
                and new.is_group_event == old.is_group_event
                and (
                    abs((old.dtstart - new.dtstart).total_seconds()) <= 15 * 60
                    or old.dtstart < new.dtend and new.dtstart < old.dtend
                )
            ]
            if len(candidates) > 1:
                result[old.uid] = candidates
        return result

    @staticmethod
    def _apply_attendance_choices(
        source_events: Iterable[UniversityEvent],
        personal_events: Iterable[PersonalUniversityEvent],
        attendance_service: AttendanceRuleService,
    ) -> None:
        """Promote an existing EXPECTED event only after a saved user choice."""
        personal_by_uid = {
            event.university_event_uid: event for event in personal_events
            if event.university_event_uid
        }
        for source_event in source_events:
            personal = personal_by_uid.get(source_event.uid)
            if personal is None or personal.state != PersonalEventState.EXPECTED:
                continue
            result = attendance_service.evaluate_event(source_event)
            if not result.is_matched:
                continue
            personal_id = personal.id
            personal.apply_match_result(result)
            # A preference match confirms attendance; it is not a new
            # identity.  The personal ID remains the stable local reference.
            personal.id = personal_id

    def _load(self) -> dict[str, list[Any]]:
        if not self.path.exists():
            return {"source_events": [], "personal_events": []}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != self.VERSION:
            raise ValueError("Unsupported university snapshot storage version")
        return {
            "source_events": [self._source_from_dict(value)
                              for value in payload.get("source_events", [])],
            "personal_events": [self._personal_from_dict(value)
                                for value in payload.get("personal_events", [])],
        }

    def _save(
        self,
        source_events: Iterable[UniversityEvent],
        personal_events: Iterable[PersonalUniversityEvent],
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "source_events": [self._source_to_dict(event) for event in source_events],
            "personal_events": [self._personal_to_dict(event) for event in personal_events],
        }
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False,
                prefix=f".{self.path.name}.", suffix=".tmp",
            ) as output:
                json.dump(payload, output, ensure_ascii=False, sort_keys=True)
                temporary = Path(output.name)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _source_to_dict(event: UniversityEvent) -> dict[str, Any]:
        return {
            "uid": event.uid, "summary": event.summary,
            "description": event.description, "location": event.location,
            "dtstart": event.dtstart.isoformat(), "dtend": event.dtend.isoformat(),
            "event_type": event.event_type.value, "is_group_event": event.is_group_event,
            "status": event.status.value, "sequence": event.sequence,
        }

    @staticmethod
    def _source_from_dict(value: dict[str, Any]) -> UniversityEvent:
        return UniversityEvent(
            uid=str(value["uid"]), summary=str(value["summary"]),
            description=str(value.get("description", "")), location=str(value.get("location", "")),
            dtstart=datetime.fromisoformat(value["dtstart"]),
            dtend=datetime.fromisoformat(value["dtend"]),
            event_type=EventType.from_string(str(value.get("event_type", ""))),
            is_group_event=bool(value.get("is_group_event", False)),
            status=UniversityEventStatus.from_string(value.get("status")),
            sequence=int(value.get("sequence", 0)),
        )

    @staticmethod
    def _personal_to_dict(event: PersonalUniversityEvent) -> dict[str, Any]:
        return {
            "id": event.id, "title": event.title, "description": event.description,
            "start_time": event.start_time.isoformat(), "end_time": event.end_time.isoformat(),
            "location": event.location, "university_event_uid": event.university_event_uid,
            "preparation_block_uid": event.preparation_block_uid, "task_uid": event.task_uid,
            "source": event.source, "state": event.state.value,
            "match_confidence": event.match_confidence.value if event.match_confidence else None,
            "match_type": event.match_type.value if event.match_type else None,
            "metadata": event.metadata,
        }

    @staticmethod
    def _personal_from_dict(value: dict[str, Any]) -> PersonalUniversityEvent:
        confidence = value.get("match_confidence")
        match_type = value.get("match_type")
        return PersonalUniversityEvent(
            id=str(value["id"]), title=str(value["title"]),
            description=str(value.get("description", "")),
            start_time=datetime.fromisoformat(value["start_time"]),
            end_time=datetime.fromisoformat(value["end_time"]),
            location=value.get("location"), university_event_uid=value.get("university_event_uid"),
            preparation_block_uid=value.get("preparation_block_uid"), task_uid=value.get("task_uid"),
            source=value.get("source"), state=PersonalEventState(value.get("state", "expected")),
            match_confidence=AttendanceConfidence(confidence) if confidence else None,
            match_type=MatchType(match_type) if match_type else None,
            metadata=dict(value.get("metadata", {})),
        )

    def _snapshot_payload(self, events: Iterable[UniversityEvent]) -> list[dict[str, Any]]:
        return sorted((self._source_to_dict(event) for event in events), key=lambda value: value["uid"])
