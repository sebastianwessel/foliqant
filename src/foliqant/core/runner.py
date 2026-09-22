"""In-memory workflows with sequential flows and deterministic boundary routing."""

import asyncio
import math
import re
from collections.abc import Mapping
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
from .execution import (
    CallerContext,
    Failure,
    FlowRecord,
    RunResult,
    RunStatus,
    Selection,
    StepOutcome,
    StepRecord,
    TransitionRecord,
    Usage,
    flow_record_value,
)
from .identity import Identity, validate_identity_id
from .json import FrozenJson, FrozenObject, freeze_json
from .observation import incoming_trace, observe
from .plan import (
    MAX_COLLECTION_DEPTH,
    BindingPlan,
    CategoryPlan,
    CompiledStep,
    DecisionIssue,
    DecisionStepPlan,
    FlowCollectionStepPlan,
    FlowPlan,
    MatchRoutingPlan,
    McpStepPlan,
    TransitionTargetPlan,
    UnresolvedRoutingPlan,
    WorkflowPlan,
)


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
_ITEM_ID = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", re.ASCII)


@dataclass(slots=True)
class _RunBudget:
    """Invocation-local counter shared by all flows, including isolated evaluation."""

    consumed: int = 0


def _failure(error: Exception) -> Failure:
    if isinstance(error, TimeoutError):
        return Failure(ErrorCode.TIMEOUT)
    if isinstance(error, ServiceError):
        return Failure(error.code, error.retryable)
    return Failure(ErrorCode.DEPENDENCY_FAILURE)


def _record_data(record: StepRecord | FlowRecord) -> FrozenObject:
    value: dict[str, FrozenJson] = {"status": record.status}
    if record.has_result:
        value["result"] = record.result
    if isinstance(record, StepRecord) and record.kind is not None:
        value["kind"] = record.kind
    if isinstance(record, StepRecord) and record.selection is not None:
        value["selection"] = record.selection.as_json()
    if isinstance(record, StepRecord) and record.partial_result is not None:
        value["partial_result"] = record.partial_result
    if record.error is not None:
        value["error"] = MappingProxyType(
            {
                "code": record.error.code.value,
                "message": record.error.message,
                "retryable": record.error.retryable,
            }
        )
    return MappingProxyType(value)


def _binding_context(envelope: AcceptedEnvelope, records: Mapping[str, StepRecord]) -> FrozenObject:
    return MappingProxyType(
        {
            "payload": envelope.payload,
            "metadata": envelope.metadata,
            "steps": MappingProxyType(
                {name: _record_data(record) for name, record in records.items()}
            ),
        }
    )


def _workflow_context(
    envelope: AcceptedEnvelope, records: Mapping[str, FlowRecord]
) -> FrozenObject:
    return MappingProxyType(
        {
            "payload": envelope.payload,
            "metadata": envelope.metadata,
            "flows": MappingProxyType(
                {name: _record_data(record) for name, record in records.items()}
            ),
        }
    )


def _step_bindings(step: CompiledStep) -> tuple[tuple[str, BindingPlan], ...]:
    return (
        step.sources
        if isinstance(step, DecisionStepPlan)
        else step.arguments
        if isinstance(step, McpStepPlan)
        else step.input
    )


def _validate_outcome(outcome: object, step: CompiledStep) -> StepOutcome:
    """Reject malformed adapter control facts before recording or further effects."""
    if not isinstance(outcome, StepOutcome) or type(outcome.needs_review) is not bool:
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    issues = outcome.unresolved_issues
    allowed = ("no_supported_answer", "conflicting_information", "multiple_valid_options")
    if (
        not isinstance(issues, tuple)
        or any(type(issue) is not str or issue not in allowed for issue in issues)
        or len(set(issues)) != len(issues)
    ):
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    selected = outcome.selection
    if selected is not None:
        if (
            not isinstance(step, DecisionStepPlan)
            or not isinstance(selected, Selection)
            or not isinstance(selected.category, CategoryPlan)
            or type(selected.category.id) is not str
            or not selected.category.id.strip()
            or len(selected.category.id) > 128
            or (
                selected.category.description is not None
                and (
                    type(selected.category.description) is not str
                    or not selected.category.description
                )
            )
            or selected.origin not in ("model", "fallback")
            or outcome.needs_review != (selected.origin == "fallback")
        ):
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
    return outcome


def _skipped(flow: FlowPlan) -> FlowRecord:
    return FlowRecord("skipped", tuple((step.name, _skipped_step(step)) for step in flow.steps))


def _skipped_step(step: CompiledStep) -> StepRecord:
    return StepRecord(
        "skipped", kind="flow_collection" if isinstance(step, FlowCollectionStepPlan) else None
    )


class WorkflowRunner:
    """Execute one authored graph with one admission slot, deadline and step budget.

    Each flow has its own payload and local step records. Data crosses flow
    boundaries only through configured bindings. No conversation state is shared.
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
        self._plan, self._executor, self._validator = plan, executor, validator
        self._admission, self._limits, self._observer = admission, limits, observer

    async def run(
        self,
        envelope: AcceptedEnvelope,
        *,
        identity: Identity,
        deadline: float | None = None,
        transport_trace: TraceContext | None = None,
    ) -> RunResult:
        """Run the whole workflow; cancellation propagates after adapter cleanup."""
        return await self._admit(
            envelope, identity=identity, deadline=deadline, transport_trace=transport_trace
        )

    async def run_flow(
        self,
        flow_id: str,
        envelope: AcceptedEnvelope,
        *,
        identity: Identity,
        deadline: float | None = None,
        transport_trace: TraceContext | None = None,
    ) -> RunResult:
        """Run one flow with resolved flow input, without workflow transitions."""
        self._flow(flow_id)
        return await self._admit(
            envelope,
            identity=identity,
            deadline=deadline,
            transport_trace=transport_trace,
            flow_id=flow_id,
        )

    async def run_step(
        self,
        flow_id: str,
        step_id: str,
        envelope: AcceptedEnvelope,
        *,
        identity: Identity,
        deadline: float | None = None,
        transport_trace: TraceContext | None = None,
    ) -> RunResult:
        """Run one step with resolved input keys; never run preceding steps or routes."""
        try:
            step = self._flow(flow_id).step(step_id)
        except KeyError:
            raise ServiceError(ErrorCode.NOT_FOUND) from None
        if not isinstance(envelope.payload, Mapping) or set(envelope.payload) != {
            key for key, _ in _step_bindings(step)
        }:
            raise ServiceError(ErrorCode.INVALID_INPUT)
        return await self._admit(
            envelope,
            identity=identity,
            deadline=deadline,
            transport_trace=transport_trace,
            flow_id=flow_id,
            step_id=step_id,
        )

    def _flow(self, name: str) -> FlowPlan:
        try:
            return self._plan.flow(name)
        except KeyError:
            raise ServiceError(ErrorCode.NOT_FOUND) from None

    async def _admit(
        self,
        envelope: AcceptedEnvelope,
        *,
        identity: Identity,
        deadline: float | None,
        transport_trace: TraceContext | None,
        flow_id: str | None = None,
        step_id: str | None = None,
    ) -> RunResult:
        end = asyncio.get_running_loop().time() + self._limits.run_timeout
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
            if flow_id is None:
                self._validator.validate_input(envelope.payload)
            elif step_id is None:
                self._validator.validate_flow_input(flow_id, envelope.payload)
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
                result = await self._run(envelope, identity, end, flow_id, step_id)
                observation.status = result.status
                observation.error = result.error.code if result.error is not None else None
                return result

    def _target(
        self,
        flow: FlowPlan,
        record: FlowRecord,
        issues: tuple[DecisionIssue, ...],
        context: FrozenObject,
    ) -> TransitionTargetPlan:
        if record.status == "needs_review":
            target = flow.on_unresolved
            if isinstance(target, UnresolvedRoutingPlan):
                target = target.target(issues)
            target = target or TransitionTargetPlan(outcome="needs_review")
            if target.outcome == "completed":
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            return target
        route = flow.transition
        if route is None:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if isinstance(route, MatchRoutingPlan):
            value = resolve_binding(route.binding, context)
            if value is not None and not isinstance(value, str):
                raise ServiceError(ErrorCode.INVALID_INPUT)
            return (
                dict(route.cases).get(value, route.default) if value is not None else route.default
            )
        return route

    async def _run(
        self,
        envelope: AcceptedEnvelope,
        identity: Identity,
        deadline: float,
        flow_id: str | None,
        step_id: str | None,
    ) -> RunResult:
        execution_id = str(uuid4())
        records = {
            flow.name: _skipped(flow)
            for flow in self._plan.flows
            if (flow_id is None and not flow.callable) or flow.name == flow_id
        }
        transitions: list[TransitionRecord] = []
        budget = _RunBudget()
        usage = Usage()
        payload = envelope.payload
        status: RunStatus = "completed"
        error: Failure | None = None
        current: str | None = flow_id or self._plan.start
        visited: set[str] = set()
        active: str | None = None
        try:
            while current is not None:
                if asyncio.get_running_loop().time() >= deadline:
                    raise ServiceError(ErrorCode.TIMEOUT)
                if current in visited or len(visited) >= len(self._plan.flows):
                    raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
                visited.add(current)
                try:
                    flow = self._plan.flow(current)
                except KeyError:
                    raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
                active = current
                if flow_id is None:
                    if flow.callable:
                        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
                    inputs = resolve_bindings(flow.input, _workflow_context(envelope, records))
                    local = AcceptedEnvelope._from_frozen(
                        payload=inputs, metadata=envelope.metadata
                    )
                    self._validator.validate_flow_input(current, inputs)
                else:
                    local = envelope
                with observe(self._observer, self._plan.name, flow=flow.name) as observation:
                    record, issues = await self._execute_flow(
                        flow, local, identity, deadline, execution_id, budget, step_id
                    )
                    observation.status = (
                        "failed"
                        if record.status == "failed"
                        else "needs_review"
                        if record.status == "needs_review"
                        else "completed"
                    )
                    observation.error = record.error.code if record.error is not None else None
                records[current] = record
                if record.usage is not None:
                    usage = usage.plus(record.usage)
                active = None
                if record.status == "failed":
                    status, error = "failed", record.error
                    break
                if flow_id is not None:
                    status = "needs_review" if record.status == "needs_review" else "completed"
                    payload = record.result
                    break
                target = self._target(flow, record, issues, _workflow_context(envelope, records))
                if (target.flow is None) == (target.outcome is None):
                    raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
                transitions.append(
                    TransitionRecord(
                        current,
                        "needs_review" if record.status == "needs_review" else "completed",
                        target.flow,
                        target.outcome,
                    )
                )
                current = target.flow
                if target.outcome is not None:
                    status = target.outcome
            if status != "failed" and flow_id is None and self._plan.output is not None:
                payload = resolve_binding(self._plan.output, _workflow_context(envelope, records))
            if asyncio.get_running_loop().time() >= deadline:
                raise ServiceError(ErrorCode.TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as caught:
            status, error, payload = "failed", _failure(caught), envelope.payload
            if active is not None:
                previous = records[active]
                records[active] = FlowRecord(
                    "failed", previous.steps, error=error, usage=Usage(), elapsed_seconds=0.0
                )
        return RunResult(
            execution_id,
            self._plan.name,
            self._plan.revision,
            status,
            payload,
            envelope.metadata,
            tuple(records.items()),
            usage,
            error,
            tuple(transitions),
        )

    async def _execute_collection(
        self,
        step: FlowCollectionStepPlan,
        inputs: FrozenObject,
        metadata: FrozenObject,
        identity: Identity,
        deadline: float,
        execution_id: str,
        run_budget: _RunBudget,
        collection_depth: int,
    ) -> StepRecord:
        """Validate the entire batch before executing independent child flows."""
        started = asyncio.get_running_loop().time()
        if collection_depth >= MAX_COLLECTION_DEPTH:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        raw = inputs.get("items")
        if set(inputs) != {"items"} or not isinstance(raw, tuple) or len(raw) > step.max_items:
            raise ServiceError(ErrorCode.INVALID_INPUT)
        items: list[tuple[str, FlowPlan, FrozenObject]] = []
        seen: set[str] = set()
        for item in raw:
            if asyncio.get_running_loop().time() >= deadline:
                raise ServiceError(ErrorCode.TIMEOUT)
            if not isinstance(item, Mapping) or set(item) != {"id", "flow", "input"}:
                raise ServiceError(ErrorCode.INVALID_INPUT)
            item_id, flow_id, payload = item["id"], item["flow"], item["input"]
            if (
                type(item_id) is not str
                or _ITEM_ID.fullmatch(item_id) is None
                or item_id in seen
                or type(flow_id) is not str
                or flow_id not in step.flows
                or not isinstance(payload, Mapping)
            ):
                raise ServiceError(ErrorCode.INVALID_INPUT)
            seen.add(item_id)
            try:
                flow = self._plan.flow(flow_id)
            except KeyError:
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
            if not flow.callable:
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            self._validator.validate_flow_input(flow_id, payload)
            items.append((item_id, flow, payload))
        records = [_skipped(flow) for _, flow, _ in items]
        usage = Usage()
        status: RunStatus = "completed"
        failure: Failure | None = None
        for index, (_, flow, payload) in enumerate(items):
            with observe(self._observer, self._plan.name, flow=flow.name) as observation:
                record, _ = await self._execute_flow(
                    flow,
                    AcceptedEnvelope._from_frozen(payload=payload, metadata=metadata),
                    identity,
                    deadline,
                    execution_id,
                    run_budget,
                    None,
                    collection_depth + 1,
                )
                observation.status = (
                    "failed"
                    if record.status == "failed"
                    else "needs_review"
                    if record.status == "needs_review"
                    else "completed"
                )
                observation.error = record.error.code if record.error else None
            records[index] = record
            if record.usage is not None:
                usage = usage.plus(record.usage)
            if record.status == "failed":
                status, failure = "failed", record.error
                break
            if record.status == "needs_review":
                status = "needs_review"
        ledger: FrozenObject = MappingProxyType(
            {
                "items": tuple(
                    MappingProxyType(
                        {"id": item_id, "flow": flow.name, **flow_record_value(record)}
                    )
                    for (item_id, flow, _), record in zip(items, records, strict=True)
                ),
            }
        )
        return StepRecord(
            status,
            result=ledger if status != "failed" else None,
            has_result=status != "failed",
            error=failure,
            usage=usage,
            elapsed_seconds=asyncio.get_running_loop().time() - started,
            partial_result=ledger if status == "failed" else None,
            kind="flow_collection",
        )

    async def _execute_flow(
        self,
        flow: FlowPlan,
        envelope: AcceptedEnvelope,
        identity: Identity,
        deadline: float,
        execution_id: str,
        run_budget: _RunBudget,
        step_id: str | None,
        collection_depth: int = 0,
    ) -> tuple[FlowRecord, tuple[DecisionIssue, ...]]:
        steps = tuple(step for step in flow.steps if step_id is None or step.name == step_id)
        records = {step.name: _skipped_step(step) for step in steps}
        usage = Usage()
        status: RunStatus = "completed"
        error: Failure | None = None
        result = envelope.payload
        has_result = False
        active: str | None = None
        started = asyncio.get_running_loop().time()
        step_started = started
        step_usage = Usage()
        issues: tuple[DecisionIssue, ...] = ()
        try:
            for step in steps:
                await asyncio.sleep(0)
                if asyncio.get_running_loop().time() >= deadline:
                    raise ServiceError(ErrorCode.TIMEOUT)
                if run_budget.consumed >= self._limits.max_steps:
                    raise ServiceError(ErrorCode.BUDGET_EXHAUSTED)
                run_budget.consumed += 1
                active, step_started, step_usage = (
                    step.name,
                    asyncio.get_running_loop().time(),
                    Usage(),
                )
                with observe(
                    self._observer, self._plan.name, flow=flow.name, step=step.name
                ) as observation:
                    if step_id is not None:
                        if not isinstance(envelope.payload, Mapping):
                            raise ServiceError(ErrorCode.INVALID_INPUT)
                        inputs = envelope.payload
                    else:
                        inputs = resolve_bindings(
                            _step_bindings(step), _binding_context(envelope, records)
                        )
                    if isinstance(step, FlowCollectionStepPlan):
                        record = await self._execute_collection(
                            step,
                            inputs,
                            envelope.metadata,
                            identity,
                            deadline,
                            execution_id,
                            run_budget,
                            collection_depth,
                        )
                        records[step.name] = record
                        step_usage = record.usage or Usage()
                        usage = usage.plus(step_usage)
                        active = None
                        observation.status = (
                            "failed"
                            if record.status == "failed"
                            else "needs_review"
                            if record.status == "needs_review"
                            else "completed"
                        )
                        observation.error = record.error.code if record.error else None
                        if record.status == "failed":
                            status, error, result = "failed", record.error, None
                            break
                        if step_id is not None:
                            result = record.result
                        if record.status == "needs_review":
                            status = "needs_review"
                            break
                        continue
                    budget = StepBudget(
                        model_requests=self._limits.model_requests_per_step,
                        tool_calls=self._limits.tool_calls_per_step,
                    )
                    context = StepContext(
                        execution_id,
                        self._plan.name,
                        self._plan.revision,
                        step.name,
                        CallerContext(identity, envelope.metadata),
                        deadline,
                        self._limits.model_timeout,
                        self._limits.tool_timeout,
                        budget,
                        flow.name,
                    )
                    try:
                        async with asyncio.timeout_at(deadline):
                            outcome = await self._executor.execute(step, inputs, context)
                    finally:
                        step_usage = budget.snapshot()
                        usage = usage.plus(step_usage)
                    if asyncio.get_running_loop().time() >= deadline:
                        raise ServiceError(ErrorCode.TIMEOUT)
                    outcome = _validate_outcome(outcome, step)
                    try:
                        value = freeze_json(outcome.result)
                    except ServiceError:
                        raise ServiceError(ErrorCode.INVALID_OUTPUT) from None
                    observation.status = "needs_review" if outcome.needs_review else "completed"
                    records[step.name] = StepRecord(
                        observation.status,
                        value,
                        True,
                        elapsed_seconds=asyncio.get_running_loop().time() - step_started,
                        usage=step_usage,
                        selection=outcome.selection,
                    )
                    active = None
                    if step_id is not None:
                        result = value
                    if outcome.needs_review:
                        status = "needs_review"
                        issues = (
                            outcome.unresolved_issues if isinstance(step, DecisionStepPlan) else ()
                        )
                        break
            if status != "failed" and step_id is None and flow.output is not None:
                result = resolve_binding(flow.output, _binding_context(envelope, records))
            has_result = status != "failed"
        except asyncio.CancelledError:
            raise
        except Exception as caught:
            status, error, result = "failed", _failure(caught), None
            if active is not None:
                records[active] = StepRecord(
                    "failed",
                    error=error,
                    elapsed_seconds=asyncio.get_running_loop().time() - step_started,
                    usage=step_usage,
                    kind=records[active].kind,
                )
        return FlowRecord(
            status,
            tuple(records.items()),
            result,
            has_result,
            usage,
            asyncio.get_running_loop().time() - started,
            error,
        ), issues
