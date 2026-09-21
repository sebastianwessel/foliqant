"""Real PostgreSQL acceptance tests for durable workflow execution workers."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from test_runner import NoSchema, Scripted, make_plan

from foliqant.adapters.storage.postgres import PostgresStore
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.contracts.execution import ExecutionResult
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import RunResult, StepOutcome, StepRecord, TokenUsage, Usage
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, freeze_json
from foliqant.core.plan import WorkflowPlan
from foliqant.core.runner import ExecutionLimits
from foliqant.core.storage import ClaimedExecution, Submission
from foliqant.ports.execution import OperationStep, StepContext
from foliqant.workers.execution import ExecutionWorker, WorkflowBinding

pytestmark = pytest.mark.integration
pytest_plugins = ("postgres_support",)

_HANDLER = "type: handler\nhandler: echo\ninput: {message: {pointer: /payload/message}}\n"
_DEFAULT_IDENTITY = Identity(principal_id="operator")
_DEFAULT_LIMITS = ExecutionLimits(run_timeout=5)
_ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def _store(dsn: str) -> AsyncIterator[PostgresStore]:
    store = await PostgresStore.open(dsn, concurrency=8, queue_limit=16, operation_timeout=5)
    try:
        await store.migrate()
        yield store
    finally:
        await store.aclose()


def _two_handler_plan(path: Path) -> WorkflowPlan:
    return make_plan(
        path,
        {
            "first": _HANDLER + "next: second\n",
            "second": (
                "type: handler\nhandler: echo\n"
                "input: {message: {pointer: /steps/first/result/message}}\nnext: done\n"
            ),
            "done": "type: finish\noutcome: completed\n",
        },
    )


def _one_handler_plan(path: Path) -> WorkflowPlan:
    return make_plan(
        path,
        {"first": _HANDLER + "next: done\n", "done": "type: finish\noutcome: completed\n"},
    )


async def _submit(
    store: PostgresStore,
    plan: WorkflowPlan,
    *,
    key: str = "worker-run",
    message: str = "hello",
    identity: Identity = _DEFAULT_IDENTITY,
    limits: ExecutionLimits = _DEFAULT_LIMITS,
) -> str:
    envelope = accept_envelope(
        Envelope(payload={"message": message}, metadata={"source": "worker-test"}), identity
    )
    accepted = await store.accept(
        Submission(
            workflow=plan.name,
            revision=plan.revision,
            idempotency_key=key,
            envelope=envelope,
            identity=identity,
            first_step=plan.start,
            limits=limits,
        )
    )
    return accepted.execution_id


def _worker(
    store: PostgresStore,
    plan: WorkflowPlan,
    executor: Scripted,
    *,
    worker_id: str = "test-worker",
    concurrency: int = 1,
    lease_seconds: float = 2,
    poll_interval: float = 0.02,
    cleanup_timeout: float = 0.2,
) -> ExecutionWorker:
    return ExecutionWorker(
        store,
        [WorkflowBinding(plan, executor, NoSchema())],
        worker_id=worker_id,
        concurrency=concurrency,
        lease_seconds=lease_seconds,
        poll_interval=poll_interval,
        cleanup_timeout=cleanup_timeout,
    )


async def test_worker_runs_handlers_and_finish_with_exact_identity(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _two_handler_plan(tmp_path)
    identity = Identity(tenant_id="tenant-a", principal_id="operator-a")
    seen: list[str] = []

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        seen.append(step.name)
        assert context.caller.identity == identity
        assert context.caller.metadata["tenant_id"] == identity.tenant_id
        assert inputs["message"] == "hello"
        return StepOutcome(freeze_json({"message": inputs["message"]}))

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan, identity=identity)
        worker = _worker(store, plan, Scripted(handle))
        try:
            result = await worker.run_once()
        finally:
            assert await worker.aclose()

        assert isinstance(result, RunResult) and result.status == "completed"
        assert result.execution_id == execution_id
        assert result.metadata["tenant_id"] == "tenant-a"
        assert seen == ["first", "second"]
        assert [name for name, record in result.decisions if record.status != "skipped"] == [
            "first",
            "second",
            "done",
        ]
        stored = await store.get(execution_id, identity)
        assert stored.result == result


async def test_worker_resumes_checkpoint_without_reexecuting_completed_handler(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _two_handler_plan(tmp_path)
    identity = Identity(principal_id="resume-user")

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        assert step.name == "second"
        assert inputs["message"] == "persisted"
        return StepOutcome(freeze_json({"message": "resumed"}))

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan, identity=identity)
        claim = await store.claim(
            "lost-process",
            lease_seconds=2,
            revisions=frozenset({(plan.name, plan.revision)}),
        )
        assert claim is not None
        await store.checkpoint(
            claim.lease,
            step_id="first",
            record=StepRecord("completed", freeze_json({"message": "persisted"}), True),
            next_step="second",
        )
        await store.release(claim.lease)

        worker = _worker(store, plan, Scripted(handle))
        try:
            result = await worker.run_once()
        finally:
            assert await worker.aclose()

        assert result is not None and result.status == "completed"
        assert result.execution_id == execution_id
        assert dict(result.decisions)["first"].result == freeze_json({"message": "persisted"})


async def test_worker_reuses_persisted_attempt_budget_after_restart(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _one_handler_plan(tmp_path)
    limits = ExecutionLimits(run_timeout=5, model_requests_per_step=2)

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        assert context.budget.snapshot() == Usage(1, 0, TokenUsage())
        ticket = await context.budget.start_model_request()
        assert ticket == 2
        await context.budget.finish_model_request(ticket, TokenUsage(3, 1, 0, 0, 0))
        return StepOutcome(inputs["message"])

    async with _store(isolated_postgres_dsn) as store:
        await _submit(store, plan, limits=limits)
        claim = await store.claim(
            "interrupted-process",
            lease_seconds=2,
            revisions=frozenset({(plan.name, plan.revision)}),
        )
        assert claim is not None
        assert await store.reserve_attempt(claim.lease, step_id="first", kind="model") == 1
        await store.release(claim.lease)

        worker = _worker(store, plan, Scripted(handle))
        try:
            result = await worker.run_once()
        finally:
            assert await worker.aclose()

        assert result is not None and result.status == "completed"
        assert result.usage == Usage(2, 0, TokenUsage())


async def test_caught_reservation_storage_failure_abandons_without_business_failure(
    isolated_postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _one_handler_plan(tmp_path)

    async def translate_storage_error(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        try:
            await context.budget.start_model_request()
        except ServiceError:
            return StepOutcome("executor-translated-the-error")
        raise AssertionError("the injected reservation failure was not raised")

    async def complete(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        return StepOutcome("recovered")

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan)
        original_reserve = store.reserve_attempt

        async def failed_reservation(*args: object, **kwargs: object) -> int:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)

        monkeypatch.setattr(store, "reserve_attempt", failed_reservation)
        first_worker = _worker(store, plan, Scripted(translate_storage_error))
        try:
            with pytest.raises(ServiceError) as abandoned:
                await first_worker.run_once()
            assert abandoned.value.code is ErrorCode.DEPENDENCY_FAILURE
        finally:
            assert await first_worker.aclose()

        stored = await store.get(execution_id, _DEFAULT_IDENTITY)
        assert stored.status == "accepted"
        assert stored.result is None and stored.checkpoints == ()

        monkeypatch.setattr(store, "reserve_attempt", original_reserve)
        recovery = _worker(store, plan, Scripted(complete), worker_id="recovery")
        try:
            result = await recovery.run_once()
        finally:
            assert await recovery.aclose()
        assert result is not None and result.status == "completed"
        assert dict(result.decisions)["first"].result == "recovered"


async def test_two_concurrent_runs_keep_identity_and_results_isolated(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _one_handler_plan(tmp_path)
    both_entered = asyncio.Event()
    entered = 0

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), 1)
        assert context.caller.identity.tenant_id == inputs["message"]
        return StepOutcome(inputs["message"])

    async with _store(isolated_postgres_dsn) as store:
        identities = [
            Identity(tenant_id="first", principal_id="first-user"),
            Identity(tenant_id="second", principal_id="second-user"),
        ]
        execution_ids = [
            await _submit(
                store,
                plan,
                key=f"run-{index}",
                message=identity.tenant_id or "",
                identity=identity,
            )
            for index, identity in enumerate(identities)
        ]
        worker = _worker(store, plan, Scripted(handle), concurrency=2)
        try:
            first, second = await asyncio.gather(worker.run_once(), worker.run_once())
        finally:
            assert await worker.aclose()

        results = {result.execution_id: result for result in (first, second) if result is not None}
        assert set(results) == set(execution_ids)
        for execution_id, identity in zip(execution_ids, identities, strict=True):
            result = results[execution_id]
            assert result.metadata["tenant_id"] == identity.tenant_id
            assert dict(result.decisions)["first"].result == identity.tenant_id


async def test_persisted_cancellation_stops_active_handler_and_terminalizes(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _one_handler_plan(tmp_path)
    identity = Identity(principal_id="cancel-user")
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("unreachable")

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan, identity=identity)
        worker = _worker(store, plan, Scripted(handle))
        task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(entered.wait(), 1)
        await store.cancel(execution_id, identity)
        result = await asyncio.wait_for(task, 2)
        assert stopped.is_set()
        assert result is not None and result.status == "cancelled"
        assert result.error is not None and result.error.code is ErrorCode.CANCELLED
        assert (await store.get(execution_id, identity)).status == "cancelled"
        assert await worker.aclose()


async def test_cancellation_committed_after_claim_is_seen_before_handler(
    isolated_postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _one_handler_plan(tmp_path)
    identity = Identity(principal_id="cancel-after-claim")

    async def forbidden_handler(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        pytest.fail("a cancellation committed after claim must be observed before execution")

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan, identity=identity)
        original_claim = store.claim

        async def claim_then_cancel(
            owner: str,
            *,
            lease_seconds: float,
            revisions: frozenset[tuple[str, str]],
        ) -> ClaimedExecution | None:
            claim = await original_claim(owner, lease_seconds=lease_seconds, revisions=revisions)
            if claim is not None:
                await store.cancel(execution_id, identity)
            return claim

        monkeypatch.setattr(store, "claim", claim_then_cancel)
        worker = _worker(store, plan, Scripted(forbidden_handler))
        try:
            result = await worker.run_once()
        finally:
            assert await worker.aclose()

        assert result is not None and result.status == "cancelled"
        assert result.error is not None and result.error.code is ErrorCode.CANCELLED
        stored = await store.get(execution_id, identity)
        assert stored.status == "cancelled" and stored.checkpoints == ()


async def test_database_deadline_cancels_handler_and_terminalizes_timeout(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _one_handler_plan(tmp_path)
    stopped = asyncio.Event()

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("unreachable")

    async with _store(isolated_postgres_dsn) as store:
        await _submit(store, plan, limits=ExecutionLimits(run_timeout=0.15))
        worker = _worker(store, plan, Scripted(handle), poll_interval=0.01)
        try:
            result = await asyncio.wait_for(worker.run_once(), 2)
        finally:
            assert await worker.aclose()

        assert stopped.is_set()
        assert result is not None and result.status == "failed"
        assert result.error is not None and result.error.code is ErrorCode.TIMEOUT


async def test_swallowed_deadline_cancellation_cannot_reserve_or_checkpoint(
    isolated_postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _one_handler_plan(tmp_path)
    budget_rejected = asyncio.Event()

    async def swallow_cancellation(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            with pytest.raises(ServiceError) as expired:
                await context.budget.start_tool_call()
            assert expired.value.code is ErrorCode.TIMEOUT
            budget_rejected.set()
            return StepOutcome("must-not-checkpoint")

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan)
        original_claim = store.claim

        async def shortened_claim(
            owner: str,
            *,
            lease_seconds: float,
            revisions: frozenset[tuple[str, str]],
        ) -> ClaimedExecution | None:
            claim = await original_claim(owner, lease_seconds=lease_seconds, revisions=revisions)
            return replace(claim, remaining_seconds=0.1) if claim is not None else None

        monkeypatch.setattr(store, "claim", shortened_claim)
        worker = _worker(store, plan, Scripted(swallow_cancellation))
        try:
            result = await asyncio.wait_for(worker.run_once(), 2)
        finally:
            assert await worker.aclose()

        assert budget_rejected.is_set()
        assert result is not None and result.status == "failed"
        assert result.error is not None and result.error.code is ErrorCode.TIMEOUT
        assert result.usage == Usage()
        stored = await store.get(execution_id, _DEFAULT_IDENTITY)
        assert stored.status == "failed" and stored.checkpoints == ()


async def test_heartbeat_protects_short_lease_during_handler(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _one_handler_plan(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        entered.set()
        await release.wait()
        return StepOutcome(inputs["message"])

    async with _store(isolated_postgres_dsn) as store:
        await _submit(store, plan)
        worker = _worker(store, plan, Scripted(handle), lease_seconds=1)
        task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(1.1)
        rival = await store.claim(
            "rival",
            lease_seconds=1,
            revisions=frozenset({(plan.name, plan.revision)}),
        )
        assert rival is None
        release.set()
        assert (await asyncio.wait_for(task, 2)) is not None
        assert await worker.aclose()


async def test_caller_cancellation_releases_cooperative_work_for_resume(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _one_handler_plan(tmp_path)
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def blocking(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("unreachable")

    async def complete(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        return StepOutcome(inputs["message"])

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan)
        first_worker = _worker(store, plan, Scripted(blocking), worker_id="first-worker")
        task = asyncio.create_task(first_worker.run_once())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        assert await first_worker.aclose()

        stored = await store.get(execution_id, Identity(principal_id="operator"))
        assert stored.status == "accepted" and stored.checkpoints == ()
        second_worker = _worker(store, plan, Scripted(complete), worker_id="second-worker")
        try:
            result = await second_worker.run_once()
        finally:
            assert await second_worker.aclose()
        assert result is not None and result.status == "completed"


async def test_shutdown_is_bounded_until_uncooperative_handler_resolves(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _one_handler_plan(tmp_path)
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def stubborn(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            return StepOutcome(inputs["message"])

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan)
        worker = _worker(
            store,
            plan,
            Scripted(stubborn),
            lease_seconds=1,
            cleanup_timeout=0.05,
        )
        task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(entered.wait(), 1)
        started = asyncio.get_running_loop().time()
        assert not await worker.aclose(timeout=0.05)
        assert asyncio.get_running_loop().time() - started < 0.5
        await asyncio.wait_for(cancelled.wait(), 1)
        with pytest.raises(ServiceError) as closed:
            await worker.run_once()
        assert closed.value.code is ErrorCode.DEPENDENCY_FAILURE

        await asyncio.sleep(1.1)
        rival = await store.claim(
            "rival",
            lease_seconds=1,
            revisions=frozenset({(plan.name, plan.revision)}),
        )
        assert rival is None
        guarded = await store.get(execution_id, _DEFAULT_IDENTITY)
        assert guarded.status == "running"
        assert guarded.result is None and guarded.checkpoints == ()

        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)

        async def release_while_draining() -> None:
            await asyncio.sleep(0.05)
            release.set()

        releaser = asyncio.create_task(release_while_draining())
        try:
            # Repeated close drains retained guardians, not only the already
            # cancelled top-level job. It must wait for the read to stop.
            assert await worker.aclose(timeout=1)
        finally:
            release.set()
            await releaser
        stored = await store.get(execution_id, Identity(principal_id="operator"))
        assert stored.status == "accepted" and stored.checkpoints == ()


async def test_user_cancel_keeps_lease_until_uncooperative_handler_stops(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _one_handler_plan(tmp_path)
    entered = asyncio.Event()
    interrupted = asyncio.Event()
    release = asyncio.Event()

    async def stubborn(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            interrupted.set()
            await release.wait()
            return StepOutcome("must-not-checkpoint")

    async def forbidden_after_cancel(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        pytest.fail("a recovered cancelled execution must terminalize without a handler")

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan)
        worker = _worker(
            store,
            plan,
            Scripted(stubborn),
            lease_seconds=1,
            cleanup_timeout=0.05,
        )
        task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(entered.wait(), 1)
        await store.cancel(execution_id, _DEFAULT_IDENTITY)
        with pytest.raises(ServiceError) as incomplete:
            await asyncio.wait_for(task, 2)
        assert incomplete.value.code is ErrorCode.DEPENDENCY_FAILURE
        await asyncio.wait_for(interrupted.wait(), 1)

        with pytest.raises(ServiceError) as closed:
            await worker.run_once()
        assert closed.value.code is ErrorCode.DEPENDENCY_FAILURE
        await asyncio.sleep(1.1)
        rival = await store.claim(
            "rival",
            lease_seconds=1,
            revisions=frozenset({(plan.name, plan.revision)}),
        )
        assert rival is None
        guarded = await store.get(execution_id, _DEFAULT_IDENTITY)
        assert guarded.status == "running" and guarded.cancel_requested
        assert guarded.result is None and guarded.checkpoints == ()

        release.set()
        for _ in range(20):
            if await worker.aclose(timeout=0.05):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("cancel guardian did not release after the handler stopped")

        recovery = _worker(
            store,
            plan,
            Scripted(forbidden_after_cancel),
            worker_id="cancel-terminalizer",
        )
        try:
            result = await recovery.run_once()
        finally:
            assert await recovery.aclose()
        assert result is not None and result.status == "cancelled"
        assert result.error is not None and result.error.code is ErrorCode.CANCELLED
        stored = await store.get(execution_id, _DEFAULT_IDENTITY)
        assert stored.status == "cancelled" and stored.checkpoints == ()


async def test_lost_lease_never_commits_stale_handler_result(
    isolated_postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _one_handler_plan(tmp_path)

    async def slow(step: OperationStep, inputs: FrozenObject, context: StepContext) -> StepOutcome:
        await asyncio.sleep(1.1)
        return StepOutcome("stale")

    async def complete(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        return StepOutcome("fresh")

    async with _store(isolated_postgres_dsn) as store:
        execution_id = await _submit(store, plan)
        original_heartbeat = store.heartbeat

        async def heartbeat_without_extension(*args: object, **kwargs: object) -> datetime:
            return datetime.now(UTC)

        monkeypatch.setattr(store, "heartbeat", heartbeat_without_extension)
        first_worker = _worker(store, plan, Scripted(slow), lease_seconds=1)
        try:
            with pytest.raises(ServiceError) as lost:
                await asyncio.wait_for(first_worker.run_once(), 2)
            assert lost.value.code is ErrorCode.CONFLICT
        finally:
            assert await first_worker.aclose()
        monkeypatch.setattr(store, "heartbeat", original_heartbeat)

        after_loss = await store.get(execution_id, Identity(principal_id="operator"))
        assert after_loss.checkpoints == () and after_loss.result is None
        second_worker = _worker(store, plan, Scripted(complete), worker_id="recovery")
        try:
            result = await second_worker.run_once()
        finally:
            assert await second_worker.aclose()
        assert result is not None and result.status == "completed"
        assert dict(result.decisions)["first"].result == "fresh"


async def test_serve_stops_and_drains_with_no_pending_work(
    isolated_postgres_dsn: str, tmp_path: Path
) -> None:
    plan = _one_handler_plan(tmp_path)

    async def unused(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        pytest.fail("there is no queued work")

    async with _store(isolated_postgres_dsn) as store:
        worker = _worker(store, plan, Scripted(unused), concurrency=2)
        stop = asyncio.Event()
        serving = asyncio.create_task(worker.serve(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(serving, 1)
        assert await worker.aclose()


async def test_durable_workflow_example_runs_against_real_postgres(
    isolated_postgres_dsn: str,
) -> None:
    path = _ROOT / "examples/durable-workflow/run.py"
    spec = importlib.util.spec_from_file_location("durable_workflow_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    demonstrate = cast(Callable[[str], Awaitable[ExecutionResult]], module.demonstrate)

    result = await demonstrate(isolated_postgres_dsn)
    assert result.execution.status == "completed"
    assert result.payload == {"message": "Hallo"}
    assert result.decisions["echo"].status == "completed"
    assert result.decisions["done"].status == "completed"
