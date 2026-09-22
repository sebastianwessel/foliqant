"""Run synthetic support-email gold with a fake model and real local MCP."""

import argparse
from pathlib import Path

from examples.common import command, evaluation_output, private_output_path, read_example_dataset
from examples.support_email_tutorial.handlers import HANDLERS
from examples.support_email_tutorial.multi_policy import HANDLERS as MULTI_HANDLERS
from examples.support_email_tutorial.run import CONFIG_PATH, open_example
from foliqant import Envelope, ExecutionResult, prepare_application
from foliqant.core.json import JsonValue
from foliqant.evaluation import EvaluationVariant, evaluate
from foliqant.evaluation.dataset import EvaluationDataset, metric_specs

DATASET_PATH = Path(__file__).with_name("evaluation") / "dataset.json"


def dataset() -> EvaluationDataset:
    return read_example_dataset(DATASET_PATH)


async def run_evaluations(
    *, live: bool = False, output: Path | None = None
) -> dict[str, JsonValue]:
    output = private_output_path(output)
    gold = dataset()
    prepared = prepare_application(CONFIG_PATH, handlers=HANDLERS | MULTI_HANDLERS)
    async with open_example(live=live, prepared=prepared) as app:
        reports = []
        for spec in gold.suites:

            async def invoke(envelope: Envelope, workflow: str = spec.workflow) -> ExecutionResult:
                return await app.run(workflow, envelope)

            reports.append(
                await evaluate(
                    gold.to_suite(spec),
                    EvaluationVariant(
                        name="configured_model" if live else "scripted_wiring",
                        revision="1",
                        configuration_revision=prepared.configuration_digest,
                        workflow=spec.workflow,
                        run=invoke,
                    ),
                    include_details=True,
                    metrics=metric_specs(spec),
                    max_concurrency=1,
                    timeout=70 if live else 10,
                )
            )
    return await evaluation_output(
        reports,
        mode="live_model" if live else "offline_wiring",
        dataset=gold,
        output=output,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="use the configured local model")
    parser.add_argument("--output", type=Path, help="write a private report")
    args = parser.parse_args()
    return command(lambda: run_evaluations(live=args.live, output=args.output))


if __name__ == "__main__":
    raise SystemExit(main())
