"""
Unit tests for personal attendance domain models.
"""

import pytest
from datetime import datetime, timedelta
from typing import List, Tuple

from models import (
    AttendanceConfidence,
    MatchType,
    PersonalEventState,
    PersonalAttendanceRule,
    PersonalUniversityEvent,
    AttendanceMatchResult
)


def test_attendance_confidence_enum():
    """Test AttendanceConfidence enum values."""
    assert AttendanceConfidence.LOW.value == "low"
    assert AttendanceConfidence.MEDIUM.value == "medium"
    assert AttendanceConfidence.HIGH.value == "high"
    assert AttendanceConfidence.VERY_HIGH.value == "very_high"

    # Test enum membership
    assert AttendanceConfidence.LOW in AttendanceConfidence
    assert AttendanceConfidence.MEDIUM in AttendanceConfidence
    assert AttendanceConfidence.HIGH in AttendanceConfidence
    assert AttendanceConfidence.VERY_HIGH in AttendanceConfidence


def test_match_type_enum():
    """Test MatchType enum values."""
    assert MatchType.EXACT.value == "exact"
    assert MatchType.PARTIAL_TIME.value == "partial_time"
    assert MatchType.TITLE_ONLY.value == "title_only"
    assert MatchType.FUZZY.value == "fuzzy"
    assert MatchType.NO_MATCH.value == "no_match"


def test_personal_event_state_enum():
    """Test PersonalEventState enum values."""
    assert PersonalEventState.EXPECTED.value == "expected"
    assert PersonalEventState.CONFIRMED.value == "confirmed"
    assert PersonalEventState.MOVED.value == "moved"
    assert PersonalEventState.CANCELLED.value == "cancelled"
    assert PersonalEventState.POSSIBLY_CANCELLED.value == "possibly_cancelled"
    assert PersonalEventState.NEEDS_REVIEW.value == "needs_review"


def test_personal_attendance_rule_creation():
    """Test creating PersonalAttendanceRule instances."""
    rule = PersonalAttendanceRule(
        id="rule-1",
        description="Standard attendance matching rule"
    )

    assert rule.id == "rule-1"
    assert rule.description == "Standard attendance matching rule"
    assert rule.time_tolerance_minutes == 15
    assert rule.title_similarity_threshold == 0.8
    assert rule.min_confidence_for_match == AttendanceConfidence.MEDIUM
    assert rule.require_exact_event_type is False
    assert rule.metadata == {}


def test_personal_attendance_rule_custom_values():
    """Test creating PersonalAttendanceRule with custom values."""
    rule = PersonalAttendanceRule(
        id="strict-rule",
        description="Strict matching rule",
        time_tolerance_minutes=5,
        title_similarity_threshold=0.9,
        min_confidence_for_match=AttendanceConfidence.HIGH,
        require_exact_event_type=True,
        metadata={"version": "1.0"}
    )

    assert rule.id == "strict-rule"
    assert rule.time_tolerance_minutes == 5
    assert rule.title_similarity_threshold == 0.9
    assert rule.min_confidence_for_match == AttendanceConfidence.HIGH
    assert rule.require_exact_event_type is True
    assert rule.metadata == {"version": "1.0"}


def test_personal_attendance_rule_validation():
    """Test validation of PersonalAttendanceRule parameters."""
    # Test invalid title_similarity_threshold
    with pytest.raises(ValueError, match="Title similarity threshold must be between 0.0 and 1.0"):
        PersonalAttendanceRule(
            id="invalid-rule",
            description="Invalid rule",
            title_similarity_threshold=1.5
        )

    with pytest.raises(ValueError, match="Title similarity threshold must be between 0.0 and 1.0"):
        PersonalAttendanceRule(
            id="invalid-rule",
            description="Invalid rule",
            title_similarity_threshold=-0.1
        )

    # Test invalid time_tolerance_minutes
    with pytest.raises(ValueError, match="Time tolerance must be non-negative"):
        PersonalAttendanceRule(
            id="invalid-rule",
            description="Invalid rule",
            time_tolerance_minutes=-5
        )


def test_personal_university_event_creation():
    """Test creating PersonalUniversityEvent instances."""
    start_time = datetime(2026, 9, 1, 10, 0)
    end_time = datetime(2026, 9, 1, 12, 0)

    event = PersonalUniversityEvent(
        id="personal-event-1",
        title="Математический анализ",
        description="Лекция по математическому анализу",
        start_time=start_time,
        end_time=end_time,
        location="Аудитория 205",
        source="telegram",
        university_event_uid="univ-event-123",
        preparation_block_uid="prep-456",
        task_uid="task-789",
        state=PersonalEventState.CONFIRMED,
        match_confidence=AttendanceConfidence.HIGH,
        match_type=MatchType.EXACT,
        metadata={"importance": "high"}
    )

    assert event.id == "personal-event-1"
    assert event.title == "Математический анализ"
    assert event.description == "Лекция по математическому анализу"
    assert event.start_time == start_time
    assert event.end_time == end_time
    assert event.location == "Аудитория 205"
    assert event.source == "telegram"
    assert event.university_event_uid == "univ-event-123"
    assert event.preparation_block_uid == "prep-456"
    assert event.task_uid == "task-789"
    assert event.state == PersonalEventState.CONFIRMED
    assert event.match_confidence == AttendanceConfidence.HIGH
    assert event.match_type == MatchType.EXACT
    assert event.metadata == {"importance": "high"}


def test_personal_university_event_duration_minutes():
    """Test duration_minutes property."""
    start_time = datetime(2026, 9, 1, 10, 0)
    end_time = datetime(2026, 9, 1, 12, 30)

    event = PersonalUniversityEvent(
        id="duration-test",
        title="Тестовое событие",
        description="Проверка продолжительности",
        start_time=start_time,
        end_time=end_time
    )

    assert event.duration_minutes == 150  # 2.5 hours


def test_personal_university_event_validation():
    """Test validation of PersonalUniversityEvent parameters."""
    start_time = datetime(2026, 9, 1, 12, 0)
    end_time = datetime(2026, 9, 1, 10, 0)  # End before start

    with pytest.raises(ValueError):
        PersonalUniversityEvent(
            id="invalid-event",
            title="Неправильное событие",
            description="Конца раньше начала",
            start_time=start_time,
            end_time=end_time
        )


def test_personal_university_event_is_matched():
    """Test is_matched method."""
    start_time = datetime(2026, 9, 1, 10, 0)
    end_time = datetime(2026, 9, 1, 12, 0)

    # Test unmatched event (EXPECTED state, no UID)
    unmatched_event = PersonalUniversityEvent(
        id="unmatched",
        title="Несопоставленное событие",
        description="Еще не сопоставлено",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.EXPECTED
    )
    assert unmatched_event.is_matched() is False

    # Test matched event (CONFIRMED state with UID)
    matched_event = PersonalUniversityEvent(
        id="matched",
        title="Сопоставленное событие",
        description="Уже сопоставлено",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.CONFIRMED,
        university_event_uid="univ-123"
    )
    assert matched_event.is_matched() is True

    # Test CONFIRMED state but no UID
    partial_event = PersonalUniversityEvent(
        id="partial",
        title="Частичное событие",
        description="Сопоставлен но нет UID",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.CONFIRMED
    )
    assert partial_event.is_matched() is False

    # Test other states with UID should not be considered matched
    moved_event_with_uid = PersonalUniversityEvent(
        id="moved-with-uid",
        title="Перемещённое событие",
        description="Событие было перемещено",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.MOVED,
        university_event_uid="univ-123"
    )
    assert moved_event_with_uid.is_matched() is False


def test_personal_university_event_get_match_quality():
    """Test get_match_quality method."""
    start_time = datetime(2026, 9, 1, 10, 0)
    end_time = datetime(2026, 9, 1, 12, 0)

    # Test unmatched event (EXPECTED state)
    unmatched_event = PersonalUniversityEvent(
        id="unmatched",
        title="Несопоставленное событие",
        description="Еще не сопоставлено",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.EXPECTED
    )
    assert unmatched_event.get_match_quality() == 0.0

    # Test matched event with different confidences and types
    test_cases = [
        (AttendanceConfidence.LOW, MatchType.EXACT, 0.25),   # 0.25 * 1.0
        (AttendanceConfidence.MEDIUM, MatchType.PARTIAL_TIME, 0.4),  # 0.5 * 0.8
        (AttendanceConfidence.HIGH, MatchType.TITLE_ONLY, 0.45),   # 0.75 * 0.6
        (AttendanceConfidence.VERY_HIGH, MatchType.FUZZY, 0.4),   # 1.0 * 0.4
    ]

    for confidence, match_type, expected in test_cases:
        event = PersonalUniversityEvent(
            id="quality-test",
            title="Тест качества",
            description="Проверка качества matched",
            start_time=start_time,
            end_time=end_time,
            state=PersonalEventState.CONFIRMED,
            university_event_uid="univ-123",
            match_confidence=confidence,
            match_type=match_type
        )
        # Allow small floating point differences
        assert abs(event.get_match_quality() - expected) < 0.001


def test_attendance_match_result_creation():
    """Test creating AttendanceMatchResult instances."""
    alternatives: List[Tuple[str, MatchType, AttendanceConfidence]] = [
        ("alt1", MatchType.FUZZY, AttendanceConfidence.LOW),
        ("alt2", MatchType.PARTIAL_TIME, AttendanceConfidence.MEDIUM)
    ]

    result = AttendanceMatchResult(
        personal_event_id="personal-123",
        university_event_uid="univ-456",
        match_type=MatchType.EXACT,
        confidence=AttendanceConfidence.HIGH,
        explanation="Exact match in time and title",
        alternatives=alternatives,
        metadata={"match_score": 0.95}
    )

    assert result.personal_event_id == "personal-123"
    assert result.university_event_uid == "univ-456"
    assert result.match_type == MatchType.EXACT
    assert result.confidence == AttendanceConfidence.HIGH
    assert result.explanation == "Exact match in time and title"
    assert result.alternatives == alternatives
    assert result.metadata == {"match_score": 0.95}


def test_attendance_match_result_defaults():
    """Test AttendanceMatchResult with default values."""
    result = AttendanceMatchResult(
        personal_event_id="personal-123",
        university_event_uid=None,
        match_type=MatchType.NO_MATCH,
        confidence=AttendanceConfidence.LOW,
        explanation="No match found"
    )

    assert result.personal_event_id == "personal-123"
    assert result.university_event_uid is None
    assert result.match_type == MatchType.NO_MATCH
    assert result.confidence == AttendanceConfidence.LOW
    assert result.explanation == "No match found"
    assert result.alternatives == []
    assert result.metadata == {}


def test_personal_event_state_machine():
    """Test state machine transitions for PersonalUniversityEvent."""
    start_time = datetime(2026, 9, 1, 10, 0)
    end_time = datetime(2026, 9, 1, 12, 0)

    # Test 1: EXPECTED -> CONFIRMED (exact match with high confidence)
    event = PersonalUniversityEvent(
        id="test-1",
        title="Тестовое событие",
        description="Описание",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.EXPECTED
    )

    match_result_exact = AttendanceMatchResult(
        personal_event_id="test-1",
        university_event_uid="univ-123",
        match_type=MatchType.EXACT,
        confidence=AttendanceConfidence.HIGH,
        explanation="Exact match"
    )

    event.apply_match_result(match_result_exact)
    assert event.state == PersonalEventState.CONFIRMED
    assert event.is_matched() is True

    # Test 2: EXPECTED -> MOVED (partial match)
    event2 = PersonalUniversityEvent(
        id="test-2",
        title="Тестовое событие 2",
        description="Описание 2",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.EXPECTED
    )

    match_result_partial = AttendanceMatchResult(
        personal_event_id="test-2",
        university_event_uid="univ-456",
        match_type=MatchType.PARTIAL_TIME,
        confidence=AttendanceConfidence.MEDIUM,
        explanation="Partial match"
    )

    event2.apply_match_result(match_result_partial)
    assert event2.state == PersonalEventState.MOVED
    assert event2.is_matched() is False  # MOVED state doesn't count as matched

    # Test 3: EXPECTED -> POSSIBLY_CANCELLED (no match)
    event3 = PersonalUniversityEvent(
        id="test-3",
        title="Тестовое событие 3",
        description="Описание 3",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.EXPECTED
    )

    match_result_none = AttendanceMatchResult(
        personal_event_id="test-3",
        university_event_uid=None,
        match_type=MatchType.NO_MATCH,
        confidence=AttendanceConfidence.LOW,
        explanation="No match found"
    )

    event3.apply_match_result(match_result_none)
    assert event3.state == PersonalEventState.POSSIBLY_CANCELLED
    assert event3.is_matched() is False

    # Test 4: CONFIRMED -> MOVED (previously confirmed, now partial match)
    event4 = PersonalUniversityEvent(
        id="test-4",
        title="Тестовое событие 4",
        description="Описание 4",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.CONFIRMED,
        university_event_uid="univ-789"
    )

    match_result_moved = AttendanceMatchResult(
        personal_event_id="test-4",
        university_event_uid="univ-789",
        match_type=MatchType.PARTIAL_TIME,
        confidence=AttendanceConfidence.MEDIUM,
        explanation="Event rescheduled"
    )

    event4.apply_match_result(match_result_moved)
    assert event4.state == PersonalEventState.MOVED

    # Test 5: Any state -> NEEDS_REVIEW (low confidence fuzzy match)
    event5 = PersonalUniversityEvent(
        id="test-5",
        title="Тестовое событие 5",
        description="Описание 5",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.CONFIRMED
    )

    match_result_low_conf = AttendanceMatchResult(
        personal_event_id="test-5",
        university_event_uid="univ-xyz",
        match_type=MatchType.FUZZY,
        confidence=AttendanceConfidence.LOW,
        explanation="Low confidence fuzzy match"
    )

    event5.apply_match_result(match_result_low_conf)
    assert event5.state == PersonalEventState.NEEDS_REVIEW

    # Test 6: Applying multiple transitions
    event6 = PersonalUniversityEvent(
        id="test-6",
        title="Тестовое событие 6",
        description="Описание 6",
        start_time=start_time,
        end_time=end_time,
        state=PersonalEventState.EXPECTED
    )

    # First apply a match that makes it confirmed
    match_result1 = AttendanceMatchResult(
        personal_event_id="test-6",
        university_event_uid="univ-111",
        match_type=MatchType.EXACT,
        confidence=AttendanceConfidence.VERY_HIGH,
        explanation="High confidence exact match"
    )
    event6.apply_match_result(match_result1)
    assert event6.state == PersonalEventState.CONFIRMED

    # Then apply a no match that should make it possibly cancelled
    match_result2 = AttendanceMatchResult(
        personal_event_id="test-6",
        university_event_uid=None,
        match_type=MatchType.NO_MATCH,
        confidence=AttendanceConfidence.LOW,
        explanation="Event no longer in schedule"
    )
    event6.apply_match_result(match_result2)
    assert event6.state == PersonalEventState.POSSIBLY_CANCELLED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])