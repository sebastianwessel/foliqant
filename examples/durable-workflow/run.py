"""Run a compiled workflow through PostgreSQL and a resumable worker, without AI."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from foliqant.adapters.handlers import HandlerExecutor, HandlerRegistration
from foliqant.adapters.storage.postgres import PostgresStore
from foliqant.adapters.telemetry.logging import configure_logging
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.compiler import compile_workflow
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.core.execution import StepOutcome
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject
from foliqant.core.storage import Submission
from foliqant.ports.execution import StepContext
from foliqant.workers.execution import ExecutionWorker, WorkflowBinding


async def echo(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    """A pure read handler; real integrations use context.caller and context.budget."""
    return StepOutcome(inputs)


async def demonstrate(dsn: str) -> ExecutionResult:
    """Migrate a dedicated example database, accept a run, execute, and retrieve it."""
    registration = HandlerRegistration(
        echo,
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ("message",),
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    )
    plan = compile_workflow(
        Path(__file__).resolve().parent,
        model_aliases={},
        tool_catalogs={},
        handler_names={"echo"},
    )
    identity = Identity(principal_id="example_operator")
    store = await PostgresStore.open(dsn)
    worker = ExecutionWorker(
        store,
        [WorkflowBinding(plan, HandlerExecutor({"echo": registration}), WorkflowSchemas(plan))],
        worker_id="example_worker",
        concurrency=1,
    )
    try:
        await store.migrate()  # Explicit operator step for a dedicated example DB.
        accepted = await store.accept(
            Submission(
                plan.name,
                plan.revision,
                str(uuid4()),
                accept_envelope(Envelope(payload={"message": "Hallo"}), identity),
                identity,
                plan.start,
            )
        )
        # This dedicated database has no other producer. If an interrupted prior
        # demo exists, the worker recovers it first, then picks up this submission.
        while (await store.get(accepted.execution_id, identity)).result is None:
            if await worker.run_once() is None:
                await asyncio.sleep(0.25)
        stored = await store.get(accepted.execution_id, identity)
        assert stored.result is not None
        return to_execution_result(stored.result)
    finally:
        clean = await worker.aclose()
        if clean:
            await store.aclose()
        else:
            # Do not close a shared client while a worker still owns active I/O.
            raise RuntimeError("Worker cleanup remains incomplete")


async def main() -> None:
    logs = configure_logging()
    try:
        result = await demonstrate(os.environ["FOLIQANT_EXAMPLE_POSTGRES_DSN"])
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    finally:
        clean = await asyncio.to_thread(logs.close, timeout=1.0)
        if not clean:
            raise RuntimeError("Logging cleanup remains incomplete")


if __name__ == "__main__":
    asyncio.run(main())
