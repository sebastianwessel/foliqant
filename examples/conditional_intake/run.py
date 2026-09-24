"""Run the conditional intake workflow with scripted or local-model responses."""

import argparse
import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from examples.common import ROOT, command, example_environment
from examples.conditional_intake import offline
from examples.conditional_intake.handlers import HANDLERS
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
    "message": "Please review invoice INV-9 for account A-120; my previous account was A-100."
}


def runtime_environment(*, live: bool) -> dict[str, str]:
    """Select model settings explicitly and always confine the stdio child."""
    environment = dict(example_environment(os.environ) if live else offline.ENVIRONMENT)
    environment.update(
        {"FOLIQANT_EXAMPLE_PYTHON": sys.executable, "FOLIQANT_EXAMPLE_ROOT": str(ROOT)}
    )
    return environment


@asynccontextmanager
async def open_example(
    *,
    live: bool = False,
    environment: Mapping[str, str] | None = None,
    prepared: PreparedApplication | None = None,
) -> AsyncIterator[WorkflowApplication]:
    """Register the declared handlers and open the scripted or configured model."""
    prepared = prepared or prepare_application(CONFIG_PATH, handlers=HANDLERS)
    selected = runtime_environment(live=live) if environment is None else environment
    plugins = None if live else RuntimePlugins(model_factory=offline.model_factory)
    async with open_application(prepared, environment=selected, plugins=plugins) as app:
        yield app


async def run_example(payload: dict[str, JsonValue], *, live: bool = False) -> ExecutionResult:
    async with open_example(live=live) as app:
        return await app.run("account_intake", Envelope(payload=payload))


async def run_demo(*, live: bool = False) -> dict[str, JsonValue]:
    result = await run_example(DEMO_PAYLOAD, live=live)
    output = cast(dict[str, JsonValue], result.model_dump(mode="json"))
    output["ok"] = result.execution.status == "completed"
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call the configured local model")
    args = parser.parse_args()
    return command(lambda: run_demo(live=args.live))


if __name__ == "__main__":
    raise SystemExit(main())
