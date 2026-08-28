"""
Unit tests for result types.
"""

import pytest
from datetime import datetime
from results import (
    OperationResult, OperationStatus, ValidationResult,
    NotificationResult, HealthCheckResult, safe_execute
)


def test_operation_result():
    """Test OperationResult functionality."""
    # Test success result
    success_result = OperationResult.success(
        data="test data",
        message="Operation completed",
        key1="value1",
        key2=42
    )

    assert success_result.is_success == True
    assert success_result.is_failure == False
    assert success_result.is_partial == False
    assert success_result.data == "test data"
    assert success_result.message == "Operation completed"
    assert success_result.metadata["key1"] == "value1"
    assert success_result.metadata["key2"] == 42
    assert success_result.error is None

    # Test failure result
    test_error = ValueError("Test error")
    failure_result = OperationResult.failure(
        error=test_error,
        message="Operation failed",
        error_code=500
    )

    assert failure_result.is_success == False
    assert failure_result.is_failure == True
    assert failure_result.is_partial == False
    assert failure_result.error == test_error
    assert failure_result.message == "Operation failed"
    assert failure_result.metadata["error_code"] == 500
    assert failure_result.data is None

    # Test partial result
    partial_result = OperationResult.partial(
        data={"partial": "data"},
        message="Partially completed",
        items_processed=5
    )

    assert partial_result.is_success == False
    assert partial_result.is_failure == False
    assert partial_result.is_partial == True
    assert partial_result.data == {"partial": "data"}
    assert partial_result.message == "Partially completed"
    assert partial_result.metadata["items_processed"] == 5

    # Test OperationStatus enum
    assert OperationStatus.SUCCESS.value == "success"
    assert OperationStatus.FAILURE.value == "failure"
    assert OperationStatus.PARTIAL.value == "partial"


def test_validation_result():
    """Test ValidationResult functionality."""
    # Test valid result
    valid_result = ValidationResult.valid()
    assert valid_result.is_valid == True
    assert valid_result.errors == []
    assert valid_result.warnings == []

    # Test invalid result
    invalid_result = ValidationResult.invalid(["Error 1", "Error 2"])
    assert invalid_result.is_valid == False
    assert invalid_result.errors == ["Error 1", "Error 2"]
    assert invalid_result.warnings == []

    # Test adding errors and warnings
    result = ValidationResult.valid()
    assert result.is_valid == True

    result.add_error("First error")
    assert result.is_valid == False
    assert result.errors == ["First error"]
    assert result.warnings == []

    result.add_warning("First warning")
    assert result.is_valid == False  # Still invalid due to error
    assert result.errors == ["First error"]
    assert result.warnings == ["First warning"]

    result.add_error("Second error")
    assert result.is_valid == False
    assert result.errors == ["First error", "Second error"]
    assert result.warnings == ["First warning"]

    # Test creating invalid with initial errors
    result2 = ValidationResult.invalid(["Initial error"])
    result2.add_warning("Initial warning")
    assert result2.is_valid == False
    assert result2.errors == ["Initial error"]
    assert result2.warnings == ["Initial warning"]


def test_notification_result():
    """Test NotificationResult functionality."""
    # Test success result
    success_result = NotificationResult.success(
        provider="telegram",
        message_id="msg_12345"
    )

    assert success_result.sent == True
    assert success_result.provider == "telegram"
    assert success_result.message_id == "msg_12345"
    assert success_result.error is None
    assert success_result.retry_after is None
    assert success_result.failed == False

    # Test failure result
    test_error = Exception("Network timeout")
    failure_result = NotificationResult.failure(
        provider="webhook",
        error=test_error,
        retry_after=30
    )

    assert failure_result.sent == False
    assert failure_result.provider == "webhook"
    assert failure_result.message_id is None
    assert failure_result.error == test_error
    assert failure_result.retry_after == 30
    assert failure_result.failed == True

    # Test failure without retry_after
    failure_result2 = NotificationResult.failure(
        provider="email",
        error=test_error
    )

    assert failure_result2.sent == False
    assert failure_result2.provider == "email"
    assert failure_result2.error == test_error
    assert failure_result2.retry_after is None
    assert failure_result2.failed == True


def test_health_check_result():
    """Test HealthCheckResult functionality."""
    # Test healthy result
    healthy_result = HealthCheckResult.healthy(
        service="database",
        connection_count=5,
        version="1.2.3"
    )

    assert healthy_result.service == "database"
    assert healthy_result.healthy == True
    assert healthy_result.details["connection_count"] == 5
    assert healthy_result.details["version"] == "1.2.3"
    assert healthy_result.latency_ms is None
    assert healthy_result.error is None
    assert healthy_result.is_healthy == True

    # Test unhealthy result
    test_error = Exception("Connection refused")
    unhealthy_result = HealthCheckResult.unhealthy(
        service="api",
        error=test_error,
        status_code=503,
        last_known_good="2026-08-28T10:00:00Z"
    )

    assert unhealthy_result.service == "api"
    assert unhealthy_result.healthy == False
    assert unhealthy_result.error == test_error
    assert unhealthy_result.details["status_code"] == 503
    assert unhealthy_result.details["last_known_good"] == "2026-08-28T10:00:00Z"
    assert unhealthy_result.latency_ms is None
    assert unhealthy_result.is_healthy == False

    # Test with latency
    healthy_with_latency = HealthCheckResult(
        service="cache",
        healthy=True,
        latency_ms=45.5
    )

    assert healthy_with_latency.latency_ms == 45.5
    assert healthy_with_latency.is_healthy == True


def test_safe_execute():
    """Test safe_execute function."""
    # Test successful execution
    def successful_func(x, y):
        return x + y

    result = safe_execute(successful_func, 2, 3)
    assert result.is_success == True
    assert result.data == 5
    assert result.error is None

    # Test execution with exception
    def failing_func():
        raise ValueError("Something went wrong")

    result = safe_execute(failing_func)
    assert result.is_success == False
    assert result.is_failure == True
    assert result.error is not None
    assert isinstance(result.error, ValueError)
    assert str(result.error) == "Something went wrong"
    assert result.data is None

    # Test execution with keyword arguments
    def kwarg_func(message, times=1):
        return message * times

    result = safe_execute(kwarg_func, "Hello", times=3)
    assert result.is_success == True
    assert result.data == "HelloHelloHello"


def test_operation_result_generic():
    """Test OperationResult with different data types."""
    # Test with None data
    result_none = OperationResult.success(data=None)
    assert result_none.data is None

    # Test with list data
    result_list = OperationResult.success(data=[1, 2, 3])
    assert result_list.data == [1, 2, 3]

    # Test with dict data
    result_dict = OperationResult.success(data={"key": "value"})
    assert result_dict.data == {"key": "value"}

    # Test with custom object
    class CustomObj:
        def __init__(self, value):
            self.value = value

    obj = CustomObj("test")
    result_obj = OperationResult.success(data=obj)
    assert result_obj.data == obj
    assert result_obj.data.value == "test"


if __name__ == "__main__":
    pytest.main([__file__])