"""Owned bounded async connections, transactions and explicit schema checks."""

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Self

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout, TooManyRequests

from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.storage import DeliveryLease, Lease, StoredExecution

from . import mapping
from .mapping import Row
from .schema import MIGRATION_HASH, MIGRATION_LOCK, MIGRATION_SQL, SCHEMA_VERSION


class StoreBase:
    """Internal resource ownership shared by execution and outbox operations."""

    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[Row]],
        *,
        concurrency: int,
        queue_limit: int,
        operation_timeout: float,
        allow_anonymous: bool,
    ) -> None:
        self._pool = pool
        self._limiter = CapacityLimiter(concurrency=concurrency, queue_limit=queue_limit)
        self._timeout = operation_timeout
        self._allow_anonymous = allow_anonymous
        self._closed = False

    @classmethod
    async def open(
        cls,
        dsn: str,
        *,
        concurrency: int = 4,
        queue_limit: int = 16,
        operation_timeout: float = 10,
        allow_anonymous: bool = False,
    ) -> Self:
        """Open owned connections without applying or changing the database schema."""
        if (
            not isinstance(dsn, str)
            or not dsn.strip()
            or type(concurrency) is not int
            or concurrency < 1
            or type(queue_limit) is not int
            or queue_limit < 0
            or type(operation_timeout) not in (int, float)
            or not math.isfinite(operation_timeout)
            or operation_timeout <= 0
            or type(allow_anonymous) is not bool
        ):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        pool: AsyncConnectionPool[AsyncConnection[Row]] = AsyncConnectionPool(
            dsn,
            min_size=0,
            max_size=concurrency,
            open=False,
            timeout=operation_timeout,
            max_waiting=concurrency + queue_limit,
            reconnect_timeout=operation_timeout,
            kwargs={
                "row_factory": dict_row,
                "options": "-c timezone=UTC",
                "connect_timeout": max(1, math.ceil(operation_timeout)),
            },
        )
        try:
            await pool.open()
            return cls(
                pool,
                concurrency=concurrency,
                queue_limit=queue_limit,
                operation_timeout=operation_timeout,
                allow_anonymous=allow_anonymous,
            )
        except Exception:
            await pool.close(timeout=operation_timeout)
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None

    async def aclose(self) -> None:
        """Stop accepting operations and close this adapter's connection pool."""
        self._closed = True
        try:
            await self._pool.close(timeout=self._timeout)
        except Exception:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None

    @asynccontextmanager
    async def _transaction(self, *, schema: bool = True) -> AsyncIterator[AsyncConnection[Row]]:
        if self._closed:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        deadline = asyncio.get_running_loop().time() + self._timeout
        async with self._limiter.slot(deadline=deadline):
            try:
                async with asyncio.timeout_at(deadline):
                    async with self._pool.connection(timeout=self._timeout) as conn:
                        async with conn.transaction():
                            # Server limits supplement the outer admission/transaction deadline.
                            milliseconds = str(max(1, int(self._timeout * 1000)))
                            await conn.execute(
                                "SELECT set_config('statement_timeout', %s, true), "
                                "set_config('lock_timeout', %s, true), "
                                "set_config('idle_in_transaction_session_timeout', %s, true)",
                                (milliseconds, milliseconds, milliseconds),
                            )
                            if schema:
                                await self._check_schema(conn)
                            yield conn
            except ServiceError:
                raise
            except (
                TimeoutError,
                PoolTimeout,
                psycopg.errors.QueryCanceled,
                psycopg.errors.LockNotAvailable,
            ):
                raise ServiceError(ErrorCode.TIMEOUT) from None
            except TooManyRequests:
                raise ServiceError(ErrorCode.CAPACITY_EXCEEDED, retryable=True) from None
            except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName):
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
            except Exception:
                raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None

    async def _check_schema(self, conn: AsyncConnection[Row]) -> None:
        cursor = await conn.execute("SELECT version, sql_hash FROM foliqant.schema_version")
        versions = await cursor.fetchall()
        if versions != [{"version": SCHEMA_VERSION, "sql_hash": MIGRATION_HASH}]:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)

    async def migrate(self) -> None:
        """Apply the known migration under an advisory lock or reject schema drift."""
        async with self._transaction(schema=False) as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
            cursor = await conn.execute("SELECT to_regnamespace('foliqant') AS existing")
            row = await cursor.fetchone()
            assert row is not None
            if row["existing"] is not None:
                await self._check_schema(conn)
                return
            await conn.execute(MIGRATION_SQL)
            await conn.execute(
                "INSERT INTO foliqant.schema_version(version, sql_hash) VALUES (%s, %s)",
                (SCHEMA_VERSION, MIGRATION_HASH),
            )

    async def _stored(self, conn: AsyncConnection[Row], row: Row) -> StoredExecution:
        cursor = await conn.execute(
            "SELECT * FROM foliqant.checkpoints WHERE execution_id = %s ORDER BY position",
            (row["execution_id"],),
        )
        return mapping.execution(row, await cursor.fetchall())

    async def _locked(self, conn: AsyncConnection[Row], lease: Lease) -> Row:
        if not isinstance(lease, Lease):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        mapping.opaque(lease.owner)
        if type(lease.fence) is not int or not 1 <= lease.fence <= 9223372036854775807:
            raise ServiceError(ErrorCode.INVALID_INPUT)
        cursor = await conn.execute(
            "SELECT * FROM foliqant.executions WHERE execution_id = %s FOR UPDATE",
            (mapping.uuid_value(lease.execution_id),),
        )
        row = await cursor.fetchone()
        await self._live(conn, row, lease.owner, lease.fence, expected="running")
        assert row is not None
        return row

    async def _delivery_locked(self, conn: AsyncConnection[Row], lease: DeliveryLease) -> Row:
        if not isinstance(lease, DeliveryLease):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        mapping.opaque(lease.owner)
        if type(lease.fence) is not int or not 1 <= lease.fence <= 9223372036854775807:
            raise ServiceError(ErrorCode.INVALID_INPUT)
        cursor = await conn.execute(
            "SELECT * FROM foliqant.outbox WHERE event_id = %s FOR UPDATE",
            (mapping.uuid_value(lease.event_id),),
        )
        row = await cursor.fetchone()
        await self._live(conn, row, lease.owner, lease.fence, expected="pending")
        assert row is not None
        return row

    async def _live(
        self,
        conn: AsyncConnection[Row],
        row: Row | None,
        owner: str,
        fence: int,
        *,
        expected: str,
    ) -> datetime:
        cursor = await conn.execute("SELECT clock_timestamp() AS now")
        clock = await cursor.fetchone()
        assert clock is not None
        now: datetime = clock["now"]
        if (
            row is None
            or row["status"] != expected
            or row["owner"] != owner
            or row["fence"] != fence
            or row["lease_until"] is None
            or row["lease_until"] <= now
        ):
            raise ServiceError(ErrorCode.CONFLICT)
        return now

    async def _working(self, conn: AsyncConnection[Row], row: Row, *, step_id: str) -> None:
        now = await self._live(conn, row, row["owner"], row["fence"], expected="running")
        if row["cancel_requested"]:
            raise ServiceError(ErrorCode.CANCELLED)
        if row["deadline"] <= now:
            raise ServiceError(ErrorCode.TIMEOUT)
        if row["current_step"] != step_id:
            raise ServiceError(ErrorCode.CONFLICT)
        cursor = await conn.execute(
            "SELECT count(*) AS count FROM foliqant.checkpoints WHERE execution_id = %s",
            (row["execution_id"],),
        )
        count = await cursor.fetchone()
        assert count is not None
        if count["count"] >= row["submission"]["limits"]["max_steps"]:
            raise ServiceError(ErrorCode.BUDGET_EXHAUSTED)
