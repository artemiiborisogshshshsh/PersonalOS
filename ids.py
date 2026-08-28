"""
Identifier utilities for Personal OS AI Calendar system.
"""

from __future__ import annotations
import uuid
from typing import Optional


class IDGenerator:
    """Utility class for generating various types of IDs."""

    @staticmethod
    def generate_uid() -> str:
        """
        Generate a unique identifier similar to iCalendar UID format.

        Returns:
            A UUID4 string suitable for use as an iCal UID.
        """
        return str(uuid.uuid4())

    @staticmethod
    def generate_preparation_uid(source_uid: str) -> str:
        """
        Generate a UID for a preparation block based on source event UID.

        Args:
            source_uid: The UID of the source event

        Returns:
            A preparation block UID in format 'prep-{source_uid}'
        """
        return f"prep-{source_uid}"

    @staticmethod
    def is_valid_uid(uid: str) -> bool:
        """
        Check if a string is a valid UUID.

        Args:
            uid: String to validate

        Returns:
            True if uid is a valid UUID, False otherwise
        """
        if not uid or not isinstance(uid, str):
            return False
        try:
            uuid.UUID(uid)
            return True
        except ValueError:
            return False

    @staticmethod
    def generate_short_id(length: int = 8) -> str:
        """
        Generate a short alphanumeric ID.

        Args:
            length: Length of the ID to generate (default: 8)

        Returns:
            A short alphanumeric string
        """
        return str(uuid.uuid4()).replace('-', '')[:length]


class EventID:
    """Wrapper for event IDs with validation."""

    def __init__(self, value: str):
        if value is None or not isinstance(value, str):
            raise ValueError(f"Invalid UID format: {value}")
        if not IDGenerator.is_valid_uid(value):
            raise ValueError(f"Invalid UID format: {value}")
        self._value = value

    @property
    def value(self) -> str:
        """Get the ID value."""
        return self._value

    def __str__(self) -> str:
        return self._value

    def __eq__(self, other) -> bool:
        if not isinstance(other, EventID):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    @classmethod
    def generate(cls) -> EventID:
        """Generate a new EventID."""
        return cls(IDGenerator.generate_uid())


class PreparationBlockID:
    """Wrapper for preparation block IDs."""

    def __init__(self, value: str, source_uid: Optional[str] = None):
        # Preparation block UIDs don't need to be UUIDs, but we validate format
        if not value or not isinstance(value, str):
            raise ValueError("Preparation block ID must be a non-empty string")
        # source_uid can be None (optional), but if provided must be string
        if source_uid is not None and not isinstance(source_uid, str):
            raise ValueError("Source UID must be a string or None")
        self._value = value
        self._source_uid = source_uid

    @property
    def value(self) -> str:
        """Get the ID value."""
        return self._value

    @property
    def source_uid(self) -> Optional[str]:
        """Get the source event UID if available."""
        return self._source_uid

    def __str__(self) -> str:
        return self._value

    def __eq__(self, other) -> bool:
        if not isinstance(other, PreparationBlockID):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    @classmethod
    def from_source_uid(cls, source_uid: str) -> PreparationBlockID:
        """
        Create a PreparationBlockID from a source event UID.

        Args:
            source_uid: The UID of the source event

        Returns:
            A new PreparationBlockID
        """
        if not source_uid or not isinstance(source_uid, str):
            raise ValueError("Source UID must be a non-empty string")
        return cls(IDGenerator.generate_preparation_uid(source_uid), source_uid)