"""Run the nondurable embedded workflow without inference or network access."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from foliqant.adapters.validation import WorkflowSchemas
from foliqant.compiler import compile_workflow
from foliqant.contracts.envelope import Envelope, Metadata, accept_envelope
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, JsonValue, freeze_json
from foliqant.core.plan import HandlerStepPlan
from foliqant.core.runner import WorkflowRunner
from foliqant.ports.execution import OperationStep, StepContext
from foliqant.ports.observation import ExecutionObserver

EXAMPLE_DIRECTORY = Path(__file__).resolve().parent


class DeterministicExecutor:
    """A local demonstration executor with no model, tool, or network calls."""

    async def execute(
        self,
        step: OperationStep,
        inputs: FrozenObject,
        context: StepContext,
    ) -> StepOutcome:
        await asyncio.sleep(0)
        if not isinstance(step, HandlerStepPlan):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if step.handler == "route_request":
            priority = inputs["priority"]
            if priority is None:
                return StepOutcome(
                    freeze_json({"reason": "missing_priority"}),
                    needs_review=True,
                )
            queue = "expedite" if priority == "urgent" else "normal"
            return StepOutcome(freeze_json({"requestId": inputs["request_id"], "queue": queue}))
        if step.handler == "render_result":
            return StepOutcome(inputs["routed"])
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)


async def run_example(
    payload: dict[str, JsonValue],
    *,
    executor: DeterministicExecutor | None = None,
    observer: ExecutionObserver | None = None,
) -> ExecutionResult:
    """Compile and run once; this example deliberately provides no durability."""

    plan = compile_workflow(
        EXAMPLE_DIRECTORY,
        model_aliases={},
        tool_catalogs={},
        handler_names={"route_request", "render_result"},
    )
    schemas = WorkflowSchemas(plan)
    selected_executor = executor or DeterministicExecutor()
    runner = WorkflowRunner(
        plan,
        executor=selected_executor,
        validator=schemas,
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        observer=observer,
    )
    # These are host-supplied trusted demo values. This example performs no
    # authentication; untrusted envelope metadata cannot create or replace them.
    identity = Identity(tenant_id="example_org", principal_id="example_user")
    envelope = accept_envelope(
        Envelope(
            payload=payload,
            metadata=Metadata.model_validate({"source": "embedded_example"}, strict=True),
        ),
        identity,
    )
    result = await runner.run(envelope, identity=identity)
    return to_execution_result(result)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--telemetry", action="store_true", help="enable optional safe OTel providers"
    )
    args = parser.parse_args()
    payload: dict[str, JsonValue] = {"requestId": "req-001", "priority": "urgent"}
    if args.telemetry:
        from telemetry_setup import telemetry_observer

        async with telemetry_observer() as observer:
            result = await run_example(payload, observer=observer)
    else:
        result = await run_example(payload)
    print(
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ServiceError as error:
        print(
            json.dumps({"error": {"code": error.code.value, "message": str(error)}}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
