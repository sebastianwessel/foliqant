"""Stable, content-free failures suitable for public boundary adapters."""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_CONFIGURATION = "invalid_configuration"
    INVALID_INPUT = "invalid_input"
    INVALID_OUTPUT = "invalid_output"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    MISSING_BINDING = "missing_binding"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEPENDENCY_FAILURE = "dependency_failure"
    CONFLICT = "conflict"
    UNCERTAIN_EFFECT = "uncertain_effect"
    CANCELLED = "cancelled"
    CAPACITY_EXCEEDED = "capacity_exceeded"


_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_CONFIGURATION: "The workflow configuration is invalid.",
    ErrorCode.INVALID_INPUT: "The input does not satisfy the required contract.",
    ErrorCode.INVALID_OUTPUT: "An operation returned an invalid result.",
    ErrorCode.UNAUTHENTICATED: "Authentication is required.",
    ErrorCode.FORBIDDEN: "The operation is not authorized.",
    ErrorCode.NOT_FOUND: "The requested resource was not found.",
    ErrorCode.MISSING_BINDING: "A required input binding is unavailable.",
    ErrorCode.TIMEOUT: "The operation exceeded its deadline.",
    ErrorCode.BUDGET_EXHAUSTED: "The execution budget is exhausted.",
    ErrorCode.DEPENDENCY_FAILURE: "A required dependency is unavailable.",
    ErrorCode.CONFLICT: "The request conflicts with an existing operation.",
    ErrorCode.UNCERTAIN_EFFECT: "An external operation requires reconciliation.",
    ErrorCode.CANCELLED: "The execution was cancelled.",
    ErrorCode.CAPACITY_EXCEEDED: "The service has reached its admission limit.",
}


class ServiceError(Exception):
    """An error code with a fixed safe message, never a provider exception body.

    Retry eligibility is explicit: a dependency failure does not imply that an
    external mutation can safely be repeated.
    """

    def __init__(self, code: ErrorCode, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(_MESSAGES[code])
