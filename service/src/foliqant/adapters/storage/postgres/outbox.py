"""Persisted at-least-once result delivery with bounded attempts and live fencing."""

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.storage import ClaimedDelivery, DeliveryLease

from . import mapping
from .base import StoreBase


class OutboxOperations(StoreBase):
    """A delivery retry never reopens or reruns its completed execution."""

    async def claim_delivery(
        self,
        owner: str,
        *,
        lease_seconds: float,
    ) -> ClaimedDelivery | None:
        """Reserve a delivery attempt and exhaust up to 100 crashed final attempts.

        None means no deliverable row; another poll may still recover exhausted
        rows beyond the bounded maintenance batch.
        """
        mapping.opaque(owner)
        seconds = mapping.duration(lease_seconds, minimum=1, maximum=300)
        async with self._transaction() as conn:
            # Skip active owners and locked rows, including recovery work, so an
            # unrelated worker's transaction cannot serialize all delivery claims.
            await conn.execute(
                """WITH exhausted AS (
                    SELECT event_id FROM foliqant.outbox
                    WHERE status = 'pending' AND attempts >= max_attempts
                    AND (lease_until IS NULL OR lease_until <= clock_timestamp())
                    ORDER BY available_at, event_id FOR UPDATE SKIP LOCKED LIMIT 100
                ) UPDATE foliqant.outbox AS o SET status = 'exhausted', owner = NULL,
                lease_until = NULL FROM exhausted AS e WHERE o.event_id = e.event_id"""
            )
            cursor = await conn.execute(
                """SELECT * FROM foliqant.outbox WHERE status = 'pending'
                AND attempts < max_attempts AND available_at <= clock_timestamp()
                AND (lease_until IS NULL OR lease_until <= clock_timestamp())
                ORDER BY available_at, event_id FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            if row["fence"] == 9223372036854775807:
                raise ServiceError(ErrorCode.CONFLICT)
            cursor = await conn.execute(
                """UPDATE foliqant.outbox SET owner = %s, fence = fence + 1,
                attempts = attempts + 1, lease_until = clock_timestamp() + %s * interval '1 second'
                WHERE event_id = %s RETURNING *""",
                (owner, seconds, row["event_id"]),
            )
            row = await cursor.fetchone()
            assert row is not None
            claimed = ClaimedDelivery(
                DeliveryLease(str(row["event_id"]), owner, row["fence"]),
                str(row["execution_id"]),
                row["destination"],
                mapping.decode_result(row["result"]),
                row["attempts"],
                row["max_attempts"],
                row["lease_until"],
            )
            await self._live(conn, row, owner, row["fence"], expected="pending")
            return claimed

    async def complete_delivery(self, lease: DeliveryLease) -> None:
        """Acknowledge a delivery only while its lease is live."""
        async with self._transaction() as conn:
            row = await self._delivery_locked(conn, lease)
            cursor = await conn.execute(
                """UPDATE foliqant.outbox SET status = 'delivered', owner = NULL, lease_until = NULL
                WHERE event_id = %s AND lease_until > clock_timestamp() RETURNING event_id""",
                (row["event_id"],),
            )
            if await cursor.fetchone() is None:
                raise ServiceError(ErrorCode.CONFLICT)
            await self._live(conn, row, lease.owner, lease.fence, expected="pending")

    async def retry_delivery(
        self,
        lease: DeliveryLease,
        *,
        error: ErrorCode,
        delay_seconds: float,
    ) -> None:
        """Schedule a bounded retry or exhaust the accepted attempt budget."""
        seconds = mapping.duration(delay_seconds, minimum=0, maximum=3600)
        if not isinstance(error, ErrorCode):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        async with self._transaction() as conn:
            row = await self._delivery_locked(conn, lease)
            cursor = await conn.execute(
                """UPDATE foliqant.outbox
                SET status = CASE WHEN attempts >= max_attempts THEN 'exhausted' ELSE 'pending' END,
                available_at = clock_timestamp() + %s * interval '1 second', last_error = %s,
                owner = NULL, lease_until = NULL
                WHERE event_id = %s AND lease_until > clock_timestamp() RETURNING event_id""",
                (seconds, error.value, row["event_id"]),
            )
            if await cursor.fetchone() is None:
                raise ServiceError(ErrorCode.CONFLICT)
            await self._live(conn, row, lease.owner, lease.fence, expected="pending")
