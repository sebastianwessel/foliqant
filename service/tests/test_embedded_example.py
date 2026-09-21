"""The repository example runs through real offline service components."""

import json
import os
import runpy
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest

from foliqant.contracts.execution import ExecutionResult
from foliqant.core.errors import ErrorCode, ServiceError

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "examples/embedded-workflow/run.py"


def _example() -> tuple[type[object], Callable[..., Awaitable[ExecutionResult]]]:
    namespace = runpy.run_path(str(SCRIPT))
    executor = cast(type[object], namespace["DeterministicExecutor"])
    run_example = cast(Callable[..., Awaitable[ExecutionResult]], namespace["run_example"])
    return executor, run_example


def _tracking_executor(base: type[object]) -> object:
    class TrackingExecutor(base):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(self, step, inputs, context):  # type: ignore[no-untyped-def]
            self.calls.append(step.name)
            return await super().execute(step, inputs, context)

    return TrackingExecutor()


async def test_embedded_example_compiles_routes_and_returns_strict_result() -> None:
    executor_type, run_example = _example()
    executor = _tracking_executor(executor_type)
    result = await run_example(
        {"requestId": "integration-1", "priority": "urgent"}, executor=executor
    )

    assert isinstance(result, ExecutionResult)
    assert result.execution.status == "completed"
    assert result.execution.workflow == "embedded_triage"
    assert result.payload == {
        "requestId": "integration-1",
        "queue": "expedite",
    }
    assert result.metadata.tenant_id == "example_org"
    assert result.metadata.principal_id == "example_user"
    assert result.metadata.__pydantic_extra__ == {"source": "embedded_example"}
    assert result.decisions["route"].status == "completed"
    assert result.decisions["review"].status == "skipped"
    assert result.decisions["render"].status == "completed"
    assert result.decisions["done"].status == "completed"
    assert result.execution.usage.model_requests == 0
    assert result.execution.usage.tool_calls == 0
    assert executor.calls == ["route", "render"]  # type: ignore[attr-defined]
    json.dumps(result.model_dump(mode="json", by_alias=True), allow_nan=False)


async def test_input_schema_rejects_before_deterministic_handler() -> None:
    executor_type, run_example = _example()
    executor = _tracking_executor(executor_type)
    with pytest.raises(ServiceError) as error:
        await run_example(
            {"requestId": "integration-2", "priority": "unsupported"},
            executor=executor,
        )
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert executor.calls == []  # type: ignore[attr-defined]


async def test_missing_business_priority_routes_to_review() -> None:
    executor_type, run_example = _example()
    executor = _tracking_executor(executor_type)
    result = await run_example({"requestId": "integration-3"}, executor=executor)

    assert result.execution.status == "needs_review"
    assert result.payload == {"status": "needs_review"}
    assert result.decisions["route"].status == "needs_review"
    assert result.decisions["review"].status == "needs_review"
    assert result.decisions["render"].status == "skipped"
    assert executor.calls == ["route"]  # type: ignore[attr-defined]


def test_documented_no_sync_command_prints_json_result() -> None:
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "service",
            "--no-sync",
            "python",
            "examples/embedded-workflow/run.py",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = json.loads(completed.stdout)
    assert output["execution"]["status"] == "completed"
    assert output["payload"]["queue"] == "expedite"
    assert output["metadata"]["tenant_id"] == "example_org"
