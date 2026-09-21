"""Bounded read-only workflow recovery over an injected durable execution store."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from foliqant.adapters.storage.budget import PersistentStepBudget
from foliqant.adapters.telemetry.logging import LogEvent, emit_event
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import CallerContext, Failure, RunResult
from foliqant.core.machine import ExecutionMachine, validate_execution_identity
from foliqant.core.observation import incoming_trace, observe
from foliqant.core.plan import FinishStepPlan, WorkflowPlan
from foliqant.core.storage import ClaimedExecution, StoredExecution
from foliqant.ports.execution import InputValidator, StepContext, StepExecutor
from foliqant.ports.observation import ExecutionObserver
from foliqant.ports.storage import ExecutionStore

from .budget import WorkerBudget

_LOGGER = logging.getLogger("foliqant.worker")
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class WorkflowBinding:
    """Exact revision and trusted read-only adapters owned by the embedding host."""

    plan: WorkflowPlan
    executor: StepExecutor
    validator: InputValidator
    observer: ExecutionObserver | None = None


@dataclass(slots=True)
class _ClaimScope:
    stopped: bool = True


class ExecutionWorker:
    """Own bounded recovery tasks; hosts retain store and external-client ownership.

    Use ``await worker.run_once()`` for one poll, or ``await worker.serve(stop)``
    for fixed concurrent polling loops. Always drain with ``await worker.aclose()``
    before closing clients. False means an uncooperative task is still owned.
    This worker supports retryable read operations, not reconciled write effects.
    """

    def __init__(
        self,
        store: ExecutionStore,
        bindings: Sequence[WorkflowBinding],
        *,
        worker_id: str,
        concurrency: int = 4,
        lease_seconds: float = 30,
        poll_interval: float = 0.25,
        cleanup_timeout: float = 2,
    ) -> None:
        if (
            not isinstance(worker_id, str)
            or not worker_id.strip()
            or not worker_id.isprintable()
            or len(worker_id) > 256
            or type(concurrency) is not int
            or not 1 <= concurrency <= 256
            or not bindings
        ):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        for value, lower, upper in (
            (lease_seconds, 1, 300),
            (poll_interval, 0.01, 60),
            (cleanup_timeout, 0.01, 30),
        ):
            if (
                type(value) not in (float, int)
                or not math.isfinite(value)
                or not lower <= value <= upper
            ):
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if any(not isinstance(item, WorkflowBinding) for item in bindings):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        self._bindings = {(item.plan.name, item.plan.revision): item for item in bindings}
        if len(self._bindings) != len(bindings):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        self._store = store
        self._owner = worker_id
        self._concurrency = concurrency
        self._lease_seconds = lease_seconds
        self._poll_interval = poll_interval
        self._cleanup_timeout = cleanup_timeout
        self._closed = False
        self._serving = False
        self._serve_stop: asyncio.Event | None = None
        self._jobs: set[asyncio.Task[RunResult | None]] = set()
        self._children: set[asyncio.Task[object]] = set()

    @staticmethod
    def _consume(task: asyncio.Task[object]) -> None:
        if not task.cancelled():
            task.exception()

    def _child(self, operation: Awaitable[_T]) -> asyncio.Task[_T]:
        async def run() -> _T:
            return await operation

        task = asyncio.create_task(run())
        self._children.add(task)
        task.add_done_callback(self._children.discard)
        task.add_done_callback(self._consume)
        return task

    async def _stop(self, tasks: Sequence[asyncio.Task[object]]) -> bool:
        pending = {task for task in tasks if not task.done()}
        for task in pending:
            if not task.cancelling():
                task.cancel()
        if pending:
            _, pending = await asyncio.wait(pending, timeout=self._cleanup_timeout)
        if pending:
            self._closed = True
        return not pending

    async def run_once(self) -> RunResult | None:
        """Claim at most one supported execution; reject excess local work immediately."""
        if self._closed:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        if len(self._jobs) >= self._concurrency:
            raise ServiceError(ErrorCode.CAPACITY_EXCEEDED, retryable=True)
        task = asyncio.create_task(self._claim_and_run())
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)
        task.add_done_callback(self._consume)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await self._stop([task])
            raise

    async def _claim_and_run(self) -> RunResult | None:
        started = asyncio.get_running_loop().time()
        claim = await self._store.claim(
            self._owner,
            lease_seconds=self._lease_seconds,
            revisions=frozenset(self._bindings),
        )
        if claim is None:
            return None
        if self._closed or (self._serve_stop is not None and self._serve_stop.is_set()):
            await self._store.release(claim.lease)
            return None
        scope = _ClaimScope()
        terminal = False
        try:
            binding = self._bindings[
                (claim.execution.submission.workflow, claim.execution.submission.revision)
            ]
            result = await self._coordinate(
                claim, binding, started + claim.remaining_seconds, scope
            )
            terminal = True
            return result
        finally:
            if not terminal and scope.stopped:
                try:
                    async with asyncio.timeout(self._cleanup_timeout):
                        await self._store.release(claim.lease)
                except (ServiceError, TimeoutError):
                    # Lost ownership or unavailable storage leaves expiry/reclaim in charge.
                    pass

    def _machine(self, stored: StoredExecution, binding: WorkflowBinding) -> ExecutionMachine:
        submission = stored.submission
        if (
            submission.workflow != binding.plan.name
            or submission.revision != binding.plan.revision
            or submission.first_step != binding.plan.start
        ):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        validate_execution_identity(submission.envelope, submission.identity)
        machine = ExecutionMachine(
            binding.plan,
            submission.envelope,
            identity=submission.identity,
            execution_id=stored.execution_id,
            limits=submission.limits,
            checkpoints=stored.checkpoints,
        )
        if machine.current != stored.current_step:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        return machine

    async def _coordinate(
        self, claim: ClaimedExecution, binding: WorkflowBinding, deadline: float, scope: _ClaimScope
    ) -> RunResult:
        if claim.disposition != "run":
            code = (
                ErrorCode.CANCELLED
                if claim.disposition == "terminalize_cancel"
                else ErrorCode.TIMEOUT
            )
            with observe(
                binding.observer,
                binding.plan.name,
                trace=incoming_trace(claim.execution.submission.envelope.metadata),
            ) as observation:
                result = await self._terminal(claim, binding, Failure(code))
                observation.status = result.status
                observation.error = result.error.code if result.error else None
                return result
        interrupted = asyncio.Event()
        cancellation = asyncio.Event()
        execution = self._child(self._drive(claim, binding, deadline, interrupted))
        monitor = self._child(self._monitor(claim, cancellation))
        cancel_waiter = self._child(cancellation.wait())
        scope.stopped = False
        execution_stopped: bool | None = None
        try:
            done, _ = await asyncio.wait(
                {execution, monitor, cancel_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if execution in done:
                return execution.result()
            if monitor in done:
                monitor.result()  # Loss of ownership never becomes a business failure.
                raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
            interrupted.set()
            execution_stopped = await self._stop([execution])
            if not execution_stopped:
                raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
            return await self._terminal(claim, binding, Failure(ErrorCode.CANCELLED))
        finally:
            interrupted.set()
            if execution_stopped is None:
                execution_stopped = await self._stop([execution])
            await self._stop([cancel_waiter])
            if execution_stopped:
                scope.stopped = await self._stop([monitor])
            else:
                # The monitor keeps renewing even after it observes user
                # cancellation. Only the guardian stops it after the read ends.
                self._closed = True
                self._child(self._retain_claim(claim, execution, monitor))

    async def _retain_claim(
        self,
        claim: ClaimedExecution,
        execution: asyncio.Task[RunResult],
        monitor: asyncio.Task[None],
    ) -> None:
        """Own abandoned cleanup and its monitor until uncooperative reads stop."""
        if not execution.done():
            await asyncio.wait({execution})
        if await self._stop([monitor]):
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await self._store.release(claim.lease)
            except (ServiceError, TimeoutError):
                pass

    async def _monitor(self, claim: ClaimedExecution, cancellation: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(self._lease_seconds / 3)
            await self._store.heartbeat(claim.lease, lease_seconds=self._lease_seconds)
            stored = await self._store.get(
                claim.execution.execution_id, claim.execution.submission.identity
            )
            if stored.cancel_requested:
                cancellation.set()

    async def _terminal(
        self, claim: ClaimedExecution, binding: WorkflowBinding, failure: Failure
    ) -> RunResult:
        stored = await self._store.get(
            claim.execution.execution_id, claim.execution.submission.identity
        )
        machine = self._machine(stored, binding)
        if stored.cancel_requested:
            failure = Failure(ErrorCode.CANCELLED)
        result = machine.result(
            await self._store.execution_usage(claim.lease),
            failure=failure,
            cancelled=stored.cancel_requested,
        )
        await self._store.finish(claim.lease, result)
        return result

    async def _drive(
        self,
        claim: ClaimedExecution,
        binding: WorkflowBinding,
        deadline: float,
        interrupted: asyncio.Event,
    ) -> RunResult:
        submission = claim.execution.submission
        with observe(
            binding.observer, binding.plan.name, trace=incoming_trace(submission.envelope.metadata)
        ) as observation:
            await self._store.heartbeat(claim.lease, lease_seconds=self._lease_seconds)
            stored = await self._store.get(claim.execution.execution_id, submission.identity)
            if stored.cancel_requested:
                result = await self._terminal(claim, binding, Failure(ErrorCode.CANCELLED))
                observation.status, observation.error = result.status, ErrorCode.CANCELLED
                return result
            machine = self._machine(stored, binding)
            try:
                binding.validator.validate_input(submission.envelope.payload)
            except Exception as error:
                failure = self._failure(error)
                result = await self._terminal(claim, binding, failure)
                observation.status, observation.error = result.status, failure.code
                return result
            try:
                async with asyncio.timeout_at(deadline):
                    await self._store.heartbeat(claim.lease, lease_seconds=self._lease_seconds)
                    while machine.current is not None:
                        if asyncio.get_running_loop().time() >= deadline:
                            raise TimeoutError
                        try:
                            step, inputs = machine.prepare()
                        except Exception as error:
                            result = await self._terminal(claim, binding, self._failure(error))
                            observation.status = result.status
                            observation.error = result.error.code if result.error else None
                            return result
                        with observe(
                            binding.observer, binding.plan.name, step=step.name
                        ) as step_observation:
                            outcome = None
                            if not isinstance(step, FinishStepPlan):
                                budget = WorkerBudget(
                                    await PersistentStepBudget.open(
                                        self._store, claim.lease, step_id=step.name
                                    ),
                                    interrupted,
                                    deadline,
                                )
                                context = StepContext(
                                    claim.execution.execution_id,
                                    binding.plan.name,
                                    binding.plan.revision,
                                    step.name,
                                    CallerContext(
                                        submission.identity, submission.envelope.metadata
                                    ),
                                    deadline,
                                    submission.limits.model_timeout,
                                    submission.limits.tool_timeout,
                                    budget,
                                )
                                try:
                                    outcome = await binding.executor.execute(step, inputs, context)
                                except Exception as error:
                                    if budget.persistence_error is not None:
                                        raise budget.persistence_error from None
                                    result = await self._terminal(
                                        claim, binding, self._failure(error)
                                    )
                                    step_observation.status = observation.status = result.status
                                    step_observation.error = observation.error = (
                                        result.error.code if result.error else None
                                    )
                                    return result
                                if budget.persistence_error is not None:
                                    raise budget.persistence_error from None
                            if interrupted.is_set():
                                raise asyncio.CancelledError
                            if asyncio.get_running_loop().time() >= deadline:
                                raise TimeoutError
                            try:
                                checkpoint = machine.advance(outcome)
                            except Exception as error:
                                result = await self._terminal(claim, binding, self._failure(error))
                                observation.status = result.status
                                observation.error = result.error.code if result.error else None
                                return result
                            await self._store.checkpoint(
                                claim.lease,
                                step_id=checkpoint.step_id,
                                record=checkpoint.record,
                                next_step=checkpoint.next_step,
                            )
                            step_observation.status = (
                                "needs_review"
                                if checkpoint.record.status == "needs_review"
                                else "completed"
                            )
                    if interrupted.is_set():
                        raise asyncio.CancelledError
                    usage = await self._store.execution_usage(claim.lease)
                    try:
                        result = machine.result(usage)
                    except Exception as error:
                        result = await self._terminal(claim, binding, self._failure(error))
                    else:
                        await self._store.finish(claim.lease, result)
            except TimeoutError:
                result = await self._terminal(claim, binding, Failure(ErrorCode.TIMEOUT))
            except ServiceError as error:
                if error.code == ErrorCode.CANCELLED or (
                    error.code == ErrorCode.TIMEOUT
                    and asyncio.get_running_loop().time() >= deadline
                ):
                    result = await self._terminal(claim, binding, Failure(error.code))
                else:
                    raise
            observation.status = result.status
            observation.error = result.error.code if result.error else None
            return result

    @staticmethod
    def _failure(error: Exception) -> Failure:
        if isinstance(error, ServiceError):
            return Failure(error.code, error.retryable)
        return Failure(
            ErrorCode.TIMEOUT if isinstance(error, TimeoutError) else ErrorCode.DEPENDENCY_FAILURE
        )

    async def serve(self, stop: asyncio.Event) -> None:
        """Run fixed polling loops until stopped; drain before returning."""
        if self._serving or self._closed:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        self._serving = True
        self._serve_stop = stop

        async def poll() -> None:
            while not stop.is_set() and not self._closed:
                result = None
                try:
                    result = await self.run_once()
                except ServiceError as error:
                    if error.code in (ErrorCode.INVALID_CONFIGURATION, ErrorCode.FORBIDDEN):
                        raise
                    emit_event(
                        _LOGGER,
                        LogEvent.DEPENDENCY_REJECTED,
                        level=logging.WARNING,
                        error_code=error.code,
                    )
                if result is None and not stop.is_set() and not self._closed:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
                    except TimeoutError:
                        pass

        loops = [asyncio.create_task(poll()) for _ in range(self._concurrency)]
        stopper = asyncio.create_task(stop.wait())
        propagating = False
        try:
            done, _ = await asyncio.wait([*loops, stopper], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except BaseException:
            propagating = True
            raise
        finally:
            clean = await self.aclose()
            loops_clean = await self._stop([*loops, stopper])
            self._serving = False
            if not propagating and (not clean or not loops_clean):
                raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)

    async def aclose(self, *, timeout: float = 10) -> bool:
        """Stop intake; drain then cancel owned work without closing host clients."""
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("shutdown timeout must be finite and nonnegative")
        self._closed = True
        deadline = asyncio.get_running_loop().time() + timeout
        if self._jobs:
            await asyncio.wait(self._jobs, timeout=timeout)
        stopped = await self._stop(list(self._jobs))
        # Claim coordinators own child cleanup. Do not cancel lease guardians or
        # release capacity merely because a read ignored its first cancellation.
        children = {task for task in self._children if not task.done()}
        remaining = deadline - asyncio.get_running_loop().time()
        if children and remaining > 0:
            await asyncio.wait(children, timeout=remaining)
        return stopped and not any(not task.done() for task in self._children)
