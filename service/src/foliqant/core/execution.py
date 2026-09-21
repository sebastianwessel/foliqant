"""Immutable execution values shared by the engine and adapter ports."""

from dataclasses import dataclass, fields
from typing import Literal

from .errors import ErrorCode, ServiceError
from .identity import Identity
from .json import FrozenJson, FrozenObject

type RunStatus = Literal["completed", "needs_review", "failed", "cancelled"]
type StepStatus = Literal["completed", "needs_review", "failed", "cancelled", "skipped"]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Unavailable token measurements remain None, including component subsets."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None and (type(value) is not int or value < 0):
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
        for subset, total in (
            (self.cache_read_input_tokens, self.input_tokens),
            (self.cache_write_input_tokens, self.input_tokens),
            (self.reasoning_output_tokens, self.output_tokens),
        ):
            if subset is not None and total is not None and subset > total:
                raise ServiceError(ErrorCode.INVALID_OUTPUT)

    @classmethod
    def zero(cls) -> "TokenUsage":
        """Known zero only when no model attempts occurred."""
        return cls(0, 0, 0, 0, 0)

    def plus(self, other: "TokenUsage") -> "TokenUsage":
        """Sum measurements without converting absent provider data to zero."""

        def add(left: int | None, right: int | None) -> int | None:
            return None if left is None or right is None else left + right

        return TokenUsage(
            add(self.input_tokens, other.input_tokens),
            add(self.output_tokens, other.output_tokens),
            add(self.cache_read_input_tokens, other.cache_read_input_tokens),
            add(self.cache_write_input_tokens, other.cache_write_input_tokens),
            add(self.reasoning_output_tokens, other.reasoning_output_tokens),
        )


@dataclass(frozen=True, slots=True)
class Usage:
    model_requests: int = 0
    tool_calls: int = 0
    tokens: TokenUsage = TokenUsage(0, 0, 0, 0, 0)

    def plus(self, other: "Usage") -> "Usage":
        return Usage(
            self.model_requests + other.model_requests,
            self.tool_calls + other.tool_calls,
            self.tokens.plus(other.tokens),
        )


@dataclass(frozen=True, slots=True)
class Failure:
    code: ErrorCode
    retryable: bool = False

    @property
    def message(self) -> str:
        """Return only the canonical safe text, never an adapter exception."""
        return str(ServiceError(self.code))


@dataclass(frozen=True, slots=True)
class StepRecord:
    status: StepStatus
    result: FrozenJson = None
    has_result: bool = False
    error: Failure | None = None


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """Validated adapter output with explicit business review/routing facts."""

    result: FrozenJson
    needs_review: bool = False
    route_key: str | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    execution_id: str
    workflow: str
    revision: str
    status: RunStatus
    payload: FrozenJson
    metadata: FrozenObject
    decisions: tuple[tuple[str, StepRecord], ...]
    usage: Usage
    error: Failure | None = None


@dataclass(frozen=True, slots=True)
class CallerContext:
    """Read-only caller context; metadata does not establish authority."""

    identity: Identity
    metadata: FrozenObject
