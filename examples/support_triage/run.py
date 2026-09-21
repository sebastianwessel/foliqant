"""Run support triage with the explicitly configured local Qwen model."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from examples.local_qwen import local_qwen_profiles
from foliqant.adapters.models import ModelExecutor
from foliqant.adapters.models.providers import open_model_bindings
from foliqant.adapters.telemetry.logging import configure_logging
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.compiler import compile_workflow
from foliqant.contracts.envelope import Envelope, Metadata, accept_envelope
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.contracts.models import ModelProfiles
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity
from foliqant.core.json import JsonValue
from foliqant.core.plan import WorkflowPlan
from foliqant.core.runner import ExecutionLimits, WorkflowRunner
from foliqant.ports.execution import StepExecutor

EXAMPLE_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = EXAMPLE_DIRECTORY.parents[1]
DEMO_PAYLOAD: dict[str, JsonValue] = {
    "requestId": "support-2026-0042",
    "message": (
        "Please cancel renewal for account C-1049 by 30 September 2026. "
        "Send written confirmation to this email address."
    ),
}


def compile_example(model_aliases: Mapping[str, str]) -> WorkflowPlan:
    """Compile this bundle against exact configured model IDs."""

    return compile_workflow(
        EXAMPLE_DIRECTORY,
        model_aliases=model_aliases,
        tool_catalogs={},
        handler_names=set(),
    )


async def run_example(
    payload: dict[str, JsonValue],
    *,
    plan: WorkflowPlan,
    executor: StepExecutor,
    limits: ExecutionLimits | None = None,
) -> ExecutionResult:
    """Run one in-memory request; the caller owns the model executor."""

    runner = WorkflowRunner(
        plan,
        executor=executor,
        validator=WorkflowSchemas(plan),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        limits=limits or ExecutionLimits(),
    )
    return await _run_with_runner(runner, payload)


async def _run_with_runner(
    runner: WorkflowRunner, payload: dict[str, JsonValue]
) -> ExecutionResult:
    identity = Identity(tenant_id="example_org", principal_id="support_agent")
    envelope = accept_envelope(
        Envelope(
            payload=payload,
            metadata=Metadata.model_validate({"source": "support_example"}, strict=True),
        ),
        identity,
    )
    return to_execution_result(await runner.run(envelope, identity=identity))


@asynccontextmanager
async def open_configured(
    *, environment: Mapping[str, str] = os.environ
) -> AsyncIterator[Callable[[dict[str, JsonValue]], Awaitable[ExecutionResult]]]:
    """Own one configured model client and shared workflow runner."""

    profiles: ModelProfiles = local_qwen_profiles(REPOSITORY_ROOT, environment)
    aliases = {alias: profile.model for alias, profile in profiles.models.items()}
    plan = compile_example(aliases)
    schemas = WorkflowSchemas(plan)
    logging_runtime = configure_logging()
    failed = False
    try:
        async with open_model_bindings(profiles, environment=environment) as bindings:
            executor = ModelExecutor(bindings, schemas)
            timeout = profiles.models["local_qwen"].request_timeout
            runner = WorkflowRunner(
                plan,
                executor=executor,
                validator=schemas,
                admission=CapacityLimiter(concurrency=1, queue_limit=0),
                limits=ExecutionLimits(
                    run_timeout=timeout * 2 + 10,
                    model_timeout=timeout,
                ),
            )
            yield lambda payload: _run_with_runner(runner, payload)
    except BaseException:
        failed = True
        raise
    finally:
        clean = await asyncio.to_thread(logging_runtime.close, timeout=1.0)
        if not clean and not failed:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)


async def run_configured(
    payload: dict[str, JsonValue], *, environment: Mapping[str, str] = os.environ
) -> ExecutionResult:
    """Run with the local Qwen settings shared with model curation."""

    async with open_configured(environment=environment) as run:
        return await run(payload)


def _write_error(error: ServiceError) -> int:
    print(
        json.dumps({"error": {"code": error.code, "message": str(error)}}),
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="call the exact local model configured in the repository .env",
    )
    arguments = parser.parse_args()
    if not arguments.live:
        parser.print_help()
        return 0
    try:
        result = asyncio.run(run_configured(DEMO_PAYLOAD))
    except KeyboardInterrupt:
        return 130
    except ServiceError as error:
        return _write_error(error)
    except Exception:
        return _write_error(ServiceError(ErrorCode.DEPENDENCY_FAILURE))
    if result.execution.status == "failed":
        failure = result.execution.error
        assert failure is not None
        return _write_error(ServiceError(ErrorCode(failure.code)))
    print(json.dumps(result.model_dump(mode="json", by_alias=True), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
