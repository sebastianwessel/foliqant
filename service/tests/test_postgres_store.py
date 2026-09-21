"""Real PostgreSQL acceptance tests for the durable execution store."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast

import psycopg
import pytest

from foliqant.adapters.storage.budget import PersistentStepBudget
from foliqant.adapters.storage.postgres import PostgresStore
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import (
    Failure,
    RunResult,
    RunStatus,
    StepRecord,
    TokenUsage,
    Usage,
)
from foliqant.core.identity import Identity
from foliqant.core.json import freeze_json, thaw_json
from foliqant.core.runner import ExecutionLimits
from foliqant.core.storage import DeliveryRequest, StoredExecution, Submission

pytestmark = pytest.mark.integration
pytest_plugins = ("postgres_support",)

_DEFAULT_IDENTITY = Identity(principal_id="operator-1")
_DEFAULT_LIMITS = ExecutionLimits(run_timeout=10)
_ZERO_USAGE = Usage()
_ROOT = Path(__file__).resolve().parents[2]


def _submission(
    identity: Identity = _DEFAULT_IDENTITY,
    *,
    key: str = "request-1",
    payload: object = None,
    revision: str = "revision-1",
    limits: ExecutionLimits = _DEFAULT_LIMITS,
    delivery: DeliveryRequest | None = None,
) -> Submission:
    metadata = {
        name: value
        for name, value in (
            ("tenant_id", identity.tenant_id),
            ("principal_id", identity.principal_id),
        )
        if value is not None
    }
    return Submission(
        workflow="test_workflow",
        revision=revision,
        idempotency_key=key,
        envelope=AcceptedEnvelope(
            payload=payload if payload is not None else {"value": 1}, metadata=metadata
        ),
        identity=identity,
        first_step="start",
        limits=limits,
        delivery=delivery,
    )


def _result(
    execution: StoredExecution,
    *,
    status: RunStatus = "completed",
    usage: Usage = _ZERO_USAGE,
    error: Failure | None = None,
    decisions: tuple[tuple[str, StepRecord], ...] | None = None,
) -> RunResult:
    if decisions is None:
        decisions = tuple(
            (checkpoint.step_id, checkpoint.record) for checkpoint in execution.checkpoints
        )
    return RunResult(
        execution.execution_id,
        execution.submission.workflow,
        execution.submission.revision,
        status,
        (
            execution.submission.envelope.payload
            if status in ("failed", "cancelled")
            else freeze_json({"stored": True})
        ),
        execution.submission.envelope.metadata,
        decisions,
        usage,
        error,
    )


def _assert_service_error(value: object, code: ErrorCode) -> None:
    assert isinstance(value, ServiceError)
    assert value.code is code


@asynccontextmanager
async def _migrated_store(
    dsn: str, *, allow_anonymous: bool = False, concurrency: int = 4
) -> AsyncIterator[PostgresStore]:
    store = await PostgresStore.open(
        dsn,
        allow_anonymous=allow_anonymous,
        concurrency=concurrency,
        queue_limit=16,
        operation_timeout=5,
    )
    try:
        await store.migrate()
        yield store
    finally:
        await store.aclose()


async def test_concurrent_deduplication_and_conflict_include_anonymous_scope(
    isolated_postgres_dsn: str,
) -> None:
    async with _migrated_store(isolated_postgres_dsn, allow_anonymous=True) as store:
        anonymous = _submission(Identity(), key="anonymous-same")
        duplicates = await asyncio.gather(*(store.accept(anonymous) for _ in range(8)))
        assert len({item.execution_id for item in duplicates}) == 1
        assert all(item.deadline == duplicates[0].deadline for item in duplicates)

        left = _submission(Identity(), key="anonymous-conflict", payload={"value": "left"})
        right = _submission(Identity(), key="anonymous-conflict", payload={"value": "right"})
        raced = await asyncio.gather(
            store.accept(left), store.accept(right), return_exceptions=True
        )
        assert sum(isinstance(item, StoredExecution) for item in raced) == 1
        errors = [item for item in raced if isinstance(item, BaseException)]
        assert len(errors) == 1
        _assert_service_error(errors[0], ErrorCode.CONFLICT)

        canonical_left = _submission(
            Identity(), key="canonical", payload={"alpha": 1, "beta": {"x": 2, "y": 3}}
        )
        canonical_right = _submission(
            Identity(), key="canonical", payload={"beta": {"y": 3, "x": 2}, "alpha": 1}
        )
        first = await store.accept(canonical_left)
        assert (await store.accept(canonical_right)).execution_id == first.execution_id
        with pytest.raises(ServiceError) as changed_revision:
            await store.accept(replace(canonical_right, revision="revision-2"))
        assert changed_revision.value.code is ErrorCode.CONFLICT


async def test_default_anonymous_policy_and_exact_identity_isolation(
    isolated_postgres_dsn: str,
) -> None:
    async with _migrated_store(isolated_postgres_dsn) as store:
        with pytest.raises(ServiceError) as rejected:
            await store.accept(_submission(Identity(), key="anonymous"))
        assert rejected.value.code is ErrorCode.FORBIDDEN

        principal = Identity(principal_id="same-id")
        tenant = Identity(tenant_id="same-id")
        principal_run = await store.accept(_submission(principal, key="shared-key"))
        tenant_run = await store.accept(_submission(tenant, key="shared-key"))
        assert principal_run.execution_id != tenant_run.execution_id

        for execution, wrong_identity in (
            (principal_run, tenant),
            (tenant_run, principal),
            (principal_run, Identity(principal_id="other")),
        ):
            with pytest.raises(ServiceError) as hidden:
                await store.get(execution.execution_id, wrong_identity)
            assert hidden.value.code is ErrorCode.NOT_FOUND

        assert await store.get(principal_run.execution_id, principal) == principal_run
        assert await store.get(tenant_run.execution_id, tenant) == tenant_run

        mismatched_claim = replace(
            _submission(principal, key="bad-claim"),
            envelope=AcceptedEnvelope(payload={}, metadata={"principal_id": "other"}),
        )
        with pytest.raises(ServiceError) as forbidden:
            await store.accept(mismatched_claim)
        assert forbidden.value.code is ErrorCode.FORBIDDEN


async def test_json_roundtrip_preserves_unicode_nulls_numbers_and_object_order(
    isolated_postgres_dsn: str,
) -> None:
    payload = {
        "last": None,
        "greeting": "Grüße 👋",
        "null_byte": "\u0000",
        "number": 1.25,
        "nested": [{"second": 2, "first": 1}, None],
    }
    async with _migrated_store(isolated_postgres_dsn) as store:
        accepted = await store.accept(_submission(key="json-roundtrip", payload=payload))
        reopened = await store.get(accepted.execution_id, accepted.submission.identity)

    stored_payload = cast(Mapping[str, object], reopened.submission.envelope.payload)
    assert reopened.submission.envelope.payload == accepted.submission.envelope.payload
    assert thaw_json(reopened.submission.envelope.payload) == payload
    assert tuple(stored_payload) == tuple(payload)
    nested = cast(list[object] | tuple[object, ...], stored_payload["nested"])
    assert tuple(cast(Mapping[str, object], nested[0])) == ("second", "first")


async def test_claims_are_exclusive_and_expiry_fences_stale_workers(
    isolated_postgres_dsn: str,
) -> None:
    async with _migrated_store(isolated_postgres_dsn) as store:
        await store.accept(_submission())
        revisions = frozenset({("test_workflow", "revision-1")})
        raced = await asyncio.gather(
            *(
                store.claim(f"worker-{index}", lease_seconds=1, revisions=revisions)
                for index in range(4)
            )
        )
        claims = [claim for claim in raced if claim is not None]
        assert len(claims) == 1
        first = claims[0]
        assert first.disposition == "run"

        await asyncio.sleep(1.05)
        second = await store.claim("recovery", lease_seconds=2, revisions=revisions)
        assert second is not None
        assert second.execution.execution_id == first.execution.execution_id
        assert second.lease.fence == first.lease.fence + 1
        assert second.disposition == "run"

        with pytest.raises(ServiceError) as stale_heartbeat:
            await store.heartbeat(first.lease, lease_seconds=2)
        assert stale_heartbeat.value.code is ErrorCode.CONFLICT
        with pytest.raises(ServiceError) as stale_checkpoint:
            await store.checkpoint(
                first.lease,
                step_id="start",
                record=StepRecord("completed", freeze_json({"worker": "old"}), True),
                next_step=None,
            )
        assert stale_checkpoint.value.code is ErrorCode.CONFLICT


async def test_execution_claim_expiry_during_materialization_rolls_back_fence(
    isolated_postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _migrated_store(isolated_postgres_dsn) as store:
        accepted = await store.accept(_submission())
        revisions = frozenset({("test_workflow", "revision-1")})
        original_stored = store._stored

        async def delayed_stored(conn: object, row: object) -> StoredExecution:
            await asyncio.sleep(1.05)
            return await original_stored(conn, row)  # type: ignore[arg-type]

        monkeypatch.setattr(store, "_stored", delayed_stored)
        with pytest.raises(ServiceError) as expired:
            await store.claim("expired-claim", lease_seconds=1, revisions=revisions)
        assert expired.value.code is ErrorCode.CONFLICT

        monkeypatch.setattr(store, "_stored", original_stored)
        recovered = await store.claim("recovered-claim", lease_seconds=2, revisions=revisions)
        assert recovered is not None
        assert recovered.execution.execution_id == accepted.execution_id
        assert recovered.lease.fence == 1


async def test_database_deadline_blocks_new_work_but_allows_failed_terminalization(
    isolated_postgres_dsn: str,
) -> None:
    limits = ExecutionLimits(run_timeout=0.15)
    async with _migrated_store(isolated_postgres_dsn) as store:
        accepted = await store.accept(_submission(limits=limits))
        await asyncio.sleep(0.2)
        claim = await store.claim(
            "deadline-worker",
            lease_seconds=2,
            revisions=frozenset({("test_workflow", "revision-1")}),
        )
        assert claim is not None
        assert claim.execution.deadline == accepted.deadline
        assert claim.disposition == "terminalize_timeout"

        with pytest.raises(ServiceError) as timed_out:
            await store.reserve_attempt(claim.lease, step_id="start", kind="model")
        assert timed_out.value.code is ErrorCode.TIMEOUT
        with pytest.raises(ServiceError) as late_success:
            await store.finish(claim.lease, _result(claim.execution))
        assert late_success.value.code is ErrorCode.TIMEOUT

        failure = Failure(ErrorCode.TIMEOUT)
        await store.finish(
            claim.lease,
            _result(claim.execution, status="failed", error=failure),
        )
        stored = await store.get(accepted.execution_id, accepted.submission.identity)
        assert stored.status == "failed"
        assert stored.result is not None and stored.result.error == failure


async def test_checkpoint_replay_is_idempotent_and_changed_replay_conflicts(
    isolated_postgres_dsn: str,
) -> None:
    async with _migrated_store(isolated_postgres_dsn) as store:
        accepted = await store.accept(_submission())
        claim = await store.claim(
            "checkpoint-worker",
            lease_seconds=3,
            revisions=frozenset({("test_workflow", "revision-1")}),
        )
        assert claim is not None
        record = StepRecord("completed", freeze_json({"answer": 42}), True)
        await store.checkpoint(claim.lease, step_id="start", record=record, next_step="finish")
        await store.checkpoint(claim.lease, step_id="start", record=record, next_step="finish")

        for changed_record, changed_next in (
            (StepRecord("completed", freeze_json({"answer": 43}), True), "finish"),
            (record, "different"),
        ):
            with pytest.raises(ServiceError) as conflict:
                await store.checkpoint(
                    claim.lease,
                    step_id="start",
                    record=changed_record,
                    next_step=changed_next,
                )
            assert conflict.value.code is ErrorCode.CONFLICT

        stored = await store.get(accepted.execution_id, accepted.submission.identity)
        assert stored.current_step == "finish"
        assert stored.checkpoints[0].record == record


async def test_persistent_budget_survives_store_restart_with_unknown_usage(
    isolated_postgres_dsn: str,
) -> None:
    limits = ExecutionLimits(
        run_timeout=10,
        model_requests_per_step=2,
        tool_calls_per_step=1,
    )
    store = await PostgresStore.open(isolated_postgres_dsn, operation_timeout=5)
    await store.migrate()
    accepted = await store.accept(_submission(limits=limits))
    claim = await store.claim(
        "first-process",
        lease_seconds=1,
        revisions=frozenset({("test_workflow", "revision-1")}),
    )
    assert claim is not None
    budget = await PersistentStepBudget.open(store, claim.lease, step_id="start")
    first_ticket = await budget.start_model_request()
    await budget.finish_model_request(first_ticket, TokenUsage(7, 3, 2, None, 1))
    assert await budget.start_model_request() == 2  # interrupted: usage stays unknown
    assert await budget.start_tool_call() == 1
    with pytest.raises(ServiceError) as exhausted:
        await budget.start_model_request()
    assert exhausted.value.code is ErrorCode.BUDGET_EXHAUSTED
    await store.aclose()

    await asyncio.sleep(1.05)
    recovered_store = await PostgresStore.open(isolated_postgres_dsn, operation_timeout=5)
    try:
        recovered = await recovered_store.claim(
            "second-process",
            lease_seconds=2,
            revisions=frozenset({("test_workflow", "revision-1")}),
        )
        assert recovered is not None
        assert recovered.execution.execution_id == accepted.execution_id
        recovered_budget = await PersistentStepBudget.open(
            recovered_store, recovered.lease, step_id="start"
        )
        assert recovered_budget.snapshot() == Usage(2, 1, TokenUsage())
        with pytest.raises(ServiceError) as still_exhausted:
            await recovered_budget.start_model_request()
        assert still_exhausted.value.code is ErrorCode.BUDGET_EXHAUSTED
    finally:
        await recovered_store.aclose()


async def test_release_makes_progress_reclaimable_without_resetting_attempts(
    isolated_postgres_dsn: str,
) -> None:
    async with _migrated_store(isolated_postgres_dsn) as store:
        accepted = await store.accept(_submission())
        revisions = frozenset({("test_workflow", "revision-1")})
        first = await store.claim("releasing-worker", lease_seconds=3, revisions=revisions)
        assert first is not None
        assert await store.reserve_attempt(first.lease, step_id="start", kind="tool") == 1
        await store.release(first.lease)

        second = await store.claim("next-worker", lease_seconds=3, revisions=revisions)
        assert second is not None
        assert second.execution.execution_id == accepted.execution_id
        assert second.lease.fence == first.lease.fence + 1
        assert await store.step_usage(second.lease, step_id="start") == Usage(tool_calls=1)
        with pytest.raises(ServiceError) as stale_release:
            await store.release(first.lease)
        assert stale_release.value.code is ErrorCode.CONFLICT


async def test_cancellation_is_durable_and_only_cancelled_finish_is_allowed(
    isolated_postgres_dsn: str,
) -> None:
    identity = Identity(tenant_id="tenant-1", principal_id="operator-1")
    async with _migrated_store(isolated_postgres_dsn) as store:
        accepted = await store.accept(_submission(identity))
        claim = await store.claim(
            "cancel-worker",
            lease_seconds=3,
            revisions=frozenset({("test_workflow", "revision-1")}),
        )
        assert claim is not None
        ticket = await store.reserve_attempt(claim.lease, step_id="start", kind="model")
        cancelled = await store.cancel(accepted.execution_id, identity)
        assert cancelled.cancel_requested
        await store.report_usage(
            claim.lease,
            step_id="start",
            ticket=ticket,
            usage=TokenUsage(2, 1, 0, 0, 0),
        )
        usage = Usage(1, 0, TokenUsage(2, 1, 0, 0, 0))
        assert await store.step_usage(claim.lease, step_id="start") == usage

        with pytest.raises(ServiceError) as blocked:
            await store.reserve_attempt(claim.lease, step_id="start", kind="tool")
        assert blocked.value.code is ErrorCode.CANCELLED
        with pytest.raises(ServiceError) as false_success:
            await store.finish(claim.lease, _result(cancelled, usage=usage))
        assert false_success.value.code is ErrorCode.CANCELLED

        failure = Failure(ErrorCode.CANCELLED)
        await store.finish(
            claim.lease,
            _result(cancelled, status="cancelled", usage=usage, error=failure),
        )
        terminal = await store.cancel(accepted.execution_id, identity)
        assert terminal.status == "cancelled"
        assert terminal.result is not None and terminal.result.error == failure


async def test_cancel_disposition_precedes_expired_deadline(
    isolated_postgres_dsn: str,
) -> None:
    limits = ExecutionLimits(run_timeout=0.1)
    async with _migrated_store(isolated_postgres_dsn) as store:
        accepted = await store.accept(_submission(limits=limits))
        await store.cancel(accepted.execution_id, accepted.submission.identity)
        await asyncio.sleep(0.15)
        claim = await store.claim(
            "terminalizer",
            lease_seconds=2,
            revisions=frozenset({("test_workflow", "revision-1")}),
        )
        assert claim is not None
        assert claim.disposition == "terminalize_cancel"


async def test_finish_and_outbox_are_atomic_and_delivery_exhausts_after_final_crash(
    isolated_postgres_dsn: str,
) -> None:
    submission = _submission(delivery=DeliveryRequest("audit-sink", max_attempts=2))
    async with _migrated_store(isolated_postgres_dsn) as store:
        accepted = await store.accept(submission)
        claim = await store.claim(
            "result-worker",
            lease_seconds=3,
            revisions=frozenset({("test_workflow", "revision-1")}),
        )
        assert claim is not None
        first_record = StepRecord("completed", freeze_json({"started": True}), True)
        final_record = StepRecord("completed", freeze_json({"done": True}), True)
        await store.checkpoint(
            claim.lease, step_id="start", record=first_record, next_step="finish"
        )
        await store.checkpoint(claim.lease, step_id="finish", record=final_record, next_step=None)
        refreshed = await store.get(accepted.execution_id, submission.identity)
        skipped = StepRecord("skipped")
        decisions = (
            ("unvisited_before", skipped),
            ("start", first_record),
            ("unvisited_between", skipped),
            ("finish", final_record),
            ("unvisited_after", skipped),
        )
        reversed_checkpoints = (
            ("finish", final_record),
            ("start", first_record),
        )

        with pytest.raises(ServiceError) as reversed_order:
            await store.finish(
                claim.lease,
                _result(refreshed, decisions=reversed_checkpoints),
            )
        assert reversed_order.value.code is ErrorCode.CONFLICT

        invalid_usage = Usage(model_requests=1, tokens=TokenUsage())
        with pytest.raises(ServiceError):
            await store.finish(
                claim.lease,
                _result(refreshed, usage=invalid_usage, decisions=decisions),
            )
        unchanged = await store.get(accepted.execution_id, submission.identity)
        assert unchanged.status == "running" and unchanged.result is None
        assert await store.claim_delivery("early", lease_seconds=1) is None

        result = _result(refreshed, decisions=decisions)
        await store.finish(claim.lease, result)
        finished = await store.get(accepted.execution_id, submission.identity)
        assert finished.status == "completed" and finished.result == result

        first = await store.claim_delivery("delivery-1", lease_seconds=2)
        assert first is not None
        assert first.attempt == 1 and first.result == result
        await store.retry_delivery(first.lease, error=ErrorCode.DEPENDENCY_FAILURE, delay_seconds=0)
        with pytest.raises(ServiceError) as stale_retry_ack:
            await store.complete_delivery(first.lease)
        assert stale_retry_ack.value.code is ErrorCode.CONFLICT

        final = await store.claim_delivery("delivery-2", lease_seconds=1)
        assert final is not None
        assert final.lease.event_id == first.lease.event_id and final.attempt == 2
        await asyncio.sleep(1.05)
        assert await store.claim_delivery("recovery", lease_seconds=1) is None
        with pytest.raises(ServiceError) as stale_final_ack:
            await store.complete_delivery(final.lease)
        assert stale_final_ack.value.code is ErrorCode.CONFLICT


async def test_delivery_claim_expiry_during_materialization_rolls_back_attempt(
    isolated_postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submission = _submission(delivery=DeliveryRequest("expiry-sink", max_attempts=2))
    async with _migrated_store(isolated_postgres_dsn) as store:
        accepted = await store.accept(submission)
        claim = await store.claim(
            "result-worker",
            lease_seconds=3,
            revisions=frozenset({("test_workflow", "revision-1")}),
        )
        assert claim is not None
        record = StepRecord("completed", freeze_json({"done": True}), True)
        await store.checkpoint(claim.lease, step_id="start", record=record, next_step=None)
        execution = await store.get(accepted.execution_id, submission.identity)
        await store.finish(claim.lease, _result(execution))

        original_live = store._live

        async def delayed_live(
            conn: object,
            row: object,
            owner: str,
            fence: int,
            *,
            expected: str,
        ) -> object:
            if owner == "expired-delivery" and expected == "pending":
                await asyncio.sleep(1.05)
            return await original_live(  # type: ignore[arg-type]
                conn, row, owner, fence, expected=expected
            )

        monkeypatch.setattr(store, "_live", delayed_live)
        with pytest.raises(ServiceError) as expired:
            await store.claim_delivery("expired-delivery", lease_seconds=1)
        assert expired.value.code is ErrorCode.CONFLICT

        monkeypatch.setattr(store, "_live", original_live)
        recovered = await store.claim_delivery("recovered-delivery", lease_seconds=2)
        assert recovered is not None
        assert recovered.attempt == 1
        assert recovered.lease.fence == 1


async def test_explicit_migration_and_hash_mismatch_fail_closed(
    isolated_postgres_dsn: str,
) -> None:
    store = await PostgresStore.open(isolated_postgres_dsn, operation_timeout=5)
    try:
        with pytest.raises(ServiceError) as absent_schema:
            await store.accept(_submission())
        assert absent_schema.value.code is ErrorCode.INVALID_CONFIGURATION
        await store.migrate()
        accepted = await store.accept(_submission())

        async with await psycopg.AsyncConnection.connect(
            isolated_postgres_dsn, autocommit=True
        ) as connection:
            await connection.execute(
                "UPDATE foliqant.schema_version SET sql_hash = %s", ("0" * 64,)
            )
        with pytest.raises(ServiceError) as drift:
            await store.get(accepted.execution_id, accepted.submission.identity)
        assert drift.value.code is ErrorCode.INVALID_CONFIGURATION
    finally:
        await store.aclose()


async def test_cancelled_database_wait_releases_the_only_pool_connection(
    isolated_postgres_dsn: str,
) -> None:
    async with _migrated_store(isolated_postgres_dsn, concurrency=1) as store:
        accepted = await store.accept(_submission())
        locker = await psycopg.AsyncConnection.connect(isolated_postgres_dsn)
        try:
            await locker.execute("LOCK TABLE foliqant.schema_version IN ACCESS EXCLUSIVE MODE")
            blocked = asyncio.create_task(
                store.get(accepted.execution_id, accepted.submission.identity)
            )
            await asyncio.sleep(0.1)
            blocked.cancel()
            with pytest.raises(asyncio.CancelledError):
                await blocked
            await locker.rollback()
            assert (
                await store.get(accepted.execution_id, accepted.submission.identity)
            ).execution_id == accepted.execution_id
        finally:
            await locker.close()


async def test_durable_storage_example_runs_against_real_postgres(
    isolated_postgres_dsn: str,
) -> None:
    path = _ROOT / "examples/durable-storage/run.py"
    spec = importlib.util.spec_from_file_location("durable_storage_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    demonstrate = cast(Callable[[str], Awaitable[str]], module.demonstrate)
    assert await demonstrate(isolated_postgres_dsn) == "completed"
