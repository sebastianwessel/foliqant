"""Immutable durable-storage values; no database or boundary-validation dependencies."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .envelope import AcceptedEnvelope
from .execution import RunResult, StepRecord
from .identity import Identity
from .runner import ExecutionLimits

type StoredStatus = Literal[
    "accepted", "running", "completed", "needs_review", "failed", "cancelled"
]
type AttemptKind = Literal["model", "tool"]


@dataclass(frozen=True, slots=True)
class DeliveryRequest:
    """A deployment-owned output binding, never an input-supplied network URL."""

    destination: str
    max_attempts: int = 8


@dataclass(frozen=True, slots=True)
class Submission:
    """Authenticated immutable work submitted under one scoped deduplication key."""

    workflow: str
    revision: str
    idempotency_key: str
    envelope: AcceptedEnvelope
    identity: Identity
    first_step: str
    limits: ExecutionLimits = ExecutionLimits()
    delivery: DeliveryRequest | None = None


@dataclass(frozen=True, slots=True)
class Lease:
    """Ownership proof checked against a live database lease on every write."""

    execution_id: str
    owner: str
    fence: int


@dataclass(frozen=True, slots=True)
class Checkpoint:
    step_id: str
    record: StepRecord
    next_step: str | None


@dataclass(frozen=True, slots=True)
class StoredExecution:
    execution_id: str
    submission: Submission
    accepted_at: datetime
    deadline: datetime
    status: StoredStatus
    current_step: str | None
    cancel_requested: bool
    checkpoints: tuple[Checkpoint, ...] = ()
    result: RunResult | None = None


@dataclass(frozen=True, slots=True)
class ClaimedExecution:
    execution: StoredExecution
    lease: Lease
    lease_until: datetime
    disposition: Literal["run", "terminalize_timeout", "terminalize_cancel"]


@dataclass(frozen=True, slots=True)
class DeliveryLease:
    event_id: str
    owner: str
    fence: int


@dataclass(frozen=True, slots=True)
class ClaimedDelivery:
    lease: DeliveryLease
    execution_id: str
    destination: str
    result: RunResult
    attempt: int
    max_attempts: int
    lease_until: datetime
