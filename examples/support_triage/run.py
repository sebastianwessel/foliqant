"""Run support triage through the public application lifecycle."""

import argparse
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from examples.common import command, example_environment
from foliqant import (
    Envelope,
    ExecutionResult,
    PreparedApplication,
    RuntimePlugins,
    WorkflowApplication,
    open_application,
    prepare_application,
)
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import JsonValue

EXAMPLE_DIRECTORY = Path(__file__).resolve().parent
CONFIG_PATH = EXAMPLE_DIRECTORY / "foliqant.yaml"
DEMO_PAYLOAD: dict[str, JsonValue] = {
    "requestId": "support-2026-0042",
    "message": (
        "Cancel renewal for account C-1049 by 30 September 2026. "
        "Send written confirmation to this email address."
    ),
}


@asynccontextmanager
async def open_example(
    *,
    environment: Mapping[str, str] | None = None,
    plugins: RuntimePlugins | None = None,
    prepared: PreparedApplication | None = None,
) -> AsyncIterator[WorkflowApplication]:
    """Compile offline, then own one shared application's clients and cleanup."""
    prepared = prepared or prepare_application(CONFIG_PATH)
    async with open_application(
        prepared,
        environment=example_environment() if environment is None else environment,
        plugins=plugins,
    ) as application:
        yield application


@asynccontextmanager
async def open_configured() -> AsyncIterator[Callable[[Envelope], Awaitable[ExecutionResult]]]:
    """Expose the public envelope call to the thin HTTP example."""
    async with open_example() as application:

        async def run(envelope: Envelope) -> ExecutionResult:
            return await application.run("support_triage", envelope)

        yield run


async def run_configured() -> dict[str, JsonValue]:
    async with open_example() as application:
        result = await application.run("support_triage", Envelope(payload=DEMO_PAYLOAD))
        if result.execution.status == "failed":
            assert result.execution.error is not None
            raise ServiceError(ErrorCode(result.execution.error.code))
        return cast(dict[str, JsonValue], result.model_dump(mode="json"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call the configured local model")
    if not parser.parse_args().live:
        parser.print_help()
        return 0
    return command(run_configured)


if __name__ == "__main__":
    raise SystemExit(main())
