"""Run the minimal decision through the public application lifecycle."""

import argparse
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from examples.common import command, example_environment
from examples.decision_basics import offline
from foliqant import (
    Envelope,
    ExecutionResult,
    PreparedApplication,
    RuntimePlugins,
    WorkflowApplication,
    open_application,
    prepare_application,
)
from foliqant.core.json import JsonValue

CONFIG_PATH = Path(__file__).with_name("config") / "settings.yaml"
DEMO_PAYLOAD: dict[str, JsonValue] = {
    "message": "Please send a copy of invoice INV-42.",
}


@asynccontextmanager
async def open_example(
    *,
    live: bool = False,
    environment: Mapping[str, str] | None = None,
    prepared: PreparedApplication | None = None,
) -> AsyncIterator[WorkflowApplication]:
    """Compile once and open either the scripted or configured model."""
    prepared = prepared or prepare_application(CONFIG_PATH)
    selected = (
        (example_environment() if live else offline.ENVIRONMENT)
        if environment is None
        else environment
    )
    plugins = None if live else RuntimePlugins(model_factory=offline.model_factory)
    async with open_application(prepared, environment=selected, plugins=plugins) as app:
        yield app


async def run_example(payload: dict[str, JsonValue], *, live: bool = False) -> ExecutionResult:
    async with open_example(live=live) as app:
        return await app.run("decision_basics", Envelope(payload=payload))


async def run_demo(*, live: bool = False) -> dict[str, JsonValue]:
    result = await run_example(DEMO_PAYLOAD, live=live)
    output = cast(dict[str, JsonValue], result.model_dump(mode="json"))
    output["ok"] = result.execution.status == "completed"
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="use the configured model instead of scripted wiring"
    )
    args = parser.parse_args()
    return command(lambda: run_demo(live=args.live))


if __name__ == "__main__":
    raise SystemExit(main())
