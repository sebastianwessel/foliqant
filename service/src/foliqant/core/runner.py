"""Nondurable embedded workflow execution with deterministic, bounded routing."""

import asyncio
import math
from dataclasses import dataclass
from uuid import uuid4

from foliqant.ports.execution import InputValidator, StepContext, StepExecutor
from foliqant.ports.observation import ExecutionObserver, TraceContext

from .admission import CapacityLimiter
from .budget import StepBudget
from .envelope import AcceptedEnvelope
from .errors import ErrorCode, ServiceError
from .execution import CallerContext, Failure, RunResult, Usage
from .identity import Identity
from .machine import ExecutionMachine, validate_execution_identity
from .observation import incoming_trace, observe
from .plan import FinishStepPlan, WorkflowPlan


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
        validate_execution_identity(envelope, identity)
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
        machine = ExecutionMachine(
            plan, envelope, identity=identity, execution_id=execution_id, limits=self._limits
        )
        usage = Usage()
        error: Failure | None = None
        try:
            async with asyncio.timeout_at(deadline):
                while machine.current is not None:
                    await asyncio.sleep(0)
                    if asyncio.get_running_loop().time() >= deadline:
                        raise ServiceError(ErrorCode.TIMEOUT)
                    with observe(
                        self._observer, plan.name, step=machine.current
                    ) as step_observation:
                        step, inputs = machine.prepare()
                        if isinstance(step, FinishStepPlan):
                            checkpoint = machine.advance(None)
                            step_observation.status = step.outcome
                            continue
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
                        checkpoint = machine.advance(outcome)
                        step_observation.status = (
                            "needs_review"
                            if checkpoint.record.status == "needs_review"
                            else "completed"
                        )
                result = machine.result(usage)
                if asyncio.get_running_loop().time() >= deadline:
                    raise ServiceError(ErrorCode.TIMEOUT)
                return result
        except asyncio.CancelledError:
            raise
        except Exception as caught:
            if isinstance(caught, TimeoutError):
                error = Failure(ErrorCode.TIMEOUT)
            elif isinstance(caught, ServiceError):
                error = Failure(caught.code, caught.retryable)
            else:
                error = Failure(ErrorCode.DEPENDENCY_FAILURE)
        return machine.result(usage, failure=error)
