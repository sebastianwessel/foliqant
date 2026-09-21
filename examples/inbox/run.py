"""Run the nondurable model-enabled inbox example."""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from dotenv import dotenv_values
from pydantic import ValidationError

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
from foliqant.core.runner import WorkflowRunner
from foliqant.ports.execution import StepExecutor

EXAMPLE_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = EXAMPLE_DIRECTORY.parents[1]
DEMO_PAYLOAD: dict[str, JsonValue] = {
    "requestId": "inbox-001",
    "message": "I can still sign in, but I need help changing my password.",
}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    raise ValueError


def load_profiles(path: Path) -> ModelProfiles:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        return ModelProfiles.model_validate(raw, strict=True)
    except (OSError, UnicodeError, ValueError, RecursionError, ValidationError):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None


def environment_snapshot() -> Mapping[str, str]:
    from_file = {
        key: value
        for key, value in dotenv_values(REPOSITORY_ROOT / ".env").items()
        if value is not None
    }
    return {**from_file, **os.environ}


def compile_example(model_aliases: Mapping[str, str]) -> WorkflowPlan:
    return compile_workflow(
        EXAMPLE_DIRECTORY, model_aliases=model_aliases, tool_catalogs={}, handler_names=set()
    )


async def run_example(
    payload: dict[str, JsonValue],
    *,
    plan: WorkflowPlan,
    executor: StepExecutor,
) -> ExecutionResult:
    runner = WorkflowRunner(
        plan,
        executor=executor,
        validator=WorkflowSchemas(plan),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
    )
    identity = Identity(tenant_id="example_org", principal_id="example_user")
    envelope = accept_envelope(
        Envelope(payload=payload, metadata=Metadata.model_validate({"source": "inbox_demo"})),
        identity,
    )
    return to_execution_result(await runner.run(envelope, identity=identity))


async def run_configured(payload: dict[str, JsonValue], profiles_path: Path) -> ExecutionResult:
    profiles = load_profiles(profiles_path)
    aliases = {alias: profile.model for alias, profile in profiles.models.items()}
    plan = compile_example(aliases)
    schemas = WorkflowSchemas(plan)
    logging_runtime = configure_logging()
    failed = False
    try:
        async with open_model_bindings(profiles, environment=environment_snapshot()) as bindings:
            return await run_example(payload, plan=plan, executor=ModelExecutor(bindings, schemas))
    except BaseException:
        failed = True
        raise
    finally:
        clean = await asyncio.to_thread(logging_runtime.close, timeout=1.0)
        if not clean and not failed:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)


def _write_error(code: object, message: str) -> int:
    print(json.dumps({"error": {"code": code, "message": message}}), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, help="edited model profiles JSON file")
    arguments = parser.parse_args()
    if arguments.profiles is None:
        parser.print_help()
        return 0
    try:
        result = asyncio.run(run_configured(DEMO_PAYLOAD, cast(Path, arguments.profiles)))
    except KeyboardInterrupt:
        return 130
    except ServiceError as caught:
        return _write_error(caught.code, str(caught))
    except Exception:
        unexpected = ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        return _write_error(unexpected.code, str(unexpected))
    if result.execution.status == "failed":
        failure = result.execution.error
        assert failure is not None
        return _write_error(failure.code, failure.message)
    print(json.dumps(result.model_dump(mode="json", by_alias=True), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
