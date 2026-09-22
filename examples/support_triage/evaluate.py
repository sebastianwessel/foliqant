"""Evaluate a whole pipeline and its model steps against authored expectations."""

import argparse
import os
from pathlib import Path

from examples.common import (
    command,
    evaluation_output,
    example_environment,
    private_output_path,
    read_example_dataset,
    write_example_dataset,
)
from examples.support_triage import offline
from examples.support_triage.run import CONFIG_PATH, open_example
from foliqant import Envelope, ExecutionResult, RuntimePlugins, prepare_application
from foliqant.core.json import JsonValue
from foliqant.evaluation import (
    EvaluationSuite,
    EvaluationVariant,
    evaluate,
)
from foliqant.evaluation.dataset import EvaluationDataset, metric_specs

DATASET_PATH = Path(__file__).with_name("evaluation") / "dataset.json"


def dataset() -> EvaluationDataset:
    """Read the editable, canonical synthetic evaluation dataset."""
    return read_example_dataset(DATASET_PATH)


def suite() -> EvaluationSuite:
    gold = dataset()
    return gold.to_suite(gold.suites[0])


def step_suite(step: str) -> EvaluationSuite:
    gold = dataset()
    spec = next(spec for spec in gold.suites if spec.step == step)
    return gold.to_suite(spec)


async def run_evaluations(
    *, live: bool = False, output: Path | None = None, repeat: int = 1
) -> dict[str, JsonValue]:
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    output = private_output_path(output)
    gold = dataset()
    prepared = prepare_application(CONFIG_PATH)
    environment = example_environment(os.environ) if live else offline.ENVIRONMENT
    async with open_example(
        environment=environment,
        plugins=None if live else RuntimePlugins(model_factory=offline.model_factory),
        prepared=prepared,
    ) as app:
        reports = []
        for spec in gold.suites:
            step = spec.step
            flow = spec.flow

            async def invoke(
                envelope: Envelope, selected: str | None = step, selected_flow: str | None = flow
            ) -> ExecutionResult:
                if selected_flow is None:
                    return await app.run("support_triage", envelope)
                if selected is None:
                    return await app.run_flow("support_triage", selected_flow, envelope)
                return await app.run_step("support_triage", selected_flow, selected, envelope)

            reports.append(
                await evaluate(
                    (suite() if flow is None else gold.to_suite(spec))
                    if step is None
                    else step_suite(step),
                    EvaluationVariant(
                        name="local_qwen" if live else "scripted_wiring",
                        revision=environment["MODEL_ID"],
                        configuration_revision=prepared.configuration_digest,
                        workflow="support_triage",
                        run=invoke,
                        step=step,
                        flow=flow,
                    ),
                    include_details=True,
                    metrics=metric_specs(spec),
                    max_concurrency=1,
                    repeat=repeat,
                    timeout=620 if live else 10,
                )
            )
    return await evaluation_output(
        reports, mode="live_model" if live else "offline_wiring", dataset=gold, output=output
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="measure the configured local model instead of scripted wiring",
    )
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
