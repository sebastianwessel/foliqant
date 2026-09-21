"""Per-step budget adapter whose database reservations survive worker restarts."""

import asyncio

from foliqant.core.execution import TokenUsage, Usage
from foliqant.core.storage import Lease
from foliqant.ports.storage import ExecutionStore


class PersistentStepBudget:
    """Bridge AttemptBudget to a fenced store; the store is always authoritative.

    Construct with ``await PersistentStepBudget.open(store, lease, step_id=...)``.
    Each worker claim owns its own instance. Reopening after recovery loads all
    prior reservations, including unknown usage from interrupted model calls.
    ``snapshot`` is the last successful observation, not a database transaction.
    Terminal workers must obtain authoritative persisted totals before committing.
    """

    def __init__(self, store: ExecutionStore, lease: Lease, step_id: str, usage: Usage) -> None:
        self._store = store
        self._lease = lease
        self._step = step_id
        self._usage = usage
        self._lock = asyncio.Lock()

    @classmethod
    async def open(
        cls, store: ExecutionStore, lease: Lease, *, step_id: str
    ) -> "PersistentStepBudget":
        """Load durable reservations; never reset counters on a new worker claim."""
        return cls(store, lease, step_id, await store.step_usage(lease, step_id=step_id))

    async def start_model_request(self) -> int:
        """Commit one reservation before the caller can begin inference."""
        async with self._lock:
            ticket = await self._store.reserve_attempt(
                self._lease, step_id=self._step, kind="model"
            )
            await self._refresh()
            return ticket

    async def finish_model_request(self, ticket: int, usage: TokenUsage) -> None:
        """Report measured usage without refunding or replacing an earlier attempt."""
        async with self._lock:
            await self._store.report_usage(
                self._lease, step_id=self._step, ticket=ticket, usage=usage
            )
            await self._refresh()

    async def start_tool_call(self) -> int:
        """Commit one tool attempt; this is not authorization for a write effect."""
        async with self._lock:
            ticket = await self._store.reserve_attempt(self._lease, step_id=self._step, kind="tool")
            await self._refresh()
            return ticket

    async def _refresh(self) -> None:
        self._usage = await self._store.step_usage(self._lease, step_id=self._step)

    def snapshot(self) -> Usage:
        """Return the last persisted observation; missing measurements remain unknown."""
        return self._usage
