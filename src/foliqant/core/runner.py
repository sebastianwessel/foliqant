"""In-memory workflows with sequential flows and deterministic boundary routing."""

import asyncio
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from uuid import uuid4

from foliqant.ports.execution import FlowRole, InputValidator, StepContext, StepExecutor
from foliqant.ports.observation import ExecutionObserver, ObservationValue, TraceContext

from .admission import CapacityLimiter
from .bindings import resolve_binding, resolve_bindings
from .budget import StepBudget
from .conditions import ConditionTrace, describe_condition, evaluate_condition
from .envelope import AcceptedEnvelope
from .errors import ErrorCode, ServiceError
from .execution import (
    CallerContext,
    Failure,
    FlowRecord,
    RepeatStop,
    RouteKind,
    RunResult,
    RunStatus,
    Selection,
    StartRecord,
    StepOutcome,
    StepRecord,
    StepStatus,
    TransitionRecord,
    Usage,
    flow_record_value,
)
from .identity import Identity, validate_identity_id
from .json import FrozenJson, FrozenObject, freeze_json
from .observation import Outcome, incoming_trace, observe
from .plan import (
    MAX_COLLECTION_DEPTH,
    BindingPlan,
    CategoryPlan,
    CompiledStep,
    ConditionalRoutingPlan,
    ConditionPlan,
    DecisionIssue,
    DecisionStepPlan,
    FlowCollectionStepPlan,
    FlowPlan,
    HandlerStepPlan,
    MatchRoutingPlan,
    McpStepPlan,
    RepeatPlan,
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
class _Invocation:
    """Invocation-local identity, step counter and workflow-level flow records."""

    execution_id: str
    consumed: int = 0
    records: dict[str, FlowRecord] = field(default_factory=dict)
    active: str | None = None


@dataclass(frozen=True, slots=True)
class _Routed:
    """The final record of one routed flow and its selected boundary, if any."""

    record: FlowRecord
    target: TransitionTargetPlan | None
    transition: TransitionRecord | None
    failure: Failure | None = None


@dataclass(frozen=True, slots=True)
class _Item:
    """One collection item invocation: its ID, index, input and nesting depth."""

    id: str
    index: int
    payload: FrozenObject
    depth: int


@dataclass(frozen=True, slots=True)
class _RepeatDecision:
    """The stop reason after one attempt, or the input of the next attempt."""

    stop: RepeatStop | None
    issues: tuple[DecisionIssue, ...]
    status: StepStatus | None = None
    failure: Failure | None = None
    inputs: FrozenObject | None = None


def _failure(error: Exception) -> Failure:
    if isinstance(error, TimeoutError):
        return Failure(ErrorCode.TIMEOUT)
    if isinstance(error, ServiceError):
        return Failure(error.code, error.retryable)
    return Failure(ErrorCode.DEPENDENCY_FAILURE)


def _error_value(failure: Failure) -> FrozenObject:
    return MappingProxyType(
        {"code": failure.code.value, "message": failure.message, "retryable": failure.retryable}
    )


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
        value["error"] = _error_value(record.error)
    if isinstance(record, FlowRecord) and record.attempts:
        attempts: list[FrozenJson] = []
        for index, attempt in enumerate(record.attempts, start=1):
            item: dict[str, FrozenJson] = {"attempt": index, "status": attempt.status}
            if attempt.has_result:
                item["result"] = attempt.result
            if attempt.error is not None:
                item["error"] = _error_value(attempt.error)
            attempts.append(MappingProxyType(item))
        value["attempts"] = tuple(attempts)
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
    """Reject malformed adapter review facts before recording or further effects."""
    if not isinstance(outcome, StepOutcome) or type(outcome.needs_review) is not bool:
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    issues = outcome.unresolved_issues
    allowed = ("no_supported_answer", "conflicting_information", "multiple_valid_options")
    if (
        not isinstance(issues, tuple)
        or any(type(issue) is not str or issue not in allowed for issue in issues)
        or len(set(issues)) != len(issues)
        or (issues and not outcome.needs_review)
    ):
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    selected = outcome.selection
    if selected is not None:
        if (
            not isinstance(step, (DecisionStepPlan, HandlerStepPlan))
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


def _observed(status: StepStatus) -> StepStatus:
    return status if status in {"failed", "needs_review", "skipped"} else "completed"


def _overridden(
    base: tuple[tuple[str, BindingPlan], ...], overrides: tuple[tuple[str, BindingPlan], ...]
) -> tuple[tuple[str, BindingPlan], ...]:
    replaced = dict(overrides)
    return tuple((name, replaced.get(name, binding)) for name, binding in base)


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
        # Retry flows of routed flows are workflow-level records; a callable
        # flow's retry flow runs per collection item inside its ledger entry.
        self._retry_flows = frozenset(
            flow.repeat.retry_flow
            for flow in plan.flows
            if not flow.callable and flow.repeat is not None and flow.repeat.retry_flow is not None
        )

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
        """Run one attempt of one flow with resolved input, without routes or repeat."""
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
        """Run one step with resolved input keys; never run preceding steps or routes.

        A step's ``when`` condition belongs to its flow and is not evaluated here.
        """
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
        invocation = _Invocation(str(uuid4()))
        async with self._admission.slot(deadline=end):
            with observe(
                self._observer,
                self._plan.name,
                trace=incoming_trace(envelope.metadata),
                transport_trace=transport_trace,
                attributes={"foliqant.execution.id": invocation.execution_id},
            ) as observation:
                result = await self._run(
                    envelope, identity, end, flow_id, step_id, invocation, observation
                )
                observation.status = result.status
                observation.error = result.error.code if result.error is not None else None
                return replace(result, trace=observation.trace_ids())

    def _attributes(
        self,
        invocation: _Invocation,
        *,
        role: FlowRole | None = None,
        attempt: int | None = None,
        max_attempts: int | None = None,
        collection_index: int | None = None,
    ) -> dict[str, ObservationValue]:
        attributes: dict[str, ObservationValue] = {"foliqant.execution.id": invocation.execution_id}
        if role is not None:
            attributes["foliqant.flow.role"] = role
        if attempt is not None:
            attributes["foliqant.flow.attempt"] = attempt
        if max_attempts is not None:
            attributes["foliqant.flow.max_attempts"] = max_attempts
        if collection_index is not None:
            attributes["foliqant.collection.index"] = collection_index
        return attributes

    def _holds(
        self, condition: ConditionPlan, location: str, context: FrozenJson, observation: Outcome
    ) -> bool:
        """Evaluate a condition and report its result and type mismatches safely."""
        result, trace = self._evaluate(condition, context)
        self._report(observation, location, result, trace)
        return result

    @staticmethod
    def _evaluate(condition: ConditionPlan, context: FrozenJson) -> tuple[bool, ConditionTrace]:
        trace = ConditionTrace()
        return evaluate_condition(condition, context, trace=trace), trace

    @staticmethod
    def _report(observation: Outcome, location: str, result: bool, trace: ConditionTrace) -> None:
        observation.event("condition.evaluated", location=location, result=result)
        for mismatch in trace.mismatches:
            observation.event(
                "condition.type_mismatch",
                location=location,
                operator=mismatch.leaf.operator,
                reason=mismatch.reason,
            )

    def _select(
        self,
        route: ConditionalRoutingPlan,
        location: str,
        context: FrozenJson,
        observation: Outcome,
    ) -> tuple[TransitionTargetPlan, int]:
        for index, entry in enumerate(route.entries):
            if entry.when is None or self._holds(
                entry.when, f"{location}.{index}", context, observation
            ):
                return entry.target, index
        # Compilation requires a final otherwise entry.
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)

    def _start(self, envelope: AcceptedEnvelope, observation: Outcome) -> StartRecord:
        """Select the first flow; the result and the ``route.selected`` event agree."""
        start = self._plan.start
        if isinstance(start, str):
            selected = StartRecord(start)
        else:
            context = MappingProxyType({"payload": envelope.payload, "metadata": envelope.metadata})
            target, index = self._select(start, "start.route", context, observation)
            if target.flow is None:
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            selected = StartRecord(target.flow, "route", index)
        attributes: dict[str, ObservationValue] = {
            "kind": selected.route_kind,
            "target": selected.flow,
        }
        if selected.route_index is not None:
            attributes["index"] = selected.route_index
        observation.event("route.selected", **attributes)
        return selected

    def _target(
        self,
        flow: FlowPlan,
        record: FlowRecord,
        issues: tuple[DecisionIssue, ...],
        context: FrozenObject,
        observation: Outcome,
    ) -> tuple[TransitionTargetPlan, TransitionRecord]:
        kind: RouteKind = "direct"
        index: int | None = None
        case: str | None = None
        if record.status == "needs_review":
            kind = "review"
            route = flow.on_unresolved
            if route is None:
                target = TransitionTargetPlan(outcome="needs_review")
            elif isinstance(route, UnresolvedRoutingPlan):
                target, case = route.select(issues)
            elif isinstance(route, ConditionalRoutingPlan):
                target, index = self._select(route, "on_unresolved.route", context, observation)
            else:
                target = route
            if target.outcome == "completed":
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        else:
            transition = flow.transition
            if transition is None:
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            if isinstance(transition, MatchRoutingPlan):
                kind = "cases"
                value = resolve_binding(transition.binding, context)
                if value is not None and not isinstance(value, str):
                    raise ServiceError(ErrorCode.INVALID_INPUT)
                cases = dict(transition.cases)
                if value is not None and value in cases:
                    target, case = cases[value], value
                else:
                    target = transition.default
            elif isinstance(transition, ConditionalRoutingPlan):
                kind = "route"
                target, index = self._select(transition, "transition.route", context, observation)
            else:
                target = transition
        if (target.flow is None) == (target.outcome is None):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        attributes: dict[str, ObservationValue] = {
            "kind": kind,
            "target": target.flow or target.outcome or "",
        }
        if index is not None:
            attributes["index"] = index
        if case is not None:
            attributes["case"] = case
        observation.event("route.selected", **attributes)
        return target, TransitionRecord(
            flow.name,
            "needs_review" if record.status == "needs_review" else "completed",
            target.flow,
            target.outcome,
            kind,
            index,
            case,
        )

    async def _run(
        self,
        envelope: AcceptedEnvelope,
        identity: Identity,
        deadline: float,
        flow_id: str | None,
        step_id: str | None,
        invocation: _Invocation,
        root: Outcome,
    ) -> RunResult:
        records = invocation.records
        records.update(
            {
                flow.name: _skipped(flow)
                for flow in self._plan.flows
                if (flow_id is None and (not flow.callable or flow.name in self._retry_flows))
                or flow.name == flow_id
            }
        )
        transitions: list[TransitionRecord] = []
        payload = envelope.payload
        status: RunStatus = "completed"
        error: Failure | None = None
        visited: set[str] = set()
        start: StartRecord | None = None
        try:
            if flow_id is None:
                start = self._start(envelope, root)
            current: str | None = flow_id or (start.flow if start is not None else None)
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
                if flow_id is not None:
                    invocation.active = current
                    with observe(
                        self._observer,
                        self._plan.name,
                        flow=flow.name,
                        attributes=self._attributes(
                            invocation, role="callable" if flow.callable else "routed"
                        ),
                    ) as observation:
                        record, _ = await self._execute_flow(
                            flow,
                            envelope,
                            identity,
                            deadline,
                            invocation,
                            step_id,
                            role="callable" if flow.callable else "routed",
                        )
                        observation.status = _observed(record.status)
                        observation.error = record.error.code if record.error else None
                    records[current] = record
                    invocation.active = None
                    if record.status == "failed":
                        status, error = "failed", record.error
                    else:
                        status = "needs_review" if record.status == "needs_review" else "completed"
                        payload = record.result
                    break
                if flow.callable:
                    raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
                routed = await self._run_routed(flow, envelope, identity, deadline, invocation)
                if routed.record.status == "failed" or routed.failure is not None:
                    status, error = "failed", routed.failure or routed.record.error
                    break
                assert routed.target is not None and routed.transition is not None
                transitions.append(routed.transition)
                current = routed.target.flow
                if routed.target.outcome is not None:
                    status = routed.target.outcome
            if status != "failed" and flow_id is None and self._plan.output is not None:
                payload = resolve_binding(self._plan.output, _workflow_context(envelope, records))
            if asyncio.get_running_loop().time() >= deadline:
                raise ServiceError(ErrorCode.TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as caught:
            status, error, payload = "failed", _failure(caught), envelope.payload
            if invocation.active is not None:
                previous = records[invocation.active]
                records[invocation.active] = FlowRecord(
                    "failed",
                    _skipped(self._plan.flow(invocation.active)).steps,
                    error=error,
                    usage=Usage(),
                    elapsed_seconds=0.0,
                    attempts=previous.attempts,
                    stopped_by="failure" if previous.attempts else None,
                )
        usage = Usage()
        for _, record in records.items():
            total = record.total_usage
            if total is not None:
                usage = usage.plus(total)
        return RunResult(
            invocation.execution_id,
            self._plan.name,
            self._plan.revision,
            status,
            payload,
            envelope.metadata,
            tuple(records.items()),
            usage,
            error,
            tuple(transitions),
            start=start,
        )

    async def _run_routed(
        self,
        flow: FlowPlan,
        envelope: AcceptedEnvelope,
        identity: Identity,
        deadline: float,
        invocation: _Invocation,
    ) -> _Routed:
        """Run every attempt of one routed flow, then select its boundary target."""
        records = invocation.records
        repeat = flow.repeat
        max_attempts = repeat.max_attempts if repeat is not None else 1
        attempts: list[FlowRecord] = []
        if asyncio.get_running_loop().time() >= deadline:
            raise ServiceError(ErrorCode.TIMEOUT)
        invocation.active = flow.name
        inputs = self._flow_input(flow.name, flow.input, envelope, records)
        for attempt in range(1, max_attempts + 1):
            invocation.active = flow.name
            local = AcceptedEnvelope._from_frozen(payload=inputs, metadata=envelope.metadata)
            with observe(
                self._observer,
                self._plan.name,
                flow=flow.name,
                attributes=self._attributes(
                    invocation,
                    role="routed",
                    attempt=attempt if repeat is not None else None,
                    max_attempts=max_attempts if repeat is not None else None,
                ),
            ) as observation:
                record, issues = await self._execute_flow(
                    flow, local, identity, deadline, invocation, None, attempt=attempt
                )
                observation.status = _observed(record.status)
                observation.error = record.error.code if record.error is not None else None
                attempts.append(record)
                failure: Failure | None = None
                if repeat is None:
                    records[flow.name] = record
                else:
                    records[flow.name] = replace(record, attempts=tuple(attempts))
                    decision = await self._repeat_decision(
                        flow,
                        repeat,
                        record,
                        attempt,
                        issues,
                        envelope,
                        records,
                        identity,
                        deadline,
                        invocation,
                        observation,
                    )
                    failure, issues = decision.failure, decision.issues
                    records[flow.name] = replace(
                        record,
                        status=decision.status or record.status,
                        attempts=tuple(attempts),
                        stopped_by=decision.stop,
                    )
                    if decision.stop is None:
                        assert decision.inputs is not None
                        inputs = decision.inputs
                        continue
                    observation.event("repeat.stopped", stopped_by=decision.stop)
                invocation.active = None
                aggregated = records[flow.name]
                if aggregated.status == "failed" or failure is not None:
                    return _Routed(aggregated, None, None, failure)
                target, transition = self._target(
                    flow, aggregated, issues, _workflow_context(envelope, records), observation
                )
                return _Routed(aggregated, target, transition)
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)

    def _flow_input(
        self,
        name: str,
        bindings: tuple[tuple[str, BindingPlan], ...],
        envelope: AcceptedEnvelope,
        records: Mapping[str, FlowRecord],
    ) -> FrozenObject:
        """Resolve boundary bindings in workflow scope and validate the flow input."""
        inputs = resolve_bindings(bindings, _workflow_context(envelope, records))
        self._validator.validate_flow_input(name, inputs)
        return inputs

    async def _repeat_decision(
        self,
        flow: FlowPlan,
        repeat: RepeatPlan,
        record: FlowRecord,
        attempt: int,
        issues: tuple[DecisionIssue, ...],
        envelope: AcceptedEnvelope,
        records: dict[str, FlowRecord],
        identity: Identity,
        deadline: float,
        invocation: _Invocation,
        observation: Outcome,
        item: _Item | None = None,
    ) -> _RepeatDecision:
        """Decide after one attempt; run the retry flow and bind the next attempt.

        ``envelope`` and ``records`` form the repeat's scope: the workflow boundary
        for a routed flow, or the collection ``item`` for a callable flow. A
        technical failure of the retry flow, including its input, and of the next
        attempt's input stops the repeat with ``failure``; the repeated flow keeps
        its last attempt.
        """
        if record.status == "failed":
            return _RepeatDecision("failure", issues)
        if record.status == "needs_review":
            return _RepeatDecision("review", issues)
        if self._holds(
            repeat.until, "repeat.until", _workflow_context(envelope, records), observation
        ):
            return _RepeatDecision("until", issues)
        if attempt >= repeat.max_attempts:
            return _RepeatDecision("exhausted", issues)
        if repeat.retry_flow is not None:
            retry = self._plan.flow(repeat.retry_flow)
            previous = records.get(retry.name)
            runs = previous.attempts if previous is not None else ()
            try:
                inputs = self._flow_input(retry.name, repeat.retry_flow_input, envelope, records)
            except asyncio.CancelledError:
                raise
            except Exception as caught:
                failed = FlowRecord(
                    "failed",
                    _skipped(retry).steps,
                    error=_failure(caught),
                    usage=Usage(),
                    elapsed_seconds=0.0,
                )
                records[retry.name] = replace(failed, attempts=(*runs, failed))
                return _RepeatDecision("failure", issues, failure=failed.error)
            with observe(
                self._observer,
                self._plan.name,
                flow=retry.name,
                attributes=self._attributes(
                    invocation,
                    role="retry",
                    attempt=len(runs) + 1,
                    collection_index=item.index if item is not None else None,
                ),
            ) as retry_observation:
                retry_record, retry_issues = await self._execute_flow(
                    retry,
                    AcceptedEnvelope._from_frozen(payload=inputs, metadata=envelope.metadata),
                    identity,
                    deadline,
                    invocation,
                    None,
                    item.depth if item is not None else 0,
                    attempt=attempt,
                    role="retry",
                    collection_item=item.id if item is not None else None,
                )
                retry_observation.status = _observed(retry_record.status)
                retry_observation.error = retry_record.error.code if retry_record.error else None
            records[retry.name] = replace(retry_record, attempts=(*runs, retry_record))
            if retry_record.status == "failed":
                return _RepeatDecision("failure", issues, failure=retry_record.error)
            if retry_record.status == "needs_review":
                return _RepeatDecision("review", retry_issues, status="needs_review")
            if repeat.continue_when is not None and not self._holds(
                repeat.continue_when,
                "repeat.retry.continue_when",
                _workflow_context(envelope, records),
                observation,
            ):
                return _RepeatDecision("continue_when", issues)
        try:
            if asyncio.get_running_loop().time() >= deadline:
                raise ServiceError(ErrorCode.TIMEOUT)
            if item is None:
                bindings = _overridden(flow.input, repeat.retry_input)
                inputs = self._flow_input(flow.name, bindings, envelope, records)
            else:
                # A callable flow's next input is its item input with overridden keys.
                overrides = resolve_bindings(
                    repeat.retry_input, _workflow_context(envelope, records)
                )
                inputs = MappingProxyType({**item.payload, **overrides})
                self._validator.validate_flow_input(flow.name, inputs)
        except asyncio.CancelledError:
            raise
        except Exception as caught:
            return _RepeatDecision("failure", issues, failure=_failure(caught))
        return _RepeatDecision(None, issues, inputs=inputs)

    async def _execute_collection(
        self,
        step: FlowCollectionStepPlan,
        inputs: FrozenObject,
        metadata: FrozenObject,
        identity: Identity,
        deadline: float,
        invocation: _Invocation,
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
        retries: list[FlowRecord | None] = [None for _ in items]
        usage = Usage()
        status: RunStatus = "completed"
        failure: Failure | None = None
        for index, (item_id, flow, payload) in enumerate(items):
            record, retry, item_failure = await self._run_item(
                flow,
                _Item(item_id, index, payload, collection_depth + 1),
                metadata,
                identity,
                deadline,
                invocation,
            )
            records[index], retries[index] = record, retry
            for run in (record, retry):
                total = run.total_usage if run is not None else None
                if total is not None:
                    usage = usage.plus(total)
            if record.status == "failed" or item_failure is not None:
                status, failure = "failed", item_failure or record.error
                break
            if record.status == "needs_review":
                status = "needs_review"

        def entry(
            item_id: str, flow: FlowPlan, record: FlowRecord, retry: FlowRecord | None
        ) -> FrozenObject:
            value: dict[str, FrozenJson] = {
                "id": item_id,
                "flow": flow.name,
                **flow_record_value(record),
            }
            if retry is not None:
                # The item's retry flow runs, like `/flows/<retry>` for a routed repeat.
                value["retry"] = flow_record_value(retry)
            return MappingProxyType(value)

        ledger: FrozenObject = MappingProxyType(
            {
                "items": tuple(
                    entry(item_id, flow, record, retry)
                    for (item_id, flow, _), record, retry in zip(
                        items, records, retries, strict=True
                    )
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

    async def _run_item(
        self,
        flow: FlowPlan,
        item: _Item,
        metadata: FrozenObject,
        identity: Identity,
        deadline: float,
        invocation: _Invocation,
    ) -> tuple[FlowRecord, FlowRecord | None, Failure | None]:
        """Run one collection item, repeating it when its callable flow declares ``repeat``.

        Returns the item's flow record, its retry flow's record and a retry or
        input failure that stops the collection although the last attempt stands.
        """
        repeat = flow.repeat
        max_attempts = repeat.max_attempts if repeat is not None else 1
        scope = AcceptedEnvelope._from_frozen(payload=item.payload, metadata=metadata)
        records: dict[str, FlowRecord] = {}
        attempts: list[FlowRecord] = []
        inputs = item.payload
        for attempt in range(1, max_attempts + 1):
            with observe(
                self._observer,
                self._plan.name,
                flow=flow.name,
                attributes=self._attributes(
                    invocation,
                    role="callable",
                    attempt=attempt if repeat is not None else None,
                    max_attempts=max_attempts if repeat is not None else None,
                    collection_index=item.index,
                ),
            ) as observation:
                record, issues = await self._execute_flow(
                    flow,
                    AcceptedEnvelope._from_frozen(payload=inputs, metadata=metadata),
                    identity,
                    deadline,
                    invocation,
                    None,
                    item.depth,
                    attempt=attempt,
                    role="callable",
                    collection_item=item.id,
                )
                observation.status = _observed(record.status)
                observation.error = record.error.code if record.error else None
                if repeat is None:
                    return record, None, None
                attempts.append(record)
                records[flow.name] = replace(record, attempts=tuple(attempts))
                decision = await self._repeat_decision(
                    flow,
                    repeat,
                    record,
                    attempt,
                    issues,
                    scope,
                    records,
                    identity,
                    deadline,
                    invocation,
                    observation,
                    item,
                )
                records[flow.name] = replace(
                    record,
                    status=decision.status or record.status,
                    attempts=tuple(attempts),
                    stopped_by=decision.stop,
                )
                if decision.stop is None:
                    assert decision.inputs is not None
                    inputs = decision.inputs
                    continue
                observation.event("repeat.stopped", stopped_by=decision.stop)
                retry = records.get(repeat.retry_flow) if repeat.retry_flow is not None else None
                return records[flow.name], retry, decision.failure
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)

    async def _execute_flow(
        self,
        flow: FlowPlan,
        envelope: AcceptedEnvelope,
        identity: Identity,
        deadline: float,
        invocation: _Invocation,
        step_id: str | None,
        collection_depth: int = 0,
        *,
        attempt: int = 1,
        role: FlowRole = "routed",
        collection_item: str | None = None,
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
                attributes = self._attributes(
                    invocation,
                    attempt=attempt if flow.repeat is not None or role == "retry" else None,
                )
                guard: tuple[bool, ConditionTrace] | None = None
                location = f"steps.{step.name}.when"
                if step.when is not None and step_id is None:
                    guard = self._evaluate(step.when, _binding_context(envelope, records))
                    if not guard[0]:
                        with observe(
                            self._observer,
                            self._plan.name,
                            flow=flow.name,
                            step=step.name,
                            attributes={**attributes, "foliqant.step.skipped": True},
                        ) as skipped:
                            skipped.status = "skipped"
                            self._report(skipped, location, *guard)
                            skipped.event("step.skipped", condition=describe_condition(step.when))
                        continue
                if invocation.consumed >= self._limits.max_steps:
                    raise ServiceError(ErrorCode.BUDGET_EXHAUSTED)
                invocation.consumed += 1
                active, step_started, step_usage = (
                    step.name,
                    asyncio.get_running_loop().time(),
                    Usage(),
                )
                with observe(
                    self._observer,
                    self._plan.name,
                    flow=flow.name,
                    step=step.name,
                    attributes=attributes,
                ) as observation:
                    if guard is not None:
                        self._report(observation, location, *guard)
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
                            invocation,
                            collection_depth,
                        )
                        records[step.name] = record
                        step_usage = record.usage or Usage()
                        usage = usage.plus(step_usage)
                        active = None
                        observation.status = _observed(record.status)
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
                        invocation.execution_id,
                        self._plan.name,
                        self._plan.revision,
                        step.name,
                        CallerContext(identity, envelope.metadata),
                        deadline,
                        self._limits.model_timeout,
                        self._limits.tool_timeout,
                        budget,
                        flow.name,
                        trace=observation.carrier(),
                        attempt=attempt,
                        collection_item=collection_item,
                        flow_role=role,
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
                        issues = outcome.unresolved_issues
                        if isinstance(step, HandlerStepPlan):
                            observation.event("handler.review", issues=issues)
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
