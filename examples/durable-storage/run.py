"""Exercise durable storage with one deterministic step and no model calls."""

import asyncio
import os
from uuid import uuid4

from foliqant.adapters.storage.postgres import PostgresStore
from foliqant.adapters.telemetry.logging import configure_logging
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.core.execution import RunResult, StepRecord, Usage
from foliqant.core.identity import Identity
from foliqant.core.storage import Submission


async def demonstrate(dsn: str) -> str:
    """Use a dedicated example database; migrate explicitly, then persist a result."""
    store = await PostgresStore.open(dsn)
    try:
        await store.migrate()
        identity = Identity(principal_id="example_operator")
        revision = f"example-{uuid4()}"
        envelope = accept_envelope(Envelope(payload={"message": "hello"}), identity)
        accepted = await store.accept(
            Submission(
                workflow="hello",
                revision=revision,
                idempotency_key=revision,
                envelope=envelope,
                identity=identity,
                first_step="done",
            )
        )
        claim = await store.claim(
            "example_worker", lease_seconds=30, revisions=frozenset({("hello", revision)})
        )
        assert claim is not None and claim.disposition == "run"
        record = StepRecord("completed", result=envelope.payload, has_result=True)
        await store.checkpoint(claim.lease, step_id="done", record=record, next_step=None)
        await store.finish(
            claim.lease,
            RunResult(
                execution_id=accepted.execution_id,
                workflow="hello",
                revision=revision,
                status="completed",
                payload=envelope.payload,
                metadata=envelope.metadata,
                decisions=(("done", record),),
                usage=Usage(),
            ),
        )
        execution_id = accepted.execution_id
    finally:
        await store.aclose()

    reopened = await PostgresStore.open(dsn)
    try:
        stored = await reopened.get(execution_id, identity)
        assert stored.status == "completed" and stored.result is not None
        return stored.status
    finally:
        await reopened.aclose()


async def main() -> None:
    logging_runtime = configure_logging()
    try:
        print(await demonstrate(os.environ["FOLIQANT_EXAMPLE_POSTGRES_DSN"]))
    finally:
        clean = await asyncio.to_thread(logging_runtime.close, timeout=1.0)
        if not clean:
            raise RuntimeError("Safe logging cleanup did not finish")


if __name__ == "__main__":
    asyncio.run(main())
