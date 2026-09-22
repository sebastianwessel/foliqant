"""Immutable execution values shared by the engine and adapter ports."""

from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Literal, cast

from .errors import ErrorCode, ServiceError
from .identity import Identity
from .json import FrozenJson, FrozenObject, freeze_json
from .plan import CategoryPlan, DecisionIssue

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
class Selection:
    """Effective classification, kept separate from the unchanged native result."""

    category: CategoryPlan
    origin: Literal["model", "fallback"]

    def as_json(self) -> FrozenObject:
        category: dict[str, str] = {"id": self.category.id}
        if self.category.description is not None:
            category["description"] = self.category.description
        return cast(FrozenObject, freeze_json({"category": category, "origin": self.origin}))


@dataclass(frozen=True, slots=True)
class StepRecord:
    status: StepStatus
    result: FrozenJson = None
    has_result: bool = False
    error: Failure | None = None
    elapsed_seconds: float | None = None
    usage: Usage | None = None
    selection: Selection | None = None
    partial_result: FrozenObject | None = None
    kind: Literal["flow_collection"] | None = None


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """Validated adapter output with explicit business review/routing facts."""

    result: FrozenJson
    needs_review: bool = False
    route_key: str | None = None
    selection: Selection | None = None
    unresolved_issues: tuple[DecisionIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class FlowRecord:
    """One sequential flow, including unvisited steps and an explicit result presence."""

    status: StepStatus
    steps: tuple[tuple[str, StepRecord], ...]
    result: FrozenJson = None
    has_result: bool = False
    usage: Usage | None = None
    elapsed_seconds: float | None = None
    error: Failure | None = None


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """An authored boundary selected after a completed or unresolved flow."""

    source: str
    reason: Literal["completed", "needs_review"]
    flow: str | None = None
    outcome: Literal["completed", "needs_review"] | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    execution_id: str
    workflow: str
    revision: str
    status: RunStatus
    payload: FrozenJson
    metadata: FrozenObject
    flows: tuple[tuple[str, FlowRecord], ...]
    usage: Usage
    error: Failure | None = None
    transitions: tuple[TransitionRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class CallerContext:
    """Read-only caller context; metadata does not establish authority."""

    identity: Identity
    metadata: FrozenObject


def usage_value(value: Usage) -> FrozenObject:
    """Project measured usage once, without adding parent and child totals."""
    return MappingProxyType(
        {
            "model_requests": value.model_requests,
            "tool_calls": value.tool_calls,
            **{field.name: getattr(value.tokens, field.name) for field in fields(value.tokens)},
        }
    )


def step_record_value(record: StepRecord) -> FrozenObject:
    """One canonical projection shared by nested ledgers and the public boundary."""
    value = _record_value(record)
    if record.selection is not None:
        value["selection"] = record.selection.as_json()
    if record.partial_result is not None:
        value["partial_result"] = record.partial_result
    if record.kind is not None:
        value["kind"] = record.kind
    return MappingProxyType(value)


def flow_record_value(record: FlowRecord) -> FrozenObject:
    """Include every local step exactly once in a flow's public ledger record."""
    value = _record_value(record)
    steps: dict[str, FrozenJson] = {}
    for name, step in record.steps:
        if name in steps:
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        steps[name] = step_record_value(step)
    value["steps"] = MappingProxyType(steps)
    return MappingProxyType(value)


def _record_value(record: StepRecord | FlowRecord) -> dict[str, FrozenJson]:
    if not record.has_result and record.result is not None:
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    value: dict[str, FrozenJson] = {"status": record.status}
    if record.has_result:
        value["result"] = record.result
    if record.usage is not None:
        value["usage"] = usage_value(record.usage)
    if record.elapsed_seconds is not None:
        value["elapsed_seconds"] = record.elapsed_seconds
    if record.error is not None:
        if not isinstance(record.error.code, ErrorCode) or type(record.error.retryable) is not bool:
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        value["error"] = MappingProxyType(
            {
                "code": record.error.code.value,
                "message": record.error.message,
                "retryable": record.error.retryable,
            }
        )
    return value
