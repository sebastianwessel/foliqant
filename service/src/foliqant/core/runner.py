"""Nondurable embedded workflow execution with deterministic, bounded routing."""

import asyncio
import math
from dataclasses import dataclass
from types import MappingProxyType
from uuid import uuid4

from foliqant.ports.execution import InputValidator, StepContext, StepExecutor
from foliqant.ports.observation import ExecutionObserver, TraceContext

from .admission import CapacityLimiter
from .bindings import resolve_binding, resolve_bindings
from .budget import StepBudget
from .envelope import AcceptedEnvelope
from .errors import ErrorCode, ServiceError
from .execution import CallerContext, Failure, RunResult, RunStatus, StepOutcome, StepRecord, Usage
from .identity import Identity, validate_identity_id
from .json import FrozenJson, FrozenObject, freeze_json
from .observation import incoming_trace, observe
from .plan import DecisionStepPlan, FinishStepPlan, McpStepPlan, WorkflowPlan


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    run_timeout: float = 300.0
    model_timeout: float = 60.0
    tool_timeout: float = 30.0
    max_steps: int = 32
    model_requests_per_step: int = 4
    tool_calls_per_step: int = 3

    def __post_init__(self) -> None:
        for duration in (self.run_timeout, self.model_timeout, self.tool_timeout):
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
                raise ValueError("timeouts must be finite and positive")
        for value in (self.max_steps, self.model_requests_per_step, self.tool_calls_per_step):
            if type(value) is not int or value < 1:
                raise ValueError("execution limits must be positive integers")


_DEFAULT_LIMITS = ExecutionLimits()


def _binding_context(envelope: AcceptedEnvelope, records: dict[str, StepRecord]) -> FrozenObject:
    steps: dict[str, FrozenJson] = {}
    for name, record in records.items():
        value: dict[str, FrozenJson] = {"status": record.status}
        if record.has_result:
            value["result"] = record.result
        if record.error is not None:
            value["error"] = MappingProxyType(
                {
                    "code": record.error.code.value,
                    "message": record.error.message,
                    "retryable": record.error.retryable,
                }
            )
        steps[name] = MappingProxyType(value)
    return MappingProxyType(
        {
            "payload": envelope.payload,
            "metadata": envelope.metadata,
            "steps": MappingProxyType(steps),
        }
    )


class WorkflowRunner:
    """Run a compiled workflow without persistence or hidden background execution.

    Share a configured admission limiter across runners when they compete for
    the same intake resource. Each run gets independent identity, budget and data.
    Provider, handler and MCP effects belong to the injected StepExecutor.
    """

    def __init__(
        self,
        plan: WorkflowPlan,
        *,
        executor: StepExecutor,
        validator: InputValidator,
        admission: CapacityLimiter,
        limits: ExecutionLimits = _DEFAULT_LIMITS,
        observer: ExecutionObserver | None = None,
    ) -> None:
        self._plan = plan
        self._executor = executor
        self._validator = validator
        self._admission = admission
        self._limits = limits
        self._observer = observer

    async def run(
        self,
        envelope: AcceptedEnvelope,
        *,
        identity: Identity,
        deadline: float | None = None,
        transport_trace: TraceContext | None = None,
    ) -> RunResult:
        """Validate and admit a run; boundary failures raise and execution failures return.

        An explicit transport carrier takes precedence over envelope telemetry
        when valid. Carriers are never merged or used for authorization.

        CancelledError always propagates. This embedded runner has no durable
        cancellation record; a production worker must persist that separately.
        """
        loop = asyncio.get_running_loop()
        end = loop.time() + self._limits.run_timeout
        if deadline is not None:
            if not math.isfinite(deadline):
                raise ServiceError(ErrorCode.INVALID_INPUT)
            end = min(end, deadline)
        for key in ("tenant_id", "principal_id"):
            claim = envelope.metadata.get(key)
            if key in envelope.metadata:
                validate_identity_id(claim)
            if claim != getattr(identity, key):
                raise ServiceError(ErrorCode.FORBIDDEN)
        try:
            self._validator.validate_input(envelope.payload)
        except ServiceError:
            raise
        except Exception:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None
        async with self._admission.slot(deadline=end):
            with observe(
                self._observer,
                self._plan.name,
                trace=incoming_trace(envelope.metadata),
                transport_trace=transport_trace,
            ) as observation:
                result = await self._run(envelope, identity=identity, deadline=end)
                observation.status = result.status
                observation.error = result.error.code if result.error is not None else None
                return result

    async def _run(
        self,
        envelope: AcceptedEnvelope,
        *,
        identity: Identity,
        deadline: float,
    ) -> RunResult:
        plan = self._plan
        execution_id = str(uuid4())
        records = {step.name: StepRecord("skipped") for step in plan.steps}
        usage = Usage()
        status: RunStatus = "completed"
        error: Failure | None = None
        payload = envelope.payload
        current: str | None = plan.start
        active: str | None = None
        visited: set[str] = set()
        try:
            async with asyncio.timeout_at(deadline):
                while current is not None:
                    await asyncio.sleep(0)
                    if asyncio.get_running_loop().time() >= deadline:
                        raise ServiceError(ErrorCode.TIMEOUT)
                    if len(visited) >= self._limits.max_steps:
                        raise ServiceError(ErrorCode.BUDGET_EXHAUSTED)
                    if current in visited:
                        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
                    visited.add(current)
                    try:
                        step = plan.step(current)
                    except KeyError:
                        raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
                    active = current
                    with observe(self._observer, plan.name, step=step.name) as step_observation:
                        if isinstance(step, FinishStepPlan):
                            status = step.outcome
                            step_observation.status = status
                            records[current] = StepRecord(status, None, True)
                            active = None
                            break
                        bindings = (
                            step.sources
                            if isinstance(step, DecisionStepPlan)
                            else step.arguments
                            if isinstance(step, McpStepPlan)
                            else step.input
                        )
                        inputs = resolve_bindings(bindings, _binding_context(envelope, records))
                        budget = StepBudget(
                            model_requests=self._limits.model_requests_per_step,
                            tool_calls=self._limits.tool_calls_per_step,
                        )
                        context = StepContext(
                            execution_id,
                            plan.name,
                            plan.revision,
                            step.name,
                            CallerContext(identity, envelope.metadata),
                            deadline,
                            self._limits.model_timeout,
                            self._limits.tool_timeout,
                            budget,
                        )
                        try:
                            outcome = await self._executor.execute(step, inputs, context)
                        finally:
                            usage = usage.plus(budget.snapshot())
                        if asyncio.get_running_loop().time() >= deadline:
                            raise ServiceError(ErrorCode.TIMEOUT)
                        if (
                            not isinstance(outcome, StepOutcome)
                            or type(outcome.needs_review) is not bool
                        ):
                            raise ServiceError(ErrorCode.INVALID_OUTPUT)
                        try:
                            result = freeze_json(outcome.result)
                        except ServiceError:
                            raise ServiceError(ErrorCode.INVALID_OUTPUT) from None
                        step_observation.status = (
                            "needs_review" if outcome.needs_review else "completed"
                        )
                        records[current] = StepRecord(step_observation.status, result, True)
                        if outcome.needs_review:
                            current = step.on_unresolved
                            if current is None:
                                status = "needs_review"
                        elif isinstance(step, DecisionStepPlan) and step.on_answer:
                            target = dict(step.on_answer).get(outcome.route_key or "")
                            if target is None:
                                raise ServiceError(ErrorCode.INVALID_OUTPUT)
                            current = target
                        else:
                            current = step.next
                        active = None
                if plan.output is not None:
                    payload = resolve_binding(plan.output, _binding_context(envelope, records))
                if asyncio.get_running_loop().time() >= deadline:
                    raise ServiceError(ErrorCode.TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as caught:
            if isinstance(caught, TimeoutError):
                error = Failure(ErrorCode.TIMEOUT)
            elif isinstance(caught, ServiceError):
                error = Failure(caught.code, caught.retryable)
            else:
                error = Failure(ErrorCode.DEPENDENCY_FAILURE)
            status = "failed"
            payload = envelope.payload
            if active is not None:
                records[active] = StepRecord("failed", error=error)
        return RunResult(
            execution_id,
            plan.name,
            plan.revision,
            status,
            payload,
            envelope.metadata,
            tuple(records.items()),
            usage,
            error,
        )
