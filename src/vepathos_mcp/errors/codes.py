"""Domain error model shared by every tool.

Errors reach the agent as `CallToolResult(is_error=True)` with a structured `error` object, so a
model can explain the problem to the user and self-correct. No stack traces, no internal names.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    NO_STOPS = "NO_STOPS"
    NO_VEHICLES = "NO_VEHICLES"
    INVALID_COORDINATES = "INVALID_COORDINATES"
    PLAN_UPGRADE_REQUIRED = "PLAN_UPGRADE_REQUIRED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    CONCURRENT_OPTIMIZATION_LIMIT = "CONCURRENT_OPTIMIZATION_LIMIT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    RATE_LIMITED = "RATE_LIMITED"
    OPTIMIZATION_NOT_FOUND = "OPTIMIZATION_NOT_FOUND"
    OPTIMIZATION_EXPIRED = "OPTIMIZATION_EXPIRED"
    OPTIMIZATION_FAILED = "OPTIMIZATION_FAILED"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.CONCURRENT_OPTIMIZATION_LIMIT,
        ErrorCode.RATE_LIMITED,
        ErrorCode.OPTIMIZATION_FAILED,
        ErrorCode.BACKEND_UNAVAILABLE,
        ErrorCode.TIMEOUT,
    }
)

# Codes caused by the plan or quota; counted separately in metrics.
PLAN_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.PLAN_UPGRADE_REQUIRED,
        ErrorCode.QUOTA_EXCEEDED,
        ErrorCode.CONCURRENT_OPTIMIZATION_LIMIT,
    }
)


class DomainError(Exception):
    """An error the agent can act on."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        suggestion: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.retryable = code in RETRYABLE_CODES if retryable is None else retryable
        self.details = details or {}
        self.retry_after_seconds = retry_after_seconds

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.suggestion:
            payload["suggestion"] = self.suggestion
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        if self.details:
            payload["details"] = self.details
        return payload

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DomainError({self.code.value}: {self.message})"
