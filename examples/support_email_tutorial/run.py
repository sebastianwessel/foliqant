"""Run the final support email snapshot with scripted or local-model responses."""

import argparse
import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from examples.common import ROOT, command, example_environment
from examples.support_email_tutorial import offline
from examples.support_email_tutorial.handlers import HANDLERS
from examples.support_email_tutorial.multi_policy import HANDLERS as MULTI_HANDLERS
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
    "message": "Please review the duplicate charge on invoice INV-7 for account A-100."
}
MULTI_DEMO_PAYLOAD: dict[str, JsonValue] = {
    "message": "Review invoice INV-7 for account A-100 and cancel renewal for account A-200."
}


def runtime_environment(*, live: bool) -> dict[str, str]:
    environment = dict(example_environment(os.environ) if live else offline.ENVIRONMENT)
    environment.update(
        {
            "FOLIQANT_EXAMPLE_PYTHON": sys.executable,
            "FOLIQANT_EXAMPLE_ROOT": str(ROOT),
        }
    )
    return environment


@asynccontextmanager
async def open_example(
    *,
    live: bool = False,
    environment: Mapping[str, str] | None = None,
    prepared: PreparedApplication | None = None,
) -> AsyncIterator[WorkflowApplication]:
    prepared = prepared or prepare_application(CONFIG_PATH, handlers=HANDLERS | MULTI_HANDLERS)
    selected = runtime_environment(live=live) if environment is None else environment
    plugins = None if live else RuntimePlugins(model_factory=offline.model_factory)
    async with open_application(prepared, environment=selected, plugins=plugins) as app:
        yield app


async def run_example(payload: dict[str, JsonValue], *, live: bool = False) -> ExecutionResult:
    async with open_example(live=live) as app:
        return await app.run("support_email", Envelope(payload=payload))


async def run_multi_example(
    payload: dict[str, JsonValue], *, live: bool = False
) -> ExecutionResult:
    async with open_example(live=live) as app:
        return await app.run("support_multi", Envelope(payload=payload))


async def run_demo(
    *, live: bool = False, agent: bool = False, multi: bool = False
) -> dict[str, JsonValue]:
    if multi:
        result = await run_multi_example(MULTI_DEMO_PAYLOAD, live=live)
    elif agent:
        async with open_example(live=live) as app:
            result = await app.run(
                "agent_reply",
                Envelope(payload={**DEMO_PAYLOAD, "account_reference": "A-100"}),
            )
    else:
        result = await run_example(DEMO_PAYLOAD, live=live)
    output = cast(dict[str, JsonValue], result.model_dump(mode="json"))
    output["ok"] = result.execution.status == "completed"
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call the configured local model")
    parser.add_argument("--agent", action="store_true", help="run the bounded model tool loop")
    parser.add_argument(
        "--multi", action="store_true", help="run bounded multi-request preparation"
    )
    args = parser.parse_args()
    return command(lambda: run_demo(live=args.live, agent=args.agent, multi=args.multi))


if __name__ == "__main__":
    raise SystemExit(main())
