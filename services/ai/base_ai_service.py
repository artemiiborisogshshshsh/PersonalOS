"""
Base AI Service Interface
Defines the common interface that all AI services should implement.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass


@dataclass
class AIServiceResult:
    """Base result for AI service operations."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    confidence: float = 0.0  # Confidence score from 0.0 to 1.0


class BaseAIService(ABC):
    """Base class for all AI services."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the AI service with configuration.

        Args:
            config: Configuration dictionary for the AI service
        """
        self.config = config or {}
        self._is_initialized = False

    @abstractmethod
    async def initialize(self) -> bool:
        """
        Initialize the AI service and establish connections.

        Returns:
            bool: True if initialization successful, False otherwise
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the AI service is healthy and connected.

        Returns:
            bool: True if healthy, False otherwise
        """
        pass

    @abstractmethod
    async def shutdown(self) -> bool:
        """
        Shutdown the AI service and cleanup resources.

        Returns:
            bool: True if shutdown successful, False otherwise
        """
        pass

    @property
    def is_initialized(self) -> bool:
        """Check if AI service is initialized."""
        return self._is_initialized

    def _set_initialized(self, value: bool) -> None:
        """Set initialization status."""
        self._is_initialized = value