"""Deterministic workflow transitions shared by embedded and durable execution."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from .bindings import resolve_binding, resolve_bindings
from .envelope import AcceptedEnvelope
from .errors import ErrorCode, ServiceError
from .execution import Failure, RunResult, RunStatus, StepOutcome, StepRecord, Usage
from .identity import Identity, validate_identity_id
from .json import FrozenJson, FrozenObject, freeze_json
from .plan import CompiledStep, DecisionStepPlan, FinishStepPlan, McpStepPlan, WorkflowPlan

if TYPE_CHECKING:
    from .runner import ExecutionLimits
    from .storage import Checkpoint


def validate_execution_identity(envelope: AcceptedEnvelope, identity: Identity) -> None:
    """Require accepted protected claims to equal the trusted caller identity."""
    for key in ("tenant_id", "principal_id"):
        claim = envelope.metadata.get(key)
        if key in envelope.metadata:
            validate_identity_id(claim)
        if claim != getattr(identity, key):
            raise ServiceError(ErrorCode.FORBIDDEN)


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


class ExecutionMachine:
    """Resolve inputs and routes without I/O, clocks, reservations or adapter state.

    Restored checkpoints are successful committed transitions in execution order.
    ``advance`` updates local state immediately; a durable caller must persist its
    returned checkpoint before continuing and discard the machine on conflicts.
    """

    def __init__(
        self,
        plan: WorkflowPlan,
        envelope: AcceptedEnvelope,
        *,
        identity: Identity,
        execution_id: str,
        limits: ExecutionLimits,
        checkpoints: tuple[Checkpoint, ...] = (),
    ) -> None:
        validate_execution_identity(envelope, identity)
        self._plan = plan
        self._envelope = envelope
        self._execution_id = execution_id
        self._limits = limits
        self._records: dict[str, StepRecord] = {}
        self._current: str | None = plan.start
        self._active: CompiledStep | None = None
        self._status: RunStatus = "completed"
        for checkpoint in checkpoints:
            self._restore(checkpoint)

    @property
    def current(self) -> str | None:
        """Return the next uncommitted step, or None after a terminal transition."""
        return self._current

    def _step(self) -> CompiledStep:
        if self._current is None or self._current in self._records:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if len(self._records) >= self._limits.max_steps:
            raise ServiceError(ErrorCode.BUDGET_EXHAUSTED)
        try:
            return self._plan.step(self._current)
        except KeyError:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None

    def _context(self) -> FrozenObject:
        records = {step.name: StepRecord("skipped") for step in self._plan.steps}
        records.update(self._records)
        return _binding_context(self._envelope, records)

    def prepare(self) -> tuple[CompiledStep, FrozenObject]:
        """Validate step limits and resolve inputs; finish steps have no inputs."""
        step = self._step()
        self._active = step
        if isinstance(step, FinishStepPlan):
            return step, MappingProxyType({})
        bindings = (
            step.sources
            if isinstance(step, DecisionStepPlan)
            else step.arguments
            if isinstance(step, McpStepPlan)
            else step.input
        )
        return step, resolve_bindings(bindings, self._context())

    def advance(self, outcome: StepOutcome | None) -> Checkpoint:
        """Accept one validated outcome and return its immutable selected transition."""
        from .storage import Checkpoint

        step = self._active
        if step is None or step.name != self._current:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if isinstance(step, FinishStepPlan):
            if outcome is not None:
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            record = StepRecord(step.outcome, None, True)
            target = None
        else:
            if not isinstance(outcome, StepOutcome) or type(outcome.needs_review) is not bool:
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            try:
                result = freeze_json(outcome.result)
            except ServiceError:
                raise ServiceError(ErrorCode.INVALID_OUTPUT) from None
            record = StepRecord(
                "needs_review" if outcome.needs_review else "completed", result, True
            )
            if outcome.needs_review:
                target = step.on_unresolved
            elif isinstance(step, DecisionStepPlan) and step.on_answer:
                if not isinstance(outcome.route_key, str):
                    raise ServiceError(ErrorCode.INVALID_OUTPUT)
                target = dict(step.on_answer).get(outcome.route_key)
                if target is None:
                    raise ServiceError(ErrorCode.INVALID_OUTPUT)
            else:
                target = step.next
        checkpoint = Checkpoint(step.name, record, target)
        self._commit(step, checkpoint)
        return checkpoint

    def _commit(self, step: CompiledStep, checkpoint: Checkpoint) -> None:
        self._records[step.name] = checkpoint.record
        self._current = checkpoint.next_step
        self._active = None
        if isinstance(step, FinishStepPlan):
            self._status = step.outcome
        elif checkpoint.record.status == "needs_review" and checkpoint.next_step is None:
            self._status = "needs_review"

    def _restore(self, checkpoint: Checkpoint) -> None:
        step = self._step()
        record = checkpoint.record
        if (
            checkpoint.step_id != step.name
            or record.status not in ("completed", "needs_review")
            or record.has_result is not True
            or record.error is not None
        ):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if isinstance(step, FinishStepPlan):
            targets: tuple[str | None, ...] = (None,)
            if record.status != step.outcome or record.result is not None:
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        elif record.status == "needs_review":
            targets = (step.on_unresolved,)
        elif isinstance(step, DecisionStepPlan) and step.on_answer:
            targets = tuple(target for _, target in step.on_answer)
        else:
            targets = (step.next,)
        if (
            checkpoint.next_step not in targets
            or checkpoint.next_step == step.name
            or checkpoint.next_step in self._records
        ):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if checkpoint.next_step is not None:
            try:
                self._plan.step(checkpoint.next_step)
            except KeyError:
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
        # Copy nested values even when a caller manually constructs a checkpoint.
        try:
            frozen_result = freeze_json(record.result)
        except ServiceError:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
        from .storage import Checkpoint

        frozen = Checkpoint(
            checkpoint.step_id, StepRecord(record.status, frozen_result, True), checkpoint.next_step
        )
        self._commit(step, frozen)

    def result(
        self, usage: Usage, *, failure: Failure | None = None, cancelled: bool = False
    ) -> RunResult:
        """Build a terminal result, preserving committed records and failure input."""
        error = Failure(ErrorCode.CANCELLED) if cancelled else failure
        if error is None and self._current is not None:
            error = Failure(ErrorCode.INVALID_CONFIGURATION)
        payload = self._envelope.payload
        if error is None and self._plan.output is not None:
            try:
                payload = resolve_binding(self._plan.output, self._context())
            except ServiceError as caught:
                error = Failure(caught.code, caught.retryable)
        status: RunStatus = "cancelled" if cancelled else "failed" if error else self._status
        records = dict(self._records)
        if error is not None and self._current is not None and self._current not in records:
            records[self._current] = StepRecord("cancelled" if cancelled else "failed", error=error)
        for step in self._plan.steps:
            records.setdefault(step.name, StepRecord("skipped"))
        return RunResult(
            self._execution_id,
            self._plan.name,
            self._plan.revision,
            status,
            payload,
            self._envelope.metadata,
            tuple(records.items()),
            usage,
            error,
        )
