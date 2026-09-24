"""Immutable execution values shared by the engine and adapter ports."""

from dataclasses import dataclass, fields
from decimal import ROUND_HALF_EVEN, Decimal
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
class Cost:
    """An estimated cost; ``amount`` is None when a needed count or price was unavailable."""

    currency: str
    amount: Decimal | None = None
    reference_model: str | None = None

    def __post_init__(self) -> None:
        if type(self.currency) is not str or not self.currency:
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        amount = self.amount
        if amount is not None and (
            type(amount) is not Decimal or not amount.is_finite() or amount < 0
        ):
            raise ServiceError(ErrorCode.INVALID_OUTPUT)

    @staticmethod
    def combine(left: "Cost | None", right: "Cost | None") -> "Cost | None":
        """Sum two estimates; unpriced requests (None) make a priced total incomplete."""
        if left is None or right is None:
            priced = left or right
            return None if priced is None else Cost(priced.currency, None, priced.reference_model)
        if left.currency != right.currency:
            # One currency per total; a mixed sum has no meaningful amount.
            return Cost(left.currency, None)
        amount = (
            left.amount + right.amount
            if left.amount is not None and right.amount is not None
            else None
        )
        reference = left.reference_model if left.reference_model == right.reference_model else None
        return Cost(left.currency, amount, reference)


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Requests sent to one provider model ID.

    ``cost`` is None when no request was priced; combining priced and unpriced
    requests keeps the currency but loses the amount.
    """

    requests: int
    tokens: TokenUsage = TokenUsage()
    cost: Cost | None = None

    def __post_init__(self) -> None:
        if type(self.requests) is not int or self.requests < 1:
            raise ServiceError(ErrorCode.INVALID_OUTPUT)

    def plus(self, other: "ModelUsage") -> "ModelUsage":
        return ModelUsage(
            self.requests + other.requests,
            self.tokens.plus(other.tokens),
            Cost.combine(self.cost, other.cost),
        )


def _merge_models(
    left: tuple[tuple[str, ModelUsage], ...], right: tuple[tuple[str, ModelUsage], ...]
) -> tuple[tuple[str, ModelUsage], ...]:
    if not right:
        return left
    merged = dict(left)
    for model, usage in right:
        previous = merged.get(model)
        merged[model] = usage if previous is None else previous.plus(usage)
    return tuple(sorted(merged.items()))


@dataclass(frozen=True, slots=True)
class Usage:
    """Measured totals; ``by_model`` splits model requests by provider model ID."""

    model_requests: int = 0
    tool_calls: int = 0
    tokens: TokenUsage = TokenUsage(0, 0, 0, 0, 0)
    by_model: tuple[tuple[str, ModelUsage], ...] = ()

    def plus(self, other: "Usage") -> "Usage":
        return Usage(
            self.model_requests + other.model_requests,
            self.tool_calls + other.tool_calls,
            self.tokens.plus(other.tokens),
            _merge_models(self.by_model, other.by_model),
        )

    @property
    def cost(self) -> Cost | None:
        """The estimate across models: None unless at least one model is priced."""
        if not self.by_model:
            return None
        total = self.by_model[0][1].cost
        for _, usage in self.by_model[1:]:
            total = Cost.combine(total, usage.cost)
        return total


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
    """Validated adapter output with explicit business review facts.

    ``unresolved_issues`` select issue-specific review routes; they require
    ``needs_review``. ``selection`` is an effective classification whose origin
    is ``fallback`` exactly when the step needs review.
    """

    result: FrozenJson
    needs_review: bool = False
    selection: Selection | None = None
    unresolved_issues: tuple[DecisionIssue, ...] = ()


type RepeatStop = Literal["until", "exhausted", "continue_when", "review", "failure"]


@dataclass(frozen=True, slots=True)
class FlowRecord:
    """One sequential flow, including unvisited steps and an explicit result presence.

    For a repeated or retry flow, the top-level fields describe the last run and
    ``attempts`` retains every run in order; ``stopped_by`` explains why a
    repeated flow stopped.
    """

    status: StepStatus
    steps: tuple[tuple[str, StepRecord], ...]
    result: FrozenJson = None
    has_result: bool = False
    usage: Usage | None = None
    elapsed_seconds: float | None = None
    error: Failure | None = None
    attempts: tuple["FlowRecord", ...] = ()
    stopped_by: RepeatStop | None = None

    @property
    def attempt_count(self) -> int:
        """Number of executions: zero for a skipped flow."""
        if self.attempts:
            return len(self.attempts)
        return 0 if self.status == "skipped" else 1

    @property
    def total_usage(self) -> Usage | None:
        """Usage of every run, counted once."""
        if not self.attempts:
            return self.usage
        total = Usage()
        for attempt in self.attempts:
            if attempt.usage is not None:
                total = total.plus(attempt.usage)
        return total

    @property
    def total_elapsed_seconds(self) -> float | None:
        if not self.attempts:
            return self.elapsed_seconds
        return sum(attempt.elapsed_seconds or 0.0 for attempt in self.attempts)


type RouteKind = Literal["direct", "cases", "route", "review"]


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """An authored boundary selected after a completed or unresolved flow.

    ``route_kind`` names the configuration form that selected the target;
    ``route_index`` is the ``route`` entry index and ``route_case`` the matched
    case key or review issue.
    """

    source: str
    reason: Literal["completed", "needs_review"]
    flow: str | None = None
    outcome: Literal["completed", "needs_review"] | None = None
    route_kind: RouteKind = "direct"
    route_index: int | None = None
    route_case: str | None = None


@dataclass(frozen=True, slots=True)
class StartRecord:
    """The first flow of a run and the ``start`` form that selected it.

    ``route_index`` is the selected entry of a routed ``start``.
    """

    flow: str
    route_kind: Literal["direct", "route"] = "direct"
    route_index: int | None = None


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
    trace: tuple[str, str] | None = None
    start: StartRecord | None = None


@dataclass(frozen=True, slots=True)
class CallerContext:
    """Read-only caller context; metadata does not establish authority."""

    identity: Identity
    metadata: FrozenObject


_COST_QUANTUM = Decimal("0.000001")


def cost_value(cost: Cost) -> dict[str, FrozenJson]:
    """Project an estimate rounded to six decimals; an unknown amount is null."""
    value: dict[str, FrozenJson] = {
        "cost": (
            float(cost.amount.quantize(_COST_QUANTUM, rounding=ROUND_HALF_EVEN))
            if cost.amount is not None
            else None
        ),
        "cost_complete": cost.amount is not None,
        "currency": cost.currency,
    }
    if cost.reference_model is not None:
        value["reference_model"] = cost.reference_model
    return value


def _model_usage_value(value: ModelUsage) -> FrozenObject:
    item: dict[str, FrozenJson] = {
        "requests": value.requests,
        "input_tokens": value.tokens.input_tokens,
        "cached_input_tokens": value.tokens.cache_read_input_tokens,
        "output_tokens": value.tokens.output_tokens,
        "reasoning_tokens": value.tokens.reasoning_output_tokens,
    }
    if value.cost is not None:
        item.update(cost_value(value.cost))
    return MappingProxyType(item)


def usage_value(value: Usage) -> FrozenObject:
    """Project measured usage once, without adding parent and child totals."""
    projected: dict[str, FrozenJson] = {
        "model_requests": value.model_requests,
        "tool_calls": value.tool_calls,
        **{field.name: getattr(value.tokens, field.name) for field in fields(value.tokens)},
    }
    cost = value.cost
    if cost is not None:
        projected.update(cost_value(cost))
    if value.by_model:
        projected["by_model"] = MappingProxyType(
            {model: _model_usage_value(usage) for model, usage in value.by_model}
        )
    return MappingProxyType(projected)


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
    value = _flow_value(record)
    value["attempt_count"] = record.attempt_count
    if record.attempts:
        value["attempts"] = tuple(
            MappingProxyType({"attempt": index, **_flow_value(attempt)})
            for index, attempt in enumerate(record.attempts, start=1)
        )
        total = record.total_usage
        if total is not None:
            value["attempts_usage"] = usage_value(total)
        elapsed = record.total_elapsed_seconds
        if elapsed is not None:
            value["attempts_elapsed_seconds"] = elapsed
    if record.stopped_by is not None:
        value["repeat"] = MappingProxyType({"stopped_by": record.stopped_by})
    return MappingProxyType(value)


def _flow_value(record: FlowRecord) -> dict[str, FrozenJson]:
    value = _record_value(record)
    steps: dict[str, FrozenJson] = {}
    for name, step in record.steps:
        if name in steps:
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        steps[name] = step_record_value(step)
    value["steps"] = MappingProxyType(steps)
    return value


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
