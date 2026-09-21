"""Fenced execution intake, resumable checkpoints, budgets and terminalization."""

from dataclasses import asdict
from datetime import datetime
from typing import Literal, cast
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Json

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import RunResult, StepRecord, TokenUsage, Usage
from foliqant.core.identity import Identity
from foliqant.core.storage import AttemptKind, ClaimedExecution, Lease, StoredExecution, Submission

from . import mapping
from .base import StoreBase
from .mapping import Row


class ExecutionOperations(StoreBase):
    """Execution operations sharing one row lock for every state transition."""

    async def accept(self, submission: Submission) -> StoredExecution:
        """Atomically deduplicate immutable intake within exact authenticated identity."""
        submission = mapping.normalize(submission, allow_anonymous=self._allow_anonymous)
        identity = submission.identity
        scoped = mapping.scope(identity)
        try:
            digest = mapping.digest(submission)
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError(ErrorCode.INVALID_INPUT) from None
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """WITH accepted AS MATERIALIZED (SELECT clock_timestamp() AS now)
                INSERT INTO foliqant.executions (
                    execution_id, scope, tenant_id, principal_id, workflow, revision,
                    idempotency_key, input_digest, submission, current_step, accepted_at, deadline
                ) SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, accepted.now,
                    accepted.now + %s * interval '1 second' FROM accepted
                ON CONFLICT (scope, workflow, idempotency_key) DO NOTHING RETURNING *""",
                (
                    uuid4(),
                    scoped,
                    identity.tenant_id,
                    identity.principal_id,
                    submission.workflow,
                    submission.revision,
                    submission.idempotency_key,
                    digest,
                    Json(mapping.submission_json(submission)),
                    submission.first_step,
                    submission.limits.run_timeout,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                cursor = await conn.execute(
                    """SELECT * FROM foliqant.executions
                    WHERE scope = %s AND workflow = %s AND idempotency_key = %s FOR SHARE""",
                    (scoped, submission.workflow, submission.idempotency_key),
                )
                row = await cursor.fetchone()
                assert row is not None
                if (
                    row["tenant_id"] != identity.tenant_id
                    or row["principal_id"] != identity.principal_id
                    or row["input_digest"] != digest
                ):
                    raise ServiceError(ErrorCode.CONFLICT)
            return await self._stored(conn, row)

    async def _by_identity(
        self,
        conn: AsyncConnection[Row],
        execution_id: str,
        identity: Identity,
    ) -> Row:
        if not isinstance(identity, Identity):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        cursor = await conn.execute(
            """SELECT * FROM foliqant.executions WHERE execution_id = %s
            AND scope = %s AND tenant_id IS NOT DISTINCT FROM %s
            AND principal_id IS NOT DISTINCT FROM %s FOR UPDATE""",
            (
                mapping.uuid_value(execution_id),
                mapping.scope(identity),
                identity.tenant_id,
                identity.principal_id,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ServiceError(ErrorCode.NOT_FOUND)
        return row

    async def get(self, execution_id: str, identity: Identity) -> StoredExecution:
        """Read an execution only within the independently optional identity scope."""
        async with self._transaction() as conn:
            return await self._stored(conn, await self._by_identity(conn, execution_id, identity))

    async def cancel(self, execution_id: str, identity: Identity) -> StoredExecution:
        """Persist a cancellation request; terminal records remain immutable."""
        async with self._transaction() as conn:
            row = await self._by_identity(conn, execution_id, identity)
            if row["status"] in ("accepted", "running"):
                cursor = await conn.execute(
                    """UPDATE foliqant.executions SET cancel_requested = true
                    WHERE execution_id = %s RETURNING *""",
                    (row["execution_id"],),
                )
                updated = await cursor.fetchone()
                assert updated is not None
                row = updated
            return await self._stored(conn, row)

    async def claim(
        self,
        owner: str,
        *,
        lease_seconds: float,
        revisions: frozenset[tuple[str, str]],
    ) -> ClaimedExecution | None:
        """Claim eligible supported revisions, reclaiming expired ownership with a new fence."""
        mapping.opaque(owner)
        seconds = mapping.duration(lease_seconds, minimum=1, maximum=300)
        if not isinstance(revisions, frozenset):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        workflows: list[str] = []
        versions: list[str] = []
        for pair in revisions:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ServiceError(ErrorCode.INVALID_INPUT)
            workflows.append(mapping.identifier(pair[0]))
            versions.append(mapping.opaque(pair[1], maximum=512))
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """SELECT * FROM foliqant.executions
                WHERE (status = 'accepted' OR
                    (status = 'running' AND lease_until <= clock_timestamp()))
                AND (workflow, revision) IN (SELECT * FROM unnest(%s::text[], %s::text[]))
                ORDER BY accepted_at, execution_id FOR UPDATE SKIP LOCKED LIMIT 1""",
                (workflows, versions),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            if row["fence"] == 9223372036854775807:
                raise ServiceError(ErrorCode.CONFLICT)
            cursor = await conn.execute(
                """UPDATE foliqant.executions SET status = 'running', owner = %s,
                fence = fence + 1, lease_until = clock_timestamp() + %s * interval '1 second'
                WHERE execution_id = %s RETURNING *""",
                (owner, seconds, row["execution_id"]),
            )
            row = await cursor.fetchone()
            assert row is not None
            execution = await self._stored(conn, row)
            now = await self._live(conn, row, owner, row["fence"], expected="running")
            disposition: Literal["run", "terminalize_timeout", "terminalize_cancel"] = "run"
            if row["cancel_requested"]:
                disposition = "terminalize_cancel"
            elif row["deadline"] <= now:
                disposition = "terminalize_timeout"
            return ClaimedExecution(
                execution,
                Lease(str(row["execution_id"]), owner, row["fence"]),
                row["lease_until"],
                disposition,
            )

    async def heartbeat(self, lease: Lease, *, lease_seconds: float) -> datetime:
        """Extend only a still-live lease, never revive expired ownership."""
        seconds = mapping.duration(lease_seconds, minimum=1, maximum=300)
        async with self._transaction() as conn:
            row = await self._locked(conn, lease)
            cursor = await conn.execute(
                """UPDATE foliqant.executions
                SET lease_until = clock_timestamp() + %s * interval '1 second'
                WHERE execution_id = %s AND lease_until > clock_timestamp()
                RETURNING lease_until""",
                (seconds, row["execution_id"]),
            )
            updated = await cursor.fetchone()
            if updated is None:
                raise ServiceError(ErrorCode.CONFLICT)
            await self._live(conn, row, lease.owner, lease.fence, expected="running")
            return cast(datetime, updated["lease_until"])

    async def release(self, lease: Lease) -> None:
        """Release live ownership while preserving checkpoints and consumed attempts."""
        async with self._transaction() as conn:
            row = await self._locked(conn, lease)
            cursor = await conn.execute(
                """UPDATE foliqant.executions SET status = 'accepted', owner = NULL,
                lease_until = NULL WHERE execution_id = %s AND lease_until > clock_timestamp()
                RETURNING execution_id""",
                (row["execution_id"],),
            )
            if await cursor.fetchone() is None:
                raise ServiceError(ErrorCode.CONFLICT)

            await self._live(conn, row, lease.owner, lease.fence, expected="running")

    async def checkpoint(
        self,
        lease: Lease,
        *,
        step_id: str,
        record: StepRecord,
        next_step: str | None,
    ) -> None:
        """Commit one immutable result and selected transition atomically."""
        mapping.identifier(step_id)
        if next_step is not None:
            mapping.identifier(next_step)
        value = mapping.record_json(record)
        if record.status not in ("completed", "needs_review"):
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        async with self._transaction() as conn:
            row = await self._locked(conn, lease)
            cursor = await conn.execute(
                "SELECT * FROM foliqant.checkpoints WHERE execution_id = %s ORDER BY position",
                (row["execution_id"],),
            )
            records = await cursor.fetchall()
            for previous in records:
                if previous["step_id"] == step_id:
                    if (
                        mapping.canonical(previous["record"]) == mapping.canonical(value)
                        and previous["next_step"] == next_step
                    ):
                        await self._live(conn, row, lease.owner, lease.fence, expected="running")
                        return
                    raise ServiceError(ErrorCode.CONFLICT)
            await self._working(conn, row, step_id=step_id)
            if next_step == step_id or any(item["step_id"] == next_step for item in records):
                raise ServiceError(ErrorCode.CONFLICT)
            await self._working(conn, row, step_id=step_id)
            await conn.execute(
                """INSERT INTO foliqant.checkpoints(
                    execution_id, step_id, position, record, next_step)
                VALUES (%s, %s, %s, %s, %s)""",
                (row["execution_id"], step_id, len(records) + 1, Json(value), next_step),
            )
            cursor = await conn.execute(
                """UPDATE foliqant.executions SET current_step = %s
                WHERE execution_id = %s AND lease_until > clock_timestamp()
                AND deadline > clock_timestamp() AND NOT cancel_requested RETURNING execution_id""",
                (next_step, row["execution_id"]),
            )
            if await cursor.fetchone() is None:
                raise ServiceError(ErrorCode.CONFLICT)

            now = await self._live(conn, row, lease.owner, lease.fence, expected="running")
            if row["deadline"] <= now:
                raise ServiceError(ErrorCode.TIMEOUT)

    async def reserve_attempt(self, lease: Lease, *, step_id: str, kind: AttemptKind) -> int:
        """Charge one persisted attempt before I/O; reclaim never resets its ticket sequence."""
        mapping.identifier(step_id)
        if kind not in ("model", "tool"):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        async with self._transaction() as conn:
            row = await self._locked(conn, lease)
            await self._working(conn, row, step_id=step_id)
            cursor = await conn.execute(
                """SELECT count(*) AS count FROM foliqant.attempts
                WHERE execution_id = %s AND step_id = %s AND kind = %s""",
                (row["execution_id"], step_id, kind),
            )
            count = await cursor.fetchone()
            assert count is not None
            ticket = int(count["count"]) + 1
            limit = "model_requests_per_step" if kind == "model" else "tool_calls_per_step"
            if ticket > row["submission"]["limits"][limit]:
                raise ServiceError(ErrorCode.BUDGET_EXHAUSTED)
            await self._working(conn, row, step_id=step_id)
            await conn.execute(
                """INSERT INTO foliqant.attempts(execution_id, step_id, kind, ticket)
                VALUES (%s, %s, %s, %s)""",
                (row["execution_id"], step_id, kind, ticket),
            )
            await self._working(conn, row, step_id=step_id)
            return ticket

    async def report_usage(
        self,
        lease: Lease,
        *,
        step_id: str,
        ticket: int,
        usage: TokenUsage,
    ) -> None:
        """Report measured model tokens exactly once; unknown reports remain unknown."""
        mapping.identifier(step_id)
        if type(ticket) is not int or ticket < 1 or not isinstance(usage, TokenUsage):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        value = asdict(TokenUsage(**asdict(usage)))
        async with self._transaction() as conn:
            row = await self._locked(conn, lease)
            cursor = await conn.execute(
                """SELECT usage FROM foliqant.attempts WHERE execution_id = %s AND step_id = %s
                AND kind = 'model' AND ticket = %s""",
                (row["execution_id"], step_id, ticket),
            )
            attempt = await cursor.fetchone()
            if attempt is None:
                raise ServiceError(ErrorCode.CONFLICT)
            if attempt["usage"] is not None:
                if attempt["usage"] != value:
                    raise ServiceError(ErrorCode.CONFLICT)
            else:
                await self._live(conn, row, lease.owner, lease.fence, expected="running")
                await conn.execute(
                    """UPDATE foliqant.attempts SET usage = %s WHERE execution_id = %s
                    AND step_id = %s AND kind = 'model' AND ticket = %s""",
                    (Json(value), row["execution_id"], step_id, ticket),
                )
            await self._live(conn, row, lease.owner, lease.fence, expected="running")

    async def step_usage(self, lease: Lease, *, step_id: str) -> Usage:
        """Return persisted step usage without resetting it on worker recovery."""
        mapping.identifier(step_id)
        async with self._transaction() as conn:
            row = await self._locked(conn, lease)
            cursor = await conn.execute(
                "SELECT kind, usage FROM foliqant.attempts "
                "WHERE execution_id = %s AND step_id = %s",
                (row["execution_id"], step_id),
            )
            usage = mapping.aggregate(await cursor.fetchall())
            await self._live(conn, row, lease.owner, lease.fence, expected="running")
            return usage

    async def finish(self, lease: Lease, result: RunResult) -> None:
        """Store validated immutable terminal output and its optional outbox event together."""
        public = mapping.result_json(result)
        async with self._transaction() as conn:
            row = await self._locked(conn, lease)
            if (
                result.execution_id != str(row["execution_id"])
                or result.workflow != row["workflow"]
                or result.revision != row["revision"]
            ):
                raise ServiceError(ErrorCode.CONFLICT)
            if mapping.canonical(public["metadata"]) != mapping.canonical(
                row["submission"]["envelope"]["metadata"]
            ):
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            if result.status in ("failed", "cancelled") and (
                mapping.canonical(public["payload"])
                != mapping.canonical(row["submission"]["envelope"]["payload"])
            ):
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            cursor = await conn.execute(
                "SELECT kind, usage FROM foliqant.attempts WHERE execution_id = %s",
                (row["execution_id"],),
            )
            if result.usage != mapping.aggregate(await cursor.fetchall()):
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            cursor = await conn.execute(
                "SELECT step_id, record FROM foliqant.checkpoints "
                "WHERE execution_id = %s ORDER BY position",
                (row["execution_id"],),
            )
            checkpoints = await cursor.fetchall()
            checkpoint_ids = [item["step_id"] for item in checkpoints]
            persisted_ids = set(checkpoint_ids)
            if [step for step, _ in result.decisions if step in persisted_ids] != checkpoint_ids:
                raise ServiceError(ErrorCode.CONFLICT)
            for checkpoint in checkpoints:
                if mapping.canonical(
                    public["decisions"].get(checkpoint["step_id"])
                ) != mapping.canonical(checkpoint["record"]):
                    raise ServiceError(ErrorCode.CONFLICT)
            now = await self._live(conn, row, lease.owner, lease.fence, expected="running")
            success = result.status in ("completed", "needs_review")
            if success:
                if row["cancel_requested"]:
                    raise ServiceError(ErrorCode.CANCELLED)
                if row["deadline"] <= now:
                    raise ServiceError(ErrorCode.TIMEOUT)
                if row["current_step"] is not None:
                    raise ServiceError(ErrorCode.CONFLICT)
            cursor = await conn.execute(
                """UPDATE foliqant.executions SET status = %s, result = %s,
                owner = NULL, lease_until = NULL WHERE execution_id = %s
                AND lease_until > clock_timestamp()
                AND (NOT %s OR (deadline > clock_timestamp() AND NOT cancel_requested))
                RETURNING execution_id""",
                (result.status, Json(public), row["execution_id"], success),
            )
            if await cursor.fetchone() is None:
                raise ServiceError(ErrorCode.CONFLICT)
            delivery = row["submission"]["delivery"]
            if delivery is not None:
                await conn.execute(
                    """INSERT INTO foliqant.outbox(
                        event_id, execution_id, destination, result, max_attempts
                    ) VALUES (%s, %s, %s, %s, %s)""",
                    (
                        uuid4(),
                        row["execution_id"],
                        delivery["destination"],
                        Json(public),
                        delivery["max_attempts"],
                    ),
                )
            # The row was terminalized in this transaction; validate against the
            # original ownership proof once more after the outbox insert.
            await self._live(conn, row, lease.owner, lease.fence, expected="running")
