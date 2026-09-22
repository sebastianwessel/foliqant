"""Execution ports share immutable core values, never provider-specific models."""

from dataclasses import dataclass
from typing import Protocol

from foliqant.core.execution import CallerContext, StepOutcome, TokenUsage, Usage
from foliqant.core.json import FrozenJson, FrozenObject
from foliqant.core.plan import DecisionStepPlan, HandlerStepPlan, LlmStepPlan, McpStepPlan

type OperationStep = DecisionStepPlan | LlmStepPlan | McpStepPlan | HandlerStepPlan


class AttemptBudget(Protocol):
    """Adapters reserve every request before I/O; snapshots are local observations."""

    async def start_model_request(self) -> int: ...

    async def finish_model_request(self, ticket: int, usage: TokenUsage) -> None: ...

    async def start_tool_call(self) -> int: ...

    def snapshot(self) -> Usage: ...


@dataclass(frozen=True, slots=True)
class StepContext:
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


class StepExecutor(Protocol):
    """Own validation, authorization and bounded I/O for one operation step."""

    async def execute(
        self, step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome: ...


class InputValidator(Protocol):
    """Precompiled, pure bounded validation; this method performs no I/O."""

    def validate_input(self, payload: FrozenJson) -> None: ...

    def validate_flow_input(self, flow_id: str, payload: FrozenJson) -> None: ...
