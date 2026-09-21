"""Run a public-record lookup using the public application lifecycle."""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from examples.common import ROOT, command
from foliqant import (
    Envelope,
    PreparedApplication,
    WorkflowApplication,
    open_application,
    prepare_application,
)
from foliqant.contracts.execution import ExecutionResult
from foliqant.core.json import JsonValue

CONFIG_PATH = Path(__file__).with_name("foliqant.yaml")


@asynccontextmanager
async def open_example(
    prepared: PreparedApplication | None = None,
) -> AsyncIterator[WorkflowApplication]:
    """Own the real local MCP client; this example uses no model or network server."""
    prepared = prepared or prepare_application(CONFIG_PATH)
    async with open_application(
        prepared,
        environment={
            "FOLIQANT_EXAMPLE_PYTHON": sys.executable,
            "FOLIQANT_EXAMPLE_ROOT": str(ROOT),
        },
    ) as app:
        yield app


async def run_example(payload: dict[str, JsonValue]) -> ExecutionResult:
    async with open_example() as app:
        return await app.run("public_request_lookup", Envelope(payload=payload))


async def run_demo() -> dict[str, JsonValue]:
    result = await run_example({"reference": "FOI-2026-0142"})
    output = cast(dict[str, JsonValue], result.model_dump(mode="json"))
    output["ok"] = result.execution.status == "completed"
    return output


if __name__ == "__main__":
    raise SystemExit(command(run_demo))
