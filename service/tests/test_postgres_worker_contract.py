"""Real SQL contracts used by a resumable worker, without external inference."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import psycopg
import pytest

from foliqant.adapters.storage.postgres import PostgresStore
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepRecord, TokenUsage, Usage
from foliqant.core.identity import Identity
from foliqant.core.runner import ExecutionLimits
from foliqant.core.storage import Submission

pytestmark = pytest.mark.integration
pytest_plugins = ("postgres_support",)

_IDENTITY = Identity(principal_id="worker_operator")
_REVISIONS = frozenset({("worker_contract", "revision-1")})


def _submission(*, key: str = "worker-1") -> Submission:
    return Submission(
        workflow="worker_contract",
        revision="revision-1",
        idempotency_key=key,
        envelope=AcceptedEnvelope(payload={"value": 1}, metadata={}),
        identity=_IDENTITY,
        first_step="first",
        limits=ExecutionLimits(run_timeout=60),
    )


@asynccontextmanager
async def _store(dsn: str) -> AsyncIterator[PostgresStore]:
    store = await PostgresStore.open(dsn)
    try:
        await store.migrate()
        yield store
    finally:
        await store.aclose()


async def test_claim_remaining_seconds_uses_database_clock_and_clamps_expiry(
    isolated_postgres_dsn: str,
) -> None:
    async with _store(isolated_postgres_dsn) as store:
        accepted = await store.accept(_submission())
        async with await psycopg.AsyncConnection.connect(
            isolated_postgres_dsn, autocommit=True
        ) as conn:
            await conn.execute(
                "UPDATE foliqant.executions SET deadline = "
                "clock_timestamp() + interval '20 seconds' "
                "WHERE execution_id = %s",
                (UUID(accepted.execution_id),),
            )
            clock_before = await (await conn.execute("SELECT clock_timestamp()")).fetchone()
            assert clock_before is not None
            claimed = await store.claim("worker", lease_seconds=10, revisions=_REVISIONS)
            clock_after = await (await conn.execute("SELECT clock_timestamp()")).fetchone()
            assert clock_after is not None
            assert claimed is not None
            assert claimed.disposition == "run"
            assert type(claimed.remaining_seconds) is float
            assert 0 < claimed.remaining_seconds <= 20
            # The returned duration corresponds to a clock sampled during claim,
            # independent of either the caller's UTC clock or acceptance time.
            assert (
                (claimed.execution.deadline - clock_after[0]).total_seconds()
                <= claimed.remaining_seconds
                <= (claimed.execution.deadline - clock_before[0]).total_seconds()
            )
            await store.release(claimed.lease)
            await conn.execute(
                "UPDATE foliqant.executions SET deadline = clock_timestamp() - interval '1 second' "
                "WHERE execution_id = %s",
                (UUID(accepted.execution_id),),
            )
        expired = await store.claim("replacement", lease_seconds=10, revisions=_REVISIONS)
        assert expired is not None
        assert expired.disposition == "terminalize_timeout"
        assert expired.remaining_seconds == 0.0
        await store.release(expired.lease)
        await store.cancel(accepted.execution_id, _IDENTITY)
        cancelled = await store.claim("canceller", lease_seconds=10, revisions=_REVISIONS)
        assert cancelled is not None
        assert cancelled.disposition == "terminalize_cancel"
        assert cancelled.remaining_seconds == 0.0


async def test_execution_usage_aggregates_prior_and_current_steps_after_cancellation_and_deadline(
    isolated_postgres_dsn: str,
) -> None:
    async with _store(isolated_postgres_dsn) as store:
        accepted = await store.accept(_submission())
        claimed = await store.claim("worker", lease_seconds=10, revisions=_REVISIONS)
        assert claimed is not None
        lease = claimed.lease
        assert await store.execution_usage(lease) == Usage()
        first_ticket = await store.reserve_attempt(lease, step_id="first", kind="model")
        measured = TokenUsage(10, 4, 1, 0, 2)
        await store.report_usage(lease, step_id="first", ticket=first_ticket, usage=measured)
        await store.reserve_attempt(lease, step_id="first", kind="tool")
        await store.checkpoint(
            lease, step_id="first", record=StepRecord("completed", None, True), next_step="second"
        )
        second_ticket = await store.reserve_attempt(lease, step_id="second", kind="model")
        await store.reserve_attempt(lease, step_id="second", kind="tool")
        assert await store.step_usage(lease, step_id="first") == Usage(1, 1, measured)
        assert await store.execution_usage(lease) == Usage(2, 2, TokenUsage())
        await store.cancel(accepted.execution_id, _IDENTITY)
        async with await psycopg.AsyncConnection.connect(
            isolated_postgres_dsn, autocommit=True
        ) as conn:
            await conn.execute(
                "UPDATE foliqant.executions SET deadline = clock_timestamp() - interval '1 second' "
                "WHERE execution_id = %s",
                (UUID(accepted.execution_id),),
            )
        assert await store.execution_usage(lease) == Usage(2, 2, TokenUsage())
        second_measurement = TokenUsage(3, 2, None, 0, None)
        await store.report_usage(
            lease, step_id="second", ticket=second_ticket, usage=second_measurement
        )
        expected = Usage(2, 2, TokenUsage(13, 6, None, 0, None))
        assert await store.execution_usage(lease) == expected
        await store.release(lease)
        with pytest.raises(ServiceError) as stale:
            await store.execution_usage(lease)
        assert stale.value.code is ErrorCode.CONFLICT
        reclaimed = await store.claim("replacement", lease_seconds=10, revisions=_REVISIONS)
        assert reclaimed is not None
        assert await store.execution_usage(reclaimed.lease) == expected
        async with await psycopg.AsyncConnection.connect(
            isolated_postgres_dsn, autocommit=True
        ) as conn:
            await conn.execute(
                "UPDATE foliqant.executions SET lease_until = "
                "clock_timestamp() - interval '1 second' "
                "WHERE execution_id = %s",
                (UUID(accepted.execution_id),),
            )
        with pytest.raises(ServiceError) as expired:
            await store.execution_usage(reclaimed.lease)
        assert expired.value.code is ErrorCode.CONFLICT
