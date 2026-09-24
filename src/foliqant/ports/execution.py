"""Execution ports share immutable core values, never provider-specific models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol

from foliqant.core.execution import CallerContext, StepOutcome, TokenUsage, Usage
from foliqant.core.json import FrozenJson, FrozenObject
from foliqant.core.plan import DecisionStepPlan, HandlerStepPlan, LlmStepPlan, McpStepPlan
from foliqant.core.pricing import PricingPlan

type OperationStep = DecisionStepPlan | LlmStepPlan | McpStepPlan | HandlerStepPlan


class AttemptBudget(Protocol):
    """Adapters reserve every request before I/O; snapshots are local observations.

    ``model`` is the provider model ID the request is sent to; ``pricing``, when
    configured for it, estimates the request's cost from its reported usage.
    """

    async def start_model_request(self, model: str, pricing: PricingPlan | None = None) -> int: ...

    async def finish_model_request(self, ticket: int, usage: TokenUsage) -> None: ...

    async def start_tool_call(self) -> int: ...

    def snapshot(self) -> Usage: ...


type FlowRole = Literal["routed", "callable", "retry"]


def _empty_carrier() -> Mapping[str, str]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class StepContext:
    """Invocation facts for one step; never business routing authority.

    ``trace`` is the W3C carrier (``traceparent``/``tracestate``) of the current
    step span, empty without telemetry; handlers may forward it on their own
    outbound calls. ``attempt`` is the repeat attempt (1 when not repeated),
    ``collection_item`` the item ID inside a collection and ``flow_role`` how the
    flow was invoked.
    """

    execution_id: str
    workflow: str
    revision: str
    step_id: str
    caller: CallerContext
    deadline: float
    model_timeout: float
    tool_timeout: float
    budget: AttemptBudget
    flow_id: str
    trace: Mapping[str, str] = field(default_factory=_empty_carrier)
    attempt: int = 1
    collection_item: str | None = None
    flow_role: FlowRole = "routed"


class StepExecutor(Protocol):
    """Own validation, authorization and bounded I/O for one operation step."""

    async def execute(
        self, step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome: ...


class InputValidator(Protocol):
    """Precompiled, pure bounded validation; this method performs no I/O."""

    def validate_input(self, payload: FrozenJson) -> None: ...

    def validate_flow_input(self, flow_id: str, payload: FrozenJson) -> None: ...
