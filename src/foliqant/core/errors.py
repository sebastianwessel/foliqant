"""Stable, content-free failures suitable for public boundary adapters."""

import re
from enum import StrEnum
from typing import Literal


# One precise code per failure mode; a failure is never a business outcome.
# ``retryable`` on a failure is supplied by the failing boundary; the code alone
# does not grant a retry.
class ErrorCode(StrEnum):
    INVALID_CONFIGURATION = "invalid_configuration"
    INVALID_INPUT = "invalid_input"
    INVALID_OUTPUT = "invalid_output"
    INVALID_TOOL_CALL = "invalid_tool_call"
    OUTPUT_LIMIT_REACHED = "output_limit_reached"
    OUTPUT_REFUSED = "output_refused"
    CONTEXT_LIMIT_EXCEEDED = "context_limit_exceeded"
    REQUEST_REJECTED = "request_rejected"
    MODEL_NOT_FOUND = "model_not_found"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    MISSING_BINDING = "missing_binding"
    REQUEST_TIMEOUT = "request_timeout"
    RUN_TIMEOUT = "run_timeout"
    STEP_LIMIT_REACHED = "step_limit_reached"
    MODEL_REQUEST_LIMIT_REACHED = "model_request_limit_reached"
    ITERATION_LIMIT_REACHED = "iteration_limit_reached"
    TOOL_CALL_LIMIT_REACHED = "tool_call_limit_reached"
    RATE_LIMITED = "rate_limited"
    DEPENDENCY_OVERLOADED = "dependency_overloaded"
    DEPENDENCY_FAILURE = "dependency_failure"
    TOOL_ERROR = "tool_error"
    TOOL_OUTPUT_LIMIT_EXCEEDED = "tool_output_limit_exceeded"
    TOOL_CATALOG_MISMATCH = "tool_catalog_mismatch"
    HANDLER_FAILED = "handler_failed"
    CONFLICT = "conflict"
    UNCERTAIN_EFFECT = "uncertain_effect"
    CANCELLED = "cancelled"
    CAPACITY_EXCEEDED = "capacity_exceeded"


_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_CONFIGURATION: "The workflow configuration is invalid.",
    ErrorCode.INVALID_INPUT: "The input does not satisfy the required contract.",
    ErrorCode.INVALID_OUTPUT: "An operation returned an invalid result.",
    ErrorCode.INVALID_TOOL_CALL: (
        "The model called an unknown tool or passed arguments that violate its schema."
    ),
    ErrorCode.OUTPUT_LIMIT_REACHED: (
        "The model reached its output token limit before completing the result."
    ),
    ErrorCode.OUTPUT_REFUSED: "The model or its provider refused to produce the result.",
    ErrorCode.CONTEXT_LIMIT_EXCEEDED: "The model request exceeds the model's context window.",
    ErrorCode.REQUEST_REJECTED: "The model provider or tool server rejected the request.",
    ErrorCode.MODEL_NOT_FOUND: "The configured model is not available at the provider.",
    ErrorCode.UNAUTHENTICATED: "Authentication is required.",
    ErrorCode.FORBIDDEN: "The operation is not authorized.",
    ErrorCode.NOT_FOUND: "The requested resource was not found.",
    ErrorCode.MISSING_BINDING: "A required input binding is unavailable.",
    ErrorCode.REQUEST_TIMEOUT: "A model, tool or handler request exceeded its timeout.",
    ErrorCode.RUN_TIMEOUT: "The run exceeded its deadline.",
    ErrorCode.STEP_LIMIT_REACHED: "The run reached its limit of executed steps.",
    ErrorCode.MODEL_REQUEST_LIMIT_REACHED: "The step reached its limit of model requests.",
    ErrorCode.ITERATION_LIMIT_REACHED: (
        "The step reached its limit of model turns before a final answer."
    ),
    ErrorCode.TOOL_CALL_LIMIT_REACHED: "The step reached its limit of tool calls.",
    ErrorCode.RATE_LIMITED: "A dependency rejected the request because of a rate limit.",
    ErrorCode.DEPENDENCY_OVERLOADED: "A dependency is temporarily overloaded.",
    ErrorCode.DEPENDENCY_FAILURE: "A required dependency is unavailable.",
    ErrorCode.TOOL_ERROR: "The tool reported an error.",
    ErrorCode.TOOL_OUTPUT_LIMIT_EXCEEDED: "The tool result exceeds its configured size limit.",
    ErrorCode.TOOL_CATALOG_MISMATCH: "The tool server's tools differ from the declared catalog.",
    ErrorCode.HANDLER_FAILED: "A registered handler failed unexpectedly.",
    ErrorCode.CONFLICT: "The request conflicts with an existing operation.",
    ErrorCode.UNCERTAIN_EFFECT: "An external operation requires reconciliation.",
    ErrorCode.CANCELLED: "The execution was cancelled.",
    ErrorCode.CAPACITY_EXCEEDED: "The service has reached its admission limit.",
}


#: Why an ``invalid_output`` or ``output_limit_reached`` failure happened, content-free.
type FailureReason = Literal[
    "json_parse_error",
    "schema_violation",
    "decision_contract",
    "missing_output",
    "reasoning_consumed_budget",
    "answer_exceeded_budget",
]
FAILURE_REASONS: frozenset[str] = frozenset(FailureReason.__value__.__args__)

#: A location is a JSON pointer built from schema vocabulary (``/units/0/intent``) or
#: ``question:<id>`` of a decision; a constraint is a validator keyword or problem kind.
LOCATION_PATTERN = r"[A-Za-z0-9_.:*/-]{1,256}"
CONSTRAINT_PATTERN = r"[a-z][a-z0-9_]{0,63}"
_LOCATION = re.compile(LOCATION_PATTERN)
_CONSTRAINT = re.compile(CONSTRAINT_PATTERN)


class ServiceError(Exception):
    """An error code with a fixed safe message, never a provider exception body.

    Retry eligibility is explicit: a dependency failure does not imply that an
    external mutation can safely be repeated. ``reason``, ``location`` and
    ``constraint`` optionally explain the failure without content; a value that
    does not match its safe pattern is dropped, never reported.
    """

    def __init__(
        self,
        code: ErrorCode,
        *,
        retryable: bool = False,
        reason: FailureReason | None = None,
        location: str | None = None,
        constraint: str | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.reason: FailureReason | None = reason if reason in FAILURE_REASONS else None
        self.location = safe_location(location)
        self.constraint = safe_constraint(constraint)
        super().__init__(_MESSAGES[code])


def safe_location(value: object) -> str | None:
    """The value when it is a safe content-free location, else ``None``."""
    return value if type(value) is str and _LOCATION.fullmatch(value) else None


def safe_constraint(value: object) -> str | None:
    """The value when it is a safe validator keyword or problem kind, else ``None``."""
    return value if type(value) is str and _CONSTRAINT.fullmatch(value) else None


TIMEOUT_CODES = frozenset({ErrorCode.REQUEST_TIMEOUT, ErrorCode.RUN_TIMEOUT})


def timeout_code(now: float, run_deadline: float) -> ErrorCode:
    """``run_timeout`` once the run's deadline has passed, else ``request_timeout``.

    An operation bounded by the smaller of its own timeout and the run deadline
    reports which bound it hit.
    """
    return ErrorCode.RUN_TIMEOUT if now >= run_deadline else ErrorCode.REQUEST_TIMEOUT
