"""Async transactional persistence; no transaction spans external inference or effects."""

from datetime import datetime
from typing import Protocol

from foliqant.core.errors import ErrorCode
from foliqant.core.execution import RunResult, StepRecord, TokenUsage, Usage
from foliqant.core.identity import Identity
from foliqant.core.storage import (
    AttemptKind,
    ClaimedDelivery,
    ClaimedExecution,
    DeliveryLease,
    Lease,
    StoredExecution,
    Submission,
)


class ExecutionStore(Protocol):
    """Durable execution and result-delivery contract, independent of transports."""

    async def accept(self, submission: Submission) -> StoredExecution: ...
    async def get(self, execution_id: str, identity: Identity) -> StoredExecution: ...
    async def cancel(self, execution_id: str, identity: Identity) -> StoredExecution: ...
    async def claim(
        self,
        owner: str,
        *,
        lease_seconds: float,
        revisions: frozenset[tuple[str, str]],
    ) -> ClaimedExecution | None: ...
    async def release(self, lease: Lease) -> None: ...
    async def heartbeat(self, lease: Lease, *, lease_seconds: float) -> datetime: ...
    async def checkpoint(
        self,
        lease: Lease,
        *,
        step_id: str,
        record: StepRecord,
        next_step: str | None,
    ) -> None: ...
    async def reserve_attempt(self, lease: Lease, *, step_id: str, kind: AttemptKind) -> int: ...
    async def report_usage(
        self,
        lease: Lease,
        *,
        step_id: str,
        ticket: int,
        usage: TokenUsage,
    ) -> None: ...
    async def step_usage(self, lease: Lease, *, step_id: str) -> Usage: ...
    async def execution_usage(self, lease: Lease) -> Usage:
        """Read all persisted attempts under live ownership, including expired/cancelled runs."""
        ...

    async def finish(self, lease: Lease, result: RunResult) -> None: ...
    async def claim_delivery(
        self, owner: str, *, lease_seconds: float
    ) -> ClaimedDelivery | None: ...
    async def complete_delivery(self, lease: DeliveryLease) -> None: ...
    async def retry_delivery(
        self,
        lease: DeliveryLease,
        *,
        error: ErrorCode,
        delay_seconds: float,
    ) -> None: ...
