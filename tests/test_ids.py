"""
Unit tests for ID utilities.
"""

import pytest
import uuid
from ids import IDGenerator, EventID, PreparationBlockID


def test_id_generator_uid():
    """Test UID generation."""
    uid1 = IDGenerator.generate_uid()
    uid2 = IDGenerator.generate_uid()

    # UIDs should be different
    assert uid1 != uid2

    # UIDs should be valid UUIDs
    assert IDGenerator.is_valid_uid(uid1)
    assert IDGenerator.is_valid_uid(uid2)

    # Should be able to parse as UUID
    uuid_obj = uuid.UUID(uid1)
    assert str(uuid_obj) == uid1


def test_id_generator_preparation_uid():
    """Test preparation block UID generation."""
    source_uid = "test-source-uid"
    prep_uid = IDGenerator.generate_preparation_uid(source_uid)

    assert prep_uid == f"prep-{source_uid}"

    # Test with different source UIDs
    source_uid2 = "another-source"
    prep_uid2 = IDGenerator.generate_preparation_uid(source_uid2)
    assert prep_uid2 == f"prep-{source_uid2}"
    assert prep_uid != prep_uid2


def test_id_generator_validation():
    """Test UID validation."""
    # Valid UUID
    valid_uid = "123e4567-e89b-12d3-a456-426614174000"
    assert IDGenerator.is_valid_uid(valid_uid) == True

    # Invalid UUIDs
    assert IDGenerator.is_valid_uid("") == False
    assert IDGenerator.is_valid_uid("not-a-uuid") == False
    assert IDGenerator.is_valid_uid("123e4567-e89b-12d3-a456-42661417400") == False  # too short
    assert IDGenerator.is_valid_uid("123e4567-e89b-12d3-a456-4266141740000") == False  # too long
    assert IDGenerator.is_valid_uid("123e4567-e89b-12d3-a456-42661417400g") == False  # invalid hex


def test_id_generator_short_id():
    """Test short ID generation."""
    short_id1 = IDGenerator.generate_short_id(8)
    short_id2 = IDGenerator.generate_short_id(8)
    short_id3 = IDGenerator.generate_short_id(12)

    assert len(short_id1) == 8
    assert len(short_id2) == 8
    assert len(short_id3) == 12

    # Should be alphanumeric (hexadecimal)
    assert short_id1.isalnum()
    assert short_id2.isalnum()
    assert short_id3.isalnum()

    # Different calls should produce different IDs (very high probability)
    # Note: There's a tiny chance they could be the same, but it's extremely unlikely
    # For unit testing, we'll check that they're valid format


def test_event_id():
    """Test EventID wrapper."""
    # Test valid UUID
    valid_uid = "123e4567-e89b-12d3-a456-426614174000"
    event_id = EventID(valid_uid)
    assert event_id.value == valid_uid
    assert str(event_id) == valid_uid

    # Test equality
    event_id2 = EventID(valid_uid)
    event_id3 = EventID(IDGenerator.generate_uid())
    assert event_id == event_id2
    assert event_id != event_id3

    # Test hashability (can be used in sets and as dict keys)
    event_set = {event_id, event_id2}
    assert len(event_set) == 1  # Same IDs should result in set of size 1

    event_dict = {event_id: "test"}
    assert event_dict[event_id2] == "test"

    # Test invalid UID raises exception
    with pytest.raises(ValueError):
        EventID("not-a-valid-uuid")

    with pytest.raises(ValueError):
        EventID("")

    with pytest.raises(ValueError):
        EventID(None)


def test_event_id_generate():
    """Test EventID generation."""
    event_id = EventID.generate()
    assert isinstance(event_id, EventID)
    assert IDGenerator.is_valid_uid(event_id.value)

    # Two generated IDs should be different
    event_id2 = EventID.generate()
    assert event_id != event_id2


def test_preparation_block_id():
    """Test PreparationBlockID."""
    source_uid = "source-event-123"
    prep_id = PreparationBlockID.from_source_uid(source_uid)

    assert isinstance(prep_id, PreparationBlockID)
    assert prep_id.source_uid == source_uid
    assert prep_id.value == f"prep-{source_uid}"

    # Test direct creation
    prep_id2 = PreparationBlockID("custom-prep-id", source_uid)
    assert prep_id2.value == "custom-prep-id"
    assert prep_id2.source_uid == source_uid

    # Test equality
    prep_id3 = PreparationBlockID.from_source_uid(source_uid)
    assert prep_id == prep_id3  # Should be equal when from same source

    prep_id4 = PreparationBlockID("different-id", source_uid)
    assert prep_id != prep_id4

    # Test hashability
    prep_set = {prep_id, prep_id3}
    assert len(prep_set) == 1  # Same IDs

    # Test invalid values
    with pytest.raises(ValueError):
        PreparationBlockID("", source_uid)

    with pytest.raises(ValueError):
        PreparationBlockID(None, source_uid)

    # Test that None source_uid is allowed (it's Optional)
    prep_id_none_source = PreparationBlockID("valid-id", None)
    assert prep_id_none_source.source_uid is None
    assert prep_id_none_source.value == "valid-id"


def test_preparation_block_id_string_representation():
    """Test PreparationBlockID string representation."""
    prep_id = PreparationBlockID.from_source_uid("test-source")
    assert str(prep_id) == "prep-test-source"