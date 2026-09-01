"""
Attendance Domain Service
Encapsulates business logic for personal attendance tracking:
- Matching personal events to university events
- Attendance rule evaluation
- Stable event identification
- Change detection
- Preparation integration
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
import unicodedata
import re
from models import (
    UniversityEvent,
    PersonalUniversityEvent,
    AttendanceMatchResult,
    AttendanceConfidence,
    MatchType,
    PersonalEventState,
    PersonalAttendanceRule,
    EventType
)
from ids import IDGenerator
from services.preparation.preparation_integration_service import PreparationIntegrationService


class UniversityEventChangeType(Enum):
    """Safe, explicit classification of a university schedule change."""
    ADDED = "added"
    MOVED = "moved"
    TIME_CHANGED = "time_changed"
    LOCATION_CHANGED = "location_changed"
    UPDATED = "updated"
    REPLACED = "replaced"
    CANCELLED = "cancelled"
    DISAPPEARED = "disappeared"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class UniversityEventChange:
    """One auditable difference between two university schedule snapshots."""
    change_type: UniversityEventChangeType
    old_event: Optional[UniversityEvent] = None
    new_event: Optional[UniversityEvent] = None
    stable_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class AttendanceRuleService:
    """Service for managing attendance rules and matching personal events to university events."""

    def __init__(
        self,
        rule: Optional[PersonalAttendanceRule] = None,
        preparation_integration_service: Optional[PreparationIntegrationService] = None
    ):
        """
        Initialize the attendance rule service.

        Args:
            rule: Optional attendance rule to use for matching. If None, creates a default rule.
            preparation_integration_service: Optional service for integrating preparation with personal events.
        """
        self.rule = rule or PersonalAttendanceRule(
            id="default-attendance-rule",
            description="Default attendance matching rule"
        )
        self.IDGenerator = IDGenerator()
        self.preparation_integration_service = preparation_integration_service

    # ===== Stable ID Calculation =====

    def compute_stable_id(self, event: UniversityEvent) -> str:
        """
        Compute a stable identifier for a university event based on unchanging attributes.

        The stable ID is designed to remain constant even when:
        - Event time changes (rescheduling)
        - UID changes in the source system
        - Minor description changes occur

        Based on attributes like: course_id, session_type, instructor, lab_section.
        These are extracted from the event summary/description using heuristics.

        Args:
            event: UniversityEvent to compute stable ID for

        Returns:
            str: Stable identifier string
        """
        # Extract course information from summary
        course_id = self._extract_course_id(event.summary)

        # Extract session type from event_type and summary
        session_type = self._extract_session_type(event)

        # Extract instructor information
        instructor = self._extract_instructor(event.description)

        # Extract lab/practical section information
        lab_section = self._extract_lab_section(event.summary, event.description)

        # Combine components into stable ID
        components = [
            course_id or "unknown_course",
            session_type or "unknown_session",
            instructor or "unknown_instructor",
            lab_section or "unknown_section"
        ]

        # Keep Unicode letters. The old ASCII-only cleanup erased Cyrillic
        # course names and caused unrelated events to share the same ID.
        stable_id = unicodedata.normalize("NFKC", "_".join(components)).casefold()
        stable_id = re.sub(r'[^\w]+', '_', stable_id, flags=re.UNICODE).strip('_')

        return stable_id or "unknown_event"

    def _extract_course_id(self, summary: str) -> Optional[str]:
        """Extract course ID from event summary."""
        # Common patterns: "Математический анализ", "Программирование", "Физика"
        # Look for course codes or names
        # Simple implementation - extract first meaningful word(s)
        words = summary.split()
        # Filter out common event type markers
        filtered_words = [w for w in words if w not in ['(ЛК)', '(ЛБ)', '(ПР)', '(СЕМ)', '(ВШ)', '(ЭКЗ)', 'лекция', 'лаб', 'практик', 'семин', 'воркшоп', 'экзамен'] and (len(w) > 2 or (len(w) >= 2 and w.isupper()))]
        if filtered_words:
            # Return first long enough word as course ID
            for word in filtered_words:
                if len(word) >= 2:
                    return word
        return None

    def _extract_session_type(self, event: UniversityEvent) -> Optional[str]:
        """Extract session type from event."""
        type_mapping = {
            EventType.LECTURE: 'lecture',
            EventType.LAB: 'lab',
            EventType.PRACTICAL: 'practical',
            EventType.SEMINAR: 'seminar',
            EventType.WORKSHOP: 'workshop',
            EventType.EXAM: 'exam'
        }
        return type_mapping.get(event.event_type, None)

    def _extract_instructor(self, description: str) -> Optional[str]:
        """Extract instructor from event description."""
        if not description:
            return None
        # Look for patterns like "Преподаватель: Иванов И.И." or "Преподаватель: Petrov"
        import re
        # Pattern for "Преподаватель: NAME"
        instructor_match = re.search(r'[Пп]реподаватель[:\s]+([А-Яа-яA-Za-z\s\.\-]+)', description)
        if instructor_match:
            return instructor_match.group(1).strip()
        # Pattern for "Преп. NAME"
        instructor_match2 = re.search(r'[Пп]реп[\.\s]+([А-Яа-яA-Za-z\s\.\-]+)', description)
        if instructor_match2:
            return instructor_match2.group(1).strip()
        return None

    def _extract_lab_section(self, summary: str, description: str) -> Optional[str]:
        """Extract lab/practical section from summary or description."""
        if not summary and not description:
            return None
        text = f"{summary} {description}".strip()
        # Look for patterns like "Группа: 8И41-1" or "Секция: А"
        import re
        # Pattern for "Группа: NUMBER-LETTER"
        group_match = re.search(r'[Гг]руппа[:\s]+([А-Яа-я0-9\-\s]+)', text)
        if group_match:
            return group_match.group(1).strip()
        # Pattern for "Секция: LETTER"
        section_match = re.search(r'[Сс]екц[ии][я]?[:\s]+([А-Яа-я])', text)
        if section_match:
            return section_match.group(1).strip()
        return None

    # ===== Private helper methods for event evaluation (used in tests) =====

    def _check_exact_uid_match(self, event: UniversityEvent) -> Optional[AttendanceMatchResult]:
        """
        Check for exact UID match with personal events.
        This is a stub for testing purposes - returns None by default.
        """
        return None

    def _check_stable_id_match(self, event: UniversityEvent) -> Optional[AttendanceMatchResult]:
        """
        Check for stable ID match with personal events.
        This is a stub for testing purposes - returns None by default.
        """
        return None

    def _check_fuzzy_match(self, event: UniversityEvent) -> Optional[AttendanceMatchResult]:
        """
        Check for fuzzy match with personal events.
        This is a stub for testing purposes - returns None by default.
        """
        return None

    # ===== Event Evaluation/Matching =====

    def evaluate_event(self, event: UniversityEvent) -> AttendanceMatchResult:
        """
        Evaluate a university event against attendance rules to determine if it matches
        any personal events and what the match quality is.

        Args:
            event: UniversityEvent to evaluate

        Returns:
            AttendanceMatchResult: Result of the evaluation
        """
        attendance_preferences = self.rule.metadata.get(
            'attendance_preferences', {}
        )
        course = re.sub(r'\s*\([^)]*\)\s*$', '', event.summary).strip()
        preference = attendance_preferences.get(course, {})
        session_key = {
            EventType.LECTURE: 'lectures',
            EventType.PRACTICAL: 'practicals',
        }.get(event.event_type)
        attends = preference.get(session_key) if session_key else None
        if event.event_type == EventType.LAB and preference:
            slot_key = f'{event.dtstart.weekday()}:{event.dtstart:%H:%M}'
            if preference.get('labs_enabled') is False:
                attends = False
            elif preference.get('labs_enabled') is True:
                attends = slot_key in preference.get('lab_slots', [])
        if attends is False:
            return AttendanceMatchResult(
                personal_event_id=None,
                university_event_uid=event.uid,
                match_type=MatchType.NO_MATCH,
                confidence=AttendanceConfidence.VERY_HIGH,
                explanation='Excluded by the personal attendance preference',
            )

        # First try exact UID match
        exact_match = self._check_exact_uid_match(event)
        if exact_match is not None:
            return exact_match

        # Then try stable ID match
        stable_match = self._check_stable_id_match(event)
        if stable_match is not None:
            return stable_match

        # Then try fuzzy match
        fuzzy_match = self._check_fuzzy_match(event)
        if fuzzy_match is not None:
            return fuzzy_match

        # Fallback to basic matching logic
        # Extract information for potential matching
        course_id = self._extract_course_id(event.summary)
        session_type = self._extract_session_type(event)
        instructor = self._extract_instructor(event.description)
        lab_section = self._extract_lab_section(event.summary, event.description)

        # Simple matching logic: if we have course_id and session_type, consider it a match
        if course_id and session_type:
            # Generate a fake personal event ID for demo
            personal_event_id = f"personal_{course_id}_{session_type}"
            return AttendanceMatchResult(
                personal_event_id=personal_event_id,
                university_event_uid=event.uid,
                match_type=MatchType.EXACT,
                confidence=AttendanceConfidence.HIGH,
                explanation=f"Matched by course '{course_id}' and session '{session_type}'"
            )
        else:
            # No match found
            return AttendanceMatchResult(
                personal_event_id=None,
                university_event_uid=event.uid,
                match_type=MatchType.NO_MATCH,
                confidence=AttendanceConfidence.VERY_LOW,
                explanation="No matching criteria found"
            )

    # ===== Finding Personal Events =====

    def find_personal_events(self, university_events: List[UniversityEvent]) -> List[PersonalUniversityEvent]:
        """
        Convert university events to personal events based on attendance rules.

        Args:
            university_events: List of university events to process

        Returns:
            List[PersonalUniversityEvent]: List of personal events (matched or pending)
        """
        personal_events = []
        for event in university_events:
            match_result = self.evaluate_event(event)

            # Create personal university event
            personal_event = PersonalUniversityEvent(
                id=match_result.personal_event_id or self.IDGenerator.generate_uid(),
                title=event.summary,
                description=event.description,
                start_time=event.dtstart,
                end_time=event.dtend,
                location=event.location,
                university_event_uid=event.uid,
                state=PersonalEventState.EXPECTED,  # Default state
                match_confidence=match_result.confidence if match_result.personal_event_id else None,
                match_type=match_result.match_type if match_result.personal_event_id else None,
                metadata={
                    'stable_id': self.compute_stable_id(event),
                    'course': self._extract_course_id(event.summary),
                    'session_type': self._extract_session_type(event),
                    'source_sequence': getattr(event, 'sequence', 0),
                    'source_status': getattr(event.status, 'value', 'confirmed'),
                }
            )

            # If we have a match, update the state accordingly
            if match_result.personal_event_id:
                # Apply the match result to determine personal event state
                personal_event.apply_match_result(match_result)

            personal_events.append(personal_event)

        return personal_events

    # ===== Change Detection =====

    def detect_changes(
        self,
        old_events: List[UniversityEvent],
        new_events: List[UniversityEvent]
    ) -> Dict[str, Any]:
        """Backward-compatible snapshot diff with rich change records."""
        records = self.detect_schedule_changes(old_events, new_events)
        return {
            'added': [r.new_event for r in records
                      if r.change_type == UniversityEventChangeType.ADDED and r.new_event],
            'removed': [r.old_event for r in records
                        if r.change_type in (
                            UniversityEventChangeType.CANCELLED,
                            UniversityEventChangeType.DISAPPEARED,
                        ) and r.old_event],
            'changed': [r.new_event for r in records
                        if r.change_type in (
                            UniversityEventChangeType.MOVED,
                            UniversityEventChangeType.TIME_CHANGED,
                            UniversityEventChangeType.LOCATION_CHANGED,
                            UniversityEventChangeType.UPDATED,
                            UniversityEventChangeType.REPLACED,
                        ) and r.new_event],
            'unchanged': [r.new_event for r in records
                          if r.change_type == UniversityEventChangeType.UNCHANGED and r.new_event],
            'records': records,
        }

    def detect_schedule_changes(
        self,
        old_events: List[UniversityEvent],
        new_events: List[UniversityEvent]
    ) -> List[UniversityEventChange]:
        """
        Compare complete snapshots without collapsing repeated sessions.

        UID is authoritative. If a source regenerates a UID, occurrences are
        paired by stable identity and nearest time. A different event occupying
        the same unique slot is classified as a replacement, not silently
        accepted as the old event.
        """
        records: List[UniversityEventChange] = []
        unmatched_old = {event.uid: event for event in old_events}
        unmatched_new = {event.uid: event for event in new_events}

        # 1. Match by source UID first.
        for uid in sorted(set(unmatched_old) & set(unmatched_new)):
            old_event = unmatched_old.pop(uid)
            new_event = unmatched_new.pop(uid)
            records.append(self._classify_event_pair(old_event, new_event))

        # 2. Match regenerated UIDs by stable identity and nearest occurrence.
        old_by_stable: Dict[str, List[UniversityEvent]] = {}
        new_by_stable: Dict[str, List[UniversityEvent]] = {}
        for event in unmatched_old.values():
            old_by_stable.setdefault(self.compute_stable_id(event), []).append(event)
        for event in unmatched_new.values():
            new_by_stable.setdefault(self.compute_stable_id(event), []).append(event)

        for stable_id in sorted(set(old_by_stable) & set(new_by_stable)):
            old_group = sorted(old_by_stable[stable_id], key=lambda event: event.dtstart)
            new_group = sorted(new_by_stable[stable_id], key=lambda event: event.dtstart)
            while old_group and new_group:
                old_event, new_event = min(
                    ((old_event, new_event)
                     for old_event in old_group for new_event in new_group),
                    key=lambda pair: abs((pair[0].dtstart - pair[1].dtstart).total_seconds())
                )
                old_group.remove(old_event)
                new_group.remove(new_event)
                unmatched_old.pop(old_event.uid, None)
                unmatched_new.pop(new_event.uid, None)
                records.append(self._classify_event_pair(old_event, new_event))

        # 3. Detect a unique replacement in the same timetable slot. Ambiguous
        # candidates remain DISAPPEARED + ADDED and therefore require review.
        replacement_pairs = self._find_replacement_pairs(
            list(unmatched_old.values()), list(unmatched_new.values())
        )
        for old_event, new_event in replacement_pairs:
            unmatched_old.pop(old_event.uid, None)
            unmatched_new.pop(new_event.uid, None)
            records.append(UniversityEventChange(
                change_type=UniversityEventChangeType.REPLACED,
                old_event=old_event,
                new_event=new_event,
                stable_id=self.compute_stable_id(old_event),
                details=self._event_difference_details(old_event, new_event),
            ))

        # 4. Explicit source cancellation is distinct from disappearance.
        for old_event in unmatched_old.values():
            records.append(UniversityEventChange(
                change_type=UniversityEventChangeType.DISAPPEARED,
                old_event=old_event,
                stable_id=self.compute_stable_id(old_event),
                details={'reason': 'missing_from_snapshot'},
            ))
        for new_event in unmatched_new.values():
            change_type = (
                UniversityEventChangeType.CANCELLED
                if getattr(new_event, 'is_cancelled', False)
                else UniversityEventChangeType.ADDED
            )
            records.append(UniversityEventChange(
                change_type=change_type,
                new_event=new_event,
                stable_id=self.compute_stable_id(new_event),
                details={'reason': 'explicit_source_status'} if change_type == UniversityEventChangeType.CANCELLED else {},
            ))

        return records

    def _classify_event_pair(
        self,
        old_event: UniversityEvent,
        new_event: UniversityEvent
    ) -> UniversityEventChange:
        details = self._event_difference_details(old_event, new_event)
        stable_id = self.compute_stable_id(old_event)

        if getattr(new_event, 'is_cancelled', False):
            change_type = UniversityEventChangeType.CANCELLED
        elif details.get('course_changed') or details.get('event_type_changed'):
            change_type = UniversityEventChangeType.REPLACED
        elif details.get('start_changed'):
            change_type = UniversityEventChangeType.MOVED
        elif details.get('end_changed'):
            change_type = UniversityEventChangeType.TIME_CHANGED
        elif details.get('location_changed'):
            change_type = UniversityEventChangeType.LOCATION_CHANGED
        elif any(details.values()):
            change_type = UniversityEventChangeType.UPDATED
        else:
            change_type = UniversityEventChangeType.UNCHANGED

        return UniversityEventChange(
            change_type=change_type,
            old_event=old_event,
            new_event=new_event,
            stable_id=stable_id,
            details=details,
        )

    def _event_difference_details(
        self,
        old_event: UniversityEvent,
        new_event: UniversityEvent
    ) -> Dict[str, bool]:
        return {
            'uid_changed': old_event.uid != new_event.uid,
            'start_changed': old_event.dtstart != new_event.dtstart,
            'end_changed': old_event.dtend != new_event.dtend,
            'location_changed': old_event.location != new_event.location,
            'title_changed': old_event.summary_normalized != new_event.summary_normalized,
            'description_changed': old_event.description != new_event.description,
            'course_changed': (
                self._extract_course_id(old_event.summary)
                != self._extract_course_id(new_event.summary)
            ),
            'event_type_changed': old_event.event_type != new_event.event_type,
            'instructor_changed': (
                self._extract_instructor(old_event.description)
                != self._extract_instructor(new_event.description)
            ),
            'section_changed': (
                self._extract_lab_section(old_event.summary, old_event.description)
                != self._extract_lab_section(new_event.summary, new_event.description)
            ),
            'sequence_changed': getattr(old_event, 'sequence', 0) != getattr(new_event, 'sequence', 0),
        }

    def _find_replacement_pairs(
        self,
        old_events: List[UniversityEvent],
        new_events: List[UniversityEvent]
    ) -> List[Tuple[UniversityEvent, UniversityEvent]]:
        """Return only unambiguous old/new events occupying the same slot."""
        candidates: Dict[str, List[UniversityEvent]] = {}
        reverse_candidates: Dict[str, List[UniversityEvent]] = {}

        for old_event in old_events:
            for new_event in new_events:
                same_start = abs((old_event.dtstart - new_event.dtstart).total_seconds()) <= 15 * 60
                overlaps = old_event.dtstart < new_event.dtend and new_event.dtstart < old_event.dtend
                same_group_scope = old_event.is_group_event == new_event.is_group_event
                if (same_start or overlaps) and same_group_scope:
                    candidates.setdefault(old_event.uid, []).append(new_event)
                    reverse_candidates.setdefault(new_event.uid, []).append(old_event)

        pairs = []
        for old_event in old_events:
            possible_new = candidates.get(old_event.uid, [])
            if len(possible_new) != 1:
                continue
            new_event = possible_new[0]
            if len(reverse_candidates.get(new_event.uid, [])) == 1:
                pairs.append((old_event, new_event))
        return pairs

    def _events_have_significant_changes(self, old_event: UniversityEvent, new_event: UniversityEvent) -> bool:
        """
        Determine if two events have significant changes that require update.

        Args:
            old_event: Previous version of the event
            new_event: Current version of the event

        Returns:
            bool: True if events have significant changes, False otherwise
        """
        # Significant changes: time changes, location changes, or event type changes
        # Minor changes: description updates (unless they affect extracted data)

        details = self._event_difference_details(old_event, new_event)
        # Preserve the legacy five-minute tolerance for this compatibility
        # predicate. The rich change detector still records exact time changes.
        if details['start_changed']:
            details['start_changed'] = abs(
                (old_event.dtstart - new_event.dtstart).total_seconds()
            ) > 5 * 60
        if details['end_changed']:
            details['end_changed'] = abs(
                (old_event.dtend - new_event.dtend).total_seconds()
            ) > 5 * 60
        return any(details.values())

    # ===== Preparation Integration =====

    def reconcile_schedule_snapshots(
        self,
        old_events: List[UniversityEvent],
        new_events: List[UniversityEvent],
        personal_events: List[PersonalUniversityEvent],
        disappearance_grace_syncs: int = 2,
    ) -> Dict[str, Any]:
        """Run change detection and safely propagate the result downstream."""
        changes = self.detect_changes(old_events, new_events)
        affected = self.process_removed_and_changed_events(
            changes,
            personal_events,
            disappearance_grace_syncs=disappearance_grace_syncs,
        )

        existing_source_uids = {
            event.university_event_uid for event in personal_events
            if event.university_event_uid
        }
        added_sources = [
            event for event in changes['added']
            if event.uid not in existing_source_uids and not event.is_cancelled
        ]
        created = self.find_personal_events(added_sources)
        personal_events.extend(created)

        for personal_event in [*affected, *created]:
            if personal_event.state in (
                PersonalEventState.CONFIRMED,
                PersonalEventState.MOVED,
            ):
                self.prepare_for_event(personal_event)
            elif (
                personal_event.state == PersonalEventState.CANCELLED
                and self.preparation_integration_service is not None
            ):
                self.preparation_integration_service.cancel_preparation(
                    personal_event
                )

        return {
            'changes': changes,
            'affected': affected,
            'created': created,
            'personal_events': personal_events,
        }

    def prepare_for_event(self, personal_event: PersonalUniversityEvent) -> Optional[bool]:
        """
        Prepare for a personal university event by creating preparation blocks.
        Preparation is created only for CONFIRMED or MOVED events.

        Args:
            personal_event: The personal university event to prepare for

        Returns:
            bool: True if preparation was integrated, None if not applicable
        """
        # Only integrate preparation for confirmed or moved events that have a linked university event
        if self.preparation_integration_service and personal_event.state in (PersonalEventState.CONFIRMED, PersonalEventState.MOVED) and personal_event.university_event_uid is not None:
            self.preparation_integration_service.integrate_preparation(personal_event)
            return True
        return None

    # ===== Safe Change Handling (Goal #5) =====

    def process_removed_and_changed_events(
        self,
        changes: Dict[str, Any],
        personal_events: List[PersonalUniversityEvent],
        disappearance_grace_syncs: int = 2,
    ) -> List[PersonalUniversityEvent]:
        """
        Apply a schedule diff to personal events without unsafe deletion.

        A missing source event is first marked POSSIBLY_CANCELLED. Only an
        explicit source cancellation, or repeated absence after the configured
        grace count, moves it to CANCELLED. Replacements always require review.
        """
        records = changes.get('records')
        if records is None:
            # Compatibility for callers constructing the legacy dictionary.
            records = [
                UniversityEventChange(
                    UniversityEventChangeType.DISAPPEARED,
                    old_event=event,
                    stable_id=self.compute_stable_id(event),
                    details={'reason': 'missing_from_snapshot'},
                )
                for event in changes.get('removed', [])
            ]
            changed_uids = {event.uid: event for event in changes.get('changed', [])}
            for personal_event in personal_events:
                new_event = changed_uids.get(personal_event.university_event_uid)
                if new_event:
                    change_type = (
                        UniversityEventChangeType.MOVED
                        if personal_event.start_time != new_event.dtstart
                        else UniversityEventChangeType.LOCATION_CHANGED
                        if personal_event.location != new_event.location
                        else UniversityEventChangeType.UPDATED
                    )
                    records.append(UniversityEventChange(
                        change_type,
                        new_event=new_event,
                        stable_id=self.compute_stable_id(new_event),
                    ))

        return self.apply_schedule_changes(
            records,
            personal_events,
            disappearance_grace_syncs=disappearance_grace_syncs,
        )

    def apply_schedule_changes(
        self,
        records: List[UniversityEventChange],
        personal_events: List[PersonalUniversityEvent],
        disappearance_grace_syncs: int = 2,
    ) -> List[PersonalUniversityEvent]:
        """Apply typed changes in-place and return the affected personal events."""
        if disappearance_grace_syncs < 1:
            raise ValueError("disappearance_grace_syncs must be at least 1")

        personal_by_uid = {
            event.university_event_uid: event
            for event in personal_events
            if event.university_event_uid
        }
        affected: List[PersonalUniversityEvent] = []

        for record in records:
            source_uid = (
                record.old_event.uid if record.old_event
                else record.new_event.uid if record.new_event
                else None
            )
            personal_event = personal_by_uid.get(source_uid)
            if personal_event is None:
                continue

            change_type = record.change_type
            old_state = personal_event.state

            if change_type == UniversityEventChangeType.DISAPPEARED:
                missing_count = int(personal_event.metadata.get('missing_sync_count', 0)) + 1
                personal_event.metadata['missing_sync_count'] = missing_count
                personal_event.metadata.setdefault('state_before_disappearance', old_state.value)
                personal_event.metadata['change_reason'] = 'missing_from_university_schedule'
                personal_event.state = (
                    PersonalEventState.CANCELLED
                    if missing_count >= disappearance_grace_syncs
                    else PersonalEventState.POSSIBLY_CANCELLED
                )
            elif change_type == UniversityEventChangeType.CANCELLED:
                personal_event.state = PersonalEventState.CANCELLED
                personal_event.metadata['change_reason'] = 'explicit_source_cancellation'
                personal_event.metadata['preparation_action'] = 'cancel'
                self._clear_disappearance_metadata(personal_event)
            elif change_type == UniversityEventChangeType.REPLACED:
                personal_event.state = PersonalEventState.NEEDS_REVIEW
                personal_event.metadata['change_reason'] = 'replacement_requires_confirmation'
                if record.new_event:
                    personal_event.metadata['replacement_candidate'] = {
                        'uid': record.new_event.uid,
                        'title': record.new_event.summary,
                        'start_time': record.new_event.dtstart.isoformat(),
                        'end_time': record.new_event.dtend.isoformat(),
                        'location': record.new_event.location,
                    }
            elif change_type in (
                UniversityEventChangeType.MOVED,
                UniversityEventChangeType.TIME_CHANGED,
                UniversityEventChangeType.LOCATION_CHANGED,
                UniversityEventChangeType.UPDATED,
                UniversityEventChangeType.UNCHANGED,
            ):
                self._restore_after_reappearance(personal_event)
                if record.new_event:
                    self._apply_source_event(personal_event, record.new_event)
                    if record.old_event and record.old_event.uid != record.new_event.uid:
                        personal_by_uid.pop(record.old_event.uid, None)
                        personal_by_uid[record.new_event.uid] = personal_event

                if change_type in (
                    UniversityEventChangeType.MOVED,
                    UniversityEventChangeType.TIME_CHANGED,
                ) and personal_event.state != PersonalEventState.CANCELLED:
                    personal_event.state = PersonalEventState.MOVED
                    personal_event.metadata['preparation_action'] = 'reschedule'
                elif change_type == UniversityEventChangeType.LOCATION_CHANGED:
                    personal_event.metadata['change_reason'] = 'location_changed'

            if change_type != UniversityEventChangeType.UNCHANGED:
                self._append_change_history(personal_event, record, old_state)
                affected.append(personal_event)

        return affected

    def _apply_source_event(
        self,
        personal_event: PersonalUniversityEvent,
        source_event: UniversityEvent,
    ) -> None:
        personal_event.title = source_event.summary
        personal_event.description = source_event.description
        personal_event.location = source_event.location
        personal_event.start_time = source_event.dtstart
        personal_event.end_time = source_event.dtend
        personal_event.university_event_uid = source_event.uid
        personal_event.metadata['stable_id'] = self.compute_stable_id(source_event)
        personal_event.metadata['course'] = self._extract_course_id(source_event.summary)
        personal_event.metadata['session_type'] = self._extract_session_type(source_event)
        personal_event.metadata['source_sequence'] = getattr(source_event, 'sequence', 0)

    def _restore_after_reappearance(self, personal_event: PersonalUniversityEvent) -> None:
        previous_state = personal_event.metadata.pop('state_before_disappearance', None)
        personal_event.metadata.pop('missing_sync_count', None)
        if personal_event.state == PersonalEventState.POSSIBLY_CANCELLED and previous_state:
            try:
                personal_event.state = PersonalEventState(previous_state)
            except ValueError:
                personal_event.state = PersonalEventState.NEEDS_REVIEW

    def _clear_disappearance_metadata(self, personal_event: PersonalUniversityEvent) -> None:
        personal_event.metadata.pop('missing_sync_count', None)
        personal_event.metadata.pop('state_before_disappearance', None)

    def _append_change_history(
        self,
        personal_event: PersonalUniversityEvent,
        record: UniversityEventChange,
        old_state: PersonalEventState,
    ) -> None:
        history = personal_event.metadata.setdefault('schedule_change_history', [])
        history.append({
            'type': record.change_type.value,
            'old_uid': record.old_event.uid if record.old_event else None,
            'new_uid': record.new_event.uid if record.new_event else None,
            'old_start': record.old_event.dtstart.isoformat() if record.old_event else None,
            'new_start': record.new_event.dtstart.isoformat() if record.new_event else None,
            'old_end': record.old_event.dtend.isoformat() if record.old_event else None,
            'new_end': record.new_event.dtend.isoformat() if record.new_event else None,
            'old_location': record.old_event.location if record.old_event else None,
            'new_location': record.new_event.location if record.new_event else None,
            'old_state': old_state.value,
            'new_state': personal_event.state.value,
            'details': dict(record.details),
            'processed_at': datetime.now().isoformat(),
        })


# Use case demonstration
def demonstrate_attendance_service():
    """Demonstrate use cases for the attendance service."""
    print("=== Attendance Domain Service Use Cases ===\n")

    # Initialize service
    service = AttendanceRuleService()

    # Use Case 1: Stable ID Computation
    print("Use Case 1: Stable ID Computation")
    from datetime import datetime

    # Create sample university events
    lecture_event = service.IDGenerator.generate_uid()  # Just for demo, we'll create a proper event below
    lecture_event = UniversityEvent(
        uid="lecture-math-101-20260901",
        summary="Математический анализ лекция",
        description="Лекция по пределам и непрерывности. Преподаватель: Иванов И.И.",
        location="Аудитория 205",
        dtstart=datetime(2026, 9, 1, 10, 0),
        dtend=datetime(2026, 9, 1, 11, 30),
        event_type=EventType.LECTURE,
        is_group_event=True
    )

    lab_event = UniversityEvent(
        uid="lab-prog-201-20260901",
        summary="Программирование лабораторная работа",
        description="Лабораторная работа: основы алгоритмизации. Преподаватель: Петрова А.А. Группа: 8И41-1",
        location="Компьютерный класс 101",
        dtstart=datetime(2026, 9, 1, 14, 0),
        dtend=datetime(2026, 9, 1, 16, 0),
        event_type=EventType.LAB,
        is_group_event=True
    )

    events = [lecture_event, lab_event]

    for event in events:
        stable_id = service.compute_stable_id(event)
        print(f"  Event: {event.summary}")
        print(f"    UID: {event.uid}")
        print(f"    Stable ID: {stable_id}")
        print(f"    Course ID: {service._extract_course_id(event.summary)}")
        print(f"    Session Type: {service._extract_session_type(event)}")
        print(f"    Instructor: {service._extract_instructor(event.description)}")
        print(f"    Lab Section: {service._extract_lab_section(event.summary, event.description)}")
        print()

    # Use Case 2: Event evaluation/matching
    print("Use Case 2: Event Evaluation and Matching")
    for event in events:
        match_result = service.evaluate_event(event)
        print(f"  Event: {event.summary}")
        print(f"    Match Type: {match_result.match_type.value}")
        print(f"    Confidence: {match_result.confidence.value}")
        print(f"    Explanation: {match_result.explanation}")
        print(f"    Is Matched: {match_result.is_matched}")
        print()

    # Use Case 3: Finding personal events from university events
    print("Use Case 3: Finding Personal Events")
    personal_events = service.find_personal_events(events)
    print(f"  Found {len(personal_events)} personal events:")
    for pe in personal_events:
        status = "MATCHED" if pe.is_matched else "PENDING"
        print(f"    - {pe.title} [{status}]")
        if pe.is_matched:
            print(f"      Confidence: {pe.match_confidence.value if pe.match_confidence else 'N/A'}")
            print(f"      Match Type: {pe.match_type.value if pe.match_type else 'N/A'}")
    print()

    # Use Case 4: Change detection
    print("Use Case 4: Change Detection")
    # Simulate old events (same as current)
    old_events = events.copy()

    # Simulate new events with one change (rescheduled lecture)
    import copy
    new_events = copy.deepcopy(events)
    # Reschedule lecture by 1 hour
    new_events[0].dtstart = datetime(2026, 9, 1, 11, 0)  # Was 10:00, now 11:00
    new_events[0].dtend = datetime(2026, 9, 1, 12, 30)   # Was 11:30, now 12:30

    changes = service.detect_changes(old_events, new_events)
    print(f"  Changes detected:")
    print(f"    Added: {len(changes['added'])}")
    print(f"    Removed: {len(changes['removed'])}")
    print(f"    Changed: {len(changes['changed'])}")
    print(f"    Unchanged: {len(changes['unchanged'])}")

    if changes['changed']:
        print(f"  Changed events:")
        for event in changes['changed']:
            print(f"    - {event.summary} (rescheduled)")

    print()
    print("✓ All attendance domain use cases demonstrated successfully!")


if __name__ == "__main__":
    demonstrate_attendance_service()
