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
from services.attendance_service import AttendanceRuleService, UniversityEventChangeType
from services.attendance_preferences import course_name


@dataclass(frozen=True)
class UniversityReconciliationResult:
    changed: bool
    personal_events: list[PersonalUniversityEvent]
    review_events: list[PersonalUniversityEvent]
    created_count: int
    affected_count: int


class ProtectedHistoryConflict(ValueError):
    """A source UID attempts to reuse already-published historical identity."""


class UniversitySnapshotStore:
    """Atomically persist the last verified source snapshot for one user."""

    VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    def personal_events(self) -> list[PersonalUniversityEvent]:
        return self._load()["personal_events"]

    def refresh_attendance_choices(
        self,
        attendance_service: AttendanceRuleService,
        protected_personal_ids: Iterable[str] = (),
    ) -> list[PersonalUniversityEvent]:
        """Refresh saved choices without observing a new university snapshot.

        This is safe for planning reads: it does not age disappearances or
        change any source identity, and writes only when the projection changes.
        """
        state = self._load()
        personal_events = state["personal_events"]
        protected_ids = set(protected_personal_ids)
        before = [self._personal_to_dict(event) for event in personal_events]
        self._apply_attendance_choices(
            state["source_events"],
            (event for event in personal_events if event.id not in protected_ids),
            attendance_service,
        )
        if before != [self._personal_to_dict(event) for event in personal_events]:
            self._save(state["source_events"], personal_events,
                       identity_namespace=state.get('identity_namespace'))
        return personal_events

    def reconcile(
        self,
        verified_events: Iterable[UniversityEvent],
        attendance_service: AttendanceRuleService,
        required_horizon: tuple[datetime, datetime] | None = None,
        protected_personal_ids: Iterable[str] = (),
        identity_namespace: str | None = None,
        published_personal_ids: Iterable[str] = (),
    ) -> UniversityReconciliationResult:
        """Apply a verified source snapshot without mutating the source.

        The caller must validate/download the ICS before this method.  An
        exception before ``_save`` leaves the previous snapshot intact.
        """
        new_events = list(verified_events)
        if not new_events:
            raise ValueError("A verified university snapshot must contain events")
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
        identity_aliases = state["identity_aliases"]
        published_ids = set(published_personal_ids)
        previous_namespace = state.get('identity_namespace')
        is_tpu_namespace = identity_namespace is not None and identity_namespace.startswith('tpu:')
        can_alias = is_tpu_namespace and previous_namespace in {None, identity_namespace}
        if is_tpu_namespace:
            self._apply_durable_identity_aliases(
                identity_namespace, new_events, identity_aliases
            )
        if can_alias:
            old_events = self._collapse_unpublished_exact_duplicates(
                old_events, new_events, state['personal_events'],
                published_ids | set(protected_personal_ids), identity_namespace,
                identity_aliases,
            )
            state['source_events'] = old_events
            self._alias_exact_source_identities(
                old_events, new_events, identity_namespace, identity_aliases
            )
        if len({event.uid for event in new_events}) != len(new_events):
            raise ValueError("A university snapshot contains duplicate UIDs")
        protected_ids = set(protected_personal_ids)
        preserved = [event for event in state["personal_events"] if event.id in protected_ids]
        protected_uids = {event.university_event_uid: event for event in preserved}
        for event in new_events:
            prior = protected_uids.get(event.uid)
            if prior and (event.dtstart != prior.start_time or event.dtend != prior.end_time):
                raise ProtectedHistoryConflict('Источник повторно использует ID уже начавшегося занятия с другим временем; нужен оператор.')
        personal_events = [event for event in state["personal_events"] if event.id not in protected_ids]
        matching_old = [event for event in old_events if event.uid not in protected_uids]
        matching_new = [event for event in new_events if event.uid not in protected_uids]
        self._apply_attendance_choices(matching_new, personal_events, attendance_service)
        outcome = attendance_service.reconcile_schedule_snapshots(
            matching_old,
            matching_new,
            personal_events,
        )
        # Exact/stable identity matches have already consumed their source
        # occurrences. They cannot also be alternatives for a disappeared
        # class merely because parallel groups share a timetable slot.
        records = outcome["changes"]["records"]
        ambiguous = self._ambiguous_replacement_candidates(
            [record.old_event for record in records
             if record.change_type == UniversityEventChangeType.DISAPPEARED
             and attendance_service.rule.metadata.get('attendance_preferences', {}).get(
                 course_name(record.old_event), {}).get('excluded') is not True],
            [record.new_event for record in records
             if record.change_type == UniversityEventChangeType.ADDED],
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
        for source_event in matching_new:
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
            personal.metadata.pop("replacement_rejected_by_user", None)
            try:
                personal.state = PersonalEventState(previous) if previous else PersonalEventState.NEEDS_REVIEW
            except ValueError:
                personal.state = PersonalEventState.NEEDS_REVIEW
            attendance_service._apply_source_event(personal, source_event)
            restored_count += 1

        # A move (including a regenerated UID or a changed lab slot) can
        # change the attendance choice for an existing personal event. Apply
        # it to the final projection as well as before the schedule diff.
        self._apply_attendance_choices(matching_new, personal_events, attendance_service)

        changed = self._snapshot_payload(old_events) != self._snapshot_payload(new_events)
        personal_events.extend(preserved)
        self._save(new_events, personal_events, identity_namespace=identity_namespace,
                   identity_aliases=identity_aliases)
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
        attendance_service: AttendanceRuleService | None = None,
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
        if any(event.id != personal.id and event.university_event_uid == candidate_uid
               for event in state["personal_events"]):
            raise ValueError("Replacement candidate already belongs to another personal event")

        previous_uid = personal.university_event_uid
        personal.title = source.summary
        personal.description = source.description
        personal.location = source.location
        personal.start_time = source.dtstart
        personal.end_time = source.dtend
        personal.university_event_uid = source.uid
        personal.state = PersonalEventState.MOVED
        if attendance_service is not None and attendance_service.attendance_choice(source) is not True:
            personal.state = PersonalEventState.EXPECTED
            personal.match_confidence = None
            personal.match_type = None
        personal.metadata["change_reason"] = "replacement_confirmed_by_user"
        personal.metadata["replacement_confirmed_from_uid"] = previous_uid
        personal.metadata.pop("replacement_candidate", None)
        personal.metadata.pop("replacement_candidates", None)
        self._save(state["source_events"], state["personal_events"],
                   identity_namespace=state.get('identity_namespace'))
        return personal

    def reject_replacement(self, personal_event_id: str) -> PersonalUniversityEvent:
        """Explicitly exclude a vanished old occurrence without deleting its history."""
        state = self._load()
        personal = next((event for event in state['personal_events'] if event.id == personal_event_id), None)
        if personal is None:
            raise ValueError('Replacement decision is no longer available')
        if any(event.uid == personal.university_event_uid for event in state['source_events']):
            raise ValueError('The original occurrence still exists in the current source')
        if personal.state == PersonalEventState.NEEDS_REVIEW:
            pass
        elif (personal.state in {PersonalEventState.POSSIBLY_CANCELLED, PersonalEventState.CANCELLED}
              and personal.metadata.get('change_reason') == 'missing_from_university_schedule'):
            pass
        else:
            raise ValueError('Replacement decision is no longer available')
        personal.state = PersonalEventState.CANCELLED
        personal.metadata['change_reason'] = 'missing_from_university_schedule'
        personal.metadata['replacement_rejected_by_user'] = True
        personal.metadata.pop('replacement_candidates', None)
        personal.metadata.pop('replacement_candidate', None)
        self._save(state['source_events'], state['personal_events'],
                   identity_namespace=state.get('identity_namespace'))
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
        """Apply explicit choices to active events without replacing their identity."""
        personal_by_uid = {
            event.university_event_uid: event for event in personal_events
            if event.university_event_uid
        }
        for source_event in source_events:
            personal = personal_by_uid.get(source_event.uid)
            if personal is None or source_event.is_cancelled:
                continue
            choice = attendance_service.attendance_choice(source_event)
            if choice is None:
                personal.metadata.pop("attendance_choice", None)
            else:
                personal.metadata["attendance_choice"] = choice
            if choice is False and personal.state in {
                PersonalEventState.CONFIRMED, PersonalEventState.MOVED,
            }:
                personal.state = PersonalEventState.EXPECTED
                personal.match_confidence = None
                personal.match_type = None
            elif choice is True and personal.state == PersonalEventState.EXPECTED:
                result = attendance_service.evaluate_event(source_event)
                if result.is_matched:
                    personal_id = personal.id
                    personal.apply_match_result(result)
                    # A preference confirms attendance, but the local identity
                    # and any manual links or change history remain unchanged.
                    personal.id = personal_id

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"source_events": [], "personal_events": [], "identity_namespace": None,
                    "identity_aliases": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != self.VERSION:
            raise ValueError("Unsupported university snapshot storage version")
        return {
            "source_events": [self._source_from_dict(value)
                              for value in payload.get("source_events", [])],
            "personal_events": [self._personal_from_dict(value)
                                for value in payload.get("personal_events", [])],
            "identity_namespace": payload.get('identity_namespace'),
            "identity_aliases": self._validated_identity_aliases(
                payload.get('identity_aliases', {})
            ),
        }

    def _save(
        self,
        source_events: Iterable[UniversityEvent],
        personal_events: Iterable[PersonalUniversityEvent],
        *, identity_namespace: str | None = None,
        identity_aliases: dict[str, dict[str, dict[str, str]]] | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if identity_aliases is None:
            identity_aliases = self._load().get('identity_aliases', {}) if self.path.exists() else {}
        payload = {
            "version": self.VERSION,
            "source_events": [self._source_to_dict(event) for event in source_events],
            "personal_events": [self._personal_to_dict(event) for event in personal_events],
            "identity_namespace": identity_namespace,
            "identity_aliases": identity_aliases,
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
            "attendance_slot_key": event.attendance_slot_key,
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
            attendance_slot_key=value.get("attendance_slot_key"),
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

    def _alias_exact_source_identities(
        self, old_events: Iterable[UniversityEvent], new_events: Iterable[UniversityEvent],
        identity_namespace: str, identity_aliases: dict[str, dict[str, dict[str, str]]],
    ) -> None:
        """Preserve legacy source UIDs only for exact one-to-one TPU matches."""
        old_by_semantics: dict[str, list[UniversityEvent]] = {}
        new_by_semantics: dict[str, list[UniversityEvent]] = {}
        for event in old_events:
            old_by_semantics.setdefault(self._source_semantic_key(event), []).append(event)
        for event in new_events:
            new_by_semantics.setdefault(self._source_semantic_key(event), []).append(event)
        for key in sorted(set(old_by_semantics) & set(new_by_semantics)):
            old_group = old_by_semantics[key]
            new_group = new_by_semantics[key]
            if len(old_group) != 1 or len(new_group) != 1:
                raise ProtectedHistoryConflict(
                    'Неоднозначное совпадение событий TPU при обновлении идентификаторов; нужен оператор.'
                )
            if old_group[0].uid != new_group[0].uid:
                incoming_uid = new_group[0].uid
                aliases = identity_aliases.setdefault(identity_namespace, {})
                record = {"legacy_uid": old_group[0].uid,
                          "identity": self._source_identity_key(new_group[0])}
                existing = aliases.get(incoming_uid)
                if existing is not None and existing != record:
                    raise ProtectedHistoryConflict(
                        'Конфликт сохранённой связи идентификаторов TPU; нужен оператор.'
                    )
                if any(key != incoming_uid and value.get('legacy_uid') == old_group[0].uid
                       for key, value in aliases.items()):
                    raise ProtectedHistoryConflict(
                        'Повторное использование сохранённого идентификатора TPU; нужен оператор.'
                    )
                aliases[incoming_uid] = record
                new_group[0].uid = old_group[0].uid

    def _collapse_unpublished_exact_duplicates(
        self, old_events: list[UniversityEvent], new_events: list[UniversityEvent],
        personal_events: list[PersonalUniversityEvent], published_ids: set[str],
        identity_namespace: str,
        identity_aliases: dict[str, dict[str, dict[str, str]]],
    ) -> list[UniversityEvent]:
        """Collapse exact legacy export duplicates only when no identity is published."""
        old_by_semantics: dict[str, list[UniversityEvent]] = {}
        new_by_semantics: dict[str, list[UniversityEvent]] = {}
        for event in old_events:
            old_by_semantics.setdefault(self._source_semantic_key(event), []).append(event)
        for event in new_events:
            new_by_semantics.setdefault(self._source_semantic_key(event), []).append(event)
        personal_by_source: dict[str, list[PersonalUniversityEvent]] = {}
        for event in personal_events:
            if event.university_event_uid:
                personal_by_source.setdefault(event.university_event_uid, []).append(event)

        remove_uids: set[str] = set()
        aliases = identity_aliases.setdefault(identity_namespace, {})
        for key in sorted(set(old_by_semantics) & set(new_by_semantics)):
            old_group = old_by_semantics[key]
            new_group = new_by_semantics[key]
            if len(old_group) <= 1 or len(new_group) != 1:
                continue
            affected = [personal for source_event in old_group
                        for personal in personal_by_source.get(source_event.uid, [])]
            if any(personal.id in published_ids for personal in affected):
                raise ProtectedHistoryConflict(
                    'Опубликованная история неоднозначна из-за дублирующихся экспортов TPU; нужен оператор.'
                )
            bound_old = [event for event in old_group
                         if personal_by_source.get(event.uid)]
            canonical = min(
                bound_old or old_group,
                key=lambda event: (
                    min((personal.id for personal in personal_by_source.get(event.uid, [])),
                        default=''),
                    event.uid,
                ),
            )
            incoming_uid = new_group[0].uid
            record = {'legacy_uid': canonical.uid,
                      'identity': self._source_identity_key(new_group[0])}
            existing = aliases.get(incoming_uid)
            if existing is not None and existing != record:
                raise ProtectedHistoryConflict(
                    'Конфликт сохранённой связи идентификаторов TPU; нужен оператор.'
                )
            if any(uid != incoming_uid and value.get('legacy_uid') == canonical.uid
                   for uid, value in aliases.items()):
                raise ProtectedHistoryConflict(
                    'Повторное использование сохранённого идентификатора TPU; нужен оператор.'
                )
            aliases[incoming_uid] = record
            for event in old_group:
                if event.uid == canonical.uid:
                    continue
                remove_uids.add(event.uid)
                for personal in personal_by_source.get(event.uid, []):
                    if personal.id in published_ids:
                        raise ProtectedHistoryConflict(
                            'Опубликованная история неоднозначна из-за дублирующихся экспортов TPU; нужен оператор.'
                        )
                    personal.state = PersonalEventState.CANCELLED
                    personal.metadata['source_duplicate_of'] = canonical.uid
                    personal.metadata['source_duplicate_namespace'] = identity_namespace
                    personal.metadata.pop('replacement_candidates', None)
                    personal.metadata.pop('replacement_candidate', None)
        return [event for event in old_events if event.uid not in remove_uids]

    def _apply_durable_identity_aliases(
        self, identity_namespace: str, new_events: Iterable[UniversityEvent],
        identity_aliases: dict[str, dict[str, dict[str, str]]],
    ) -> None:
        aliases = identity_aliases.get(identity_namespace, {})
        seen_targets: set[str] = set()
        for event in new_events:
            record = aliases.get(event.uid)
            if record is None:
                continue
            legacy_uid = record['legacy_uid']
            if (record['identity'] != self._source_identity_key(event)
                    or legacy_uid in seen_targets):
                raise ProtectedHistoryConflict(
                    'Сохранённая связь идентификаторов TPU больше не однозначна; нужен оператор.'
                )
            seen_targets.add(legacy_uid)
            event.uid = legacy_uid

    @staticmethod
    def _validated_identity_aliases(value: Any) -> dict[str, dict[str, dict[str, str]]]:
        if not isinstance(value, dict):
            raise ValueError('Invalid university snapshot identity aliases')
        result: dict[str, dict[str, dict[str, str]]] = {}
        for namespace, aliases in value.items():
            if not isinstance(namespace, str) or not namespace.startswith('tpu:') or not isinstance(aliases, dict):
                raise ValueError('Invalid university snapshot identity aliases')
            normalized: dict[str, dict[str, str]] = {}
            targets: set[str] = set()
            for incoming_uid, record in aliases.items():
                if (not isinstance(incoming_uid, str) or not isinstance(record, dict)
                        or not isinstance(record.get('legacy_uid'), str)
                        or not isinstance(record.get('identity'), str)
                        or record['legacy_uid'] in targets):
                    raise ValueError('Invalid or ambiguous university snapshot identity aliases')
                targets.add(record['legacy_uid'])
                normalized[incoming_uid] = {
                    'legacy_uid': record['legacy_uid'], 'identity': record['identity']
                }
            result[namespace] = normalized
        return result

    @classmethod
    def _source_identity_key(cls, event: UniversityEvent) -> str:
        row = cls._source_to_dict(event)
        for field in ('uid', 'status', 'sequence'):
            row.pop(field, None)
        return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

    @classmethod
    def _source_semantic_key(cls, event: UniversityEvent) -> str:
        row = cls._source_to_dict(event)
        row.pop('uid', None)
        return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
