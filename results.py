"""
Result types for Personal OS AI Calendar system.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Generic, TypeVar, List, Optional, Callable, Any
from enum import Enum


T = TypeVar('T')
E = TypeVar('E', bound=Exception)


class OperationStatus(Enum):
    """Status of an operation."""
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


@dataclass
class OperationResult(Generic[T]):
    """Generic result of an operation."""
    status: OperationStatus
    data: Optional[T] = None
    error: Optional[E] = None
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Check if operation was successful."""
        return self.status == OperationStatus.SUCCESS

    @property
    def is_failure(self) -> bool:
        """Check if operation failed."""
        return self.status == OperationStatus.FAILURE

    @property
    def is_partial(self) -> bool:
        """Check if operation partially succeeded."""
        return self.status == OperationStatus.PARTIAL

    @classmethod
    def success(cls, data: T = None, message: str = "", **metadata) -> OperationResult[T]:
        """Create a successful result."""
        return cls(
            status=OperationStatus.SUCCESS,
            data=data,
            message=message,
            metadata=metadata
        )

    @classmethod
    def failure(cls, error: Optional[E] = None, message: str = "", **metadata) -> OperationResult[T]:
        """Create a failure result."""
        return cls(
            status=OperationStatus.FAILURE,
            error=error,
            message=message,
            metadata=metadata
        )

    @classmethod
    def partial(cls, data: T = None, message: str = "", **metadata) -> OperationResult[T]:
        """Create a partial success result."""
        return cls(
            status=OperationStatus.PARTIAL,
            data=data,
            message=message,
            metadata=metadata
        )


@dataclass
class ValidationResult:
    """Result of a validation operation."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, error: str) -> None:
        """Add validation error."""
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        """Add validation warning."""
        self.warnings.append(warning)

    @classmethod
    def valid(cls) -> ValidationResult:
        """Create a valid validation result."""
        return cls(is_valid=True)

    @classmethod
    def invalid(cls, errors: List[str] = None) -> ValidationResult:
        """Create an invalid validation result."""
        return cls(is_valid=False, errors=errors or [])


@dataclass
class NotificationResult:
    """Result of sending a notification."""
    sent: bool
    provider: str  # e.g., "telegram", "webhook", "email"
    message_id: Optional[str] = None
    error: Optional[Exception] = None
    retry_after: Optional[int] = None  # seconds to wait before retry

    @property
    def failed(self) -> bool:
        """Check if notification failed to send."""
        return not self.sent

    @classmethod
    def success(cls, provider: str, message_id: str = None) -> NotificationResult:
        """Create a successful notification result."""
        return cls(sent=True, provider=provider, message_id=message_id)

    @classmethod
    def failure(cls, provider: str, error: Exception, retry_after: int = None) -> NotificationResult:
        """Create a failed notification result."""
        return cls(sent=False, provider=provider, error=error, retry_after=retry_after)


@dataclass
class HealthCheckResult:
    """Result of a health check operation."""
    service: str
    healthy: bool
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: Optional[float] = None
    error: Optional[Exception] = None

    @property
    def is_healthy(self) -> bool:
        """Check if service is healthy."""
        return self.healthy

    @classmethod
    def healthy(cls, service: str, **details) -> HealthCheckResult:
        """Create a healthy health check result."""
        return cls(
            service=service,
            healthy=True,
            details=details
        )

    @classmethod
    def unhealthy(cls, service: str, error: Exception = None, **details) -> HealthCheckResult:
        """Create an unhealthy health check result."""
        return cls(
            service=service,
            healthy=False,
            error=error,
            details=details
        )


def safe_execute(func: Callable[..., T], *args, **kwargs) -> OperationResult[T]:
    """
    Safely execute a function and capture any exceptions.

    Args:
        func: Function to execute
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        OperationResult containing either the function result or captured exception
    """
    try:
        result = func(*args, **kwargs)
        return OperationResult.success(data=result)
    except Exception as e:
        return OperationResult.failure(error=e, message=str(e))