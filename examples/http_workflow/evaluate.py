"""Evaluate the thin HTTP boundary against the same support-triage golden cases."""

import argparse
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2

from examples.common import (
    command,
    evaluation_output,
    example_environment,
    private_output_path,
    read_example_dataset,
    write_example_dataset,
)
from examples.http_workflow.server import SupportRun, create_app
from examples.support_triage import offline
from examples.support_triage.run import CONFIG_PATH, open_example
from foliqant import Envelope, ExecutionResult, RuntimePlugins, prepare_application
from foliqant.core.json import JsonValue
from foliqant.evaluation import EvaluationVariant, evaluate
from foliqant.evaluation.dataset import EvaluationDataset, metric_specs

DATASET_PATH = Path(__file__).with_name("evaluation") / "dataset.json"


def dataset() -> EvaluationDataset:
    """Read the editable, canonical synthetic evaluation dataset."""
    return read_example_dataset(DATASET_PATH)


async def run_evaluations(
    *, live: bool = False, output: Path | None = None, repeat: int = 1
) -> dict[str, JsonValue]:
    """Exercise ASGI without a listening socket; only --live calls the model."""
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    output = private_output_path(output)
    gold = dataset()
    spec = gold.suites[0]
    prepared = prepare_application(CONFIG_PATH)
    environment = example_environment(os.environ) if live else offline.ENVIRONMENT

    @asynccontextmanager
    async def context() -> AsyncIterator[SupportRun]:
        async with open_example(
            environment=environment,
            plugins=None if live else RuntimePlugins(model_factory=offline.model_factory),
            prepared=prepared,
        ) as application:

            async def run(envelope: Envelope) -> ExecutionResult:
                return await application.run("support_triage", envelope)

            yield run

    app = create_app(context)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://example"
        ) as client:

            async def invoke(envelope: Envelope) -> ExecutionResult:
                response = await client.post("/run", json=envelope.model_dump(mode="json"))
                response.raise_for_status()
                return ExecutionResult.model_validate(response.json(), strict=True)

            report = await evaluate(
                gold.to_suite(spec),
                EvaluationVariant(
                    name="http_local_qwen" if live else "http_scripted_wiring",
                    revision=environment["MODEL_ID"],
                    configuration_revision=prepared.configuration_digest,
                    workflow="support_triage",
                    run=invoke,
                ),
                include_details=True,
                metrics=metric_specs(spec),
                repeat=repeat,
                timeout=620 if live else 10,
            )
    return await evaluation_output(
        (report,), mode="live_model" if live else "offline_asgi", dataset=gold, output=output
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="use the configured local model")
    parser.add_argument("--output", type=Path, help="write a private full report artifact")
    parser.add_argument("--repeat", type=int, default=1, help="attempts per authored case")
    parser.add_argument(
        "--write-dataset", type=Path, help="export synthetic gold without inference"
    )
    args = parser.parse_args()
    if args.write_dataset is not None:
        return command(lambda: write_example_dataset(dataset(), args.write_dataset))
    return command(lambda: run_evaluations(live=args.live, output=args.output, repeat=args.repeat))


if __name__ == "__main__":
    raise SystemExit(main())
