"""Evaluate conditional intake at pipeline and flow scope with a scripted model and real MCP."""

import argparse
from pathlib import Path

from examples.common import command, evaluation_output, private_output_path, read_example_dataset
from examples.conditional_intake.handlers import HANDLERS
from examples.conditional_intake.run import CONFIG_PATH, open_example
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
    prepared = prepare_application(CONFIG_PATH, handlers=HANDLERS)
    async with open_example(live=live, prepared=prepared) as app:
        reports = []
        for spec in gold.suites:

            async def invoke(
                envelope: Envelope,
                flow: str | None = spec.flow,
                step: str | None = spec.step,
            ) -> ExecutionResult:
                if step is not None:
                    return await app.run_step("account_intake", flow or "extract", step, envelope)
                if flow is not None:
                    return await app.run_flow("account_intake", flow, envelope)
                return await app.run("account_intake", envelope)

            reports.append(
                await evaluate(
                    gold.to_suite(spec),
                    EvaluationVariant(
                        name="configured_model" if live else "scripted_wiring",
                        revision="1",
                        configuration_revision=prepared.configuration_digest,
                        workflow="account_intake",
                        flow=spec.flow,
                        step=spec.step,
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
    parser.add_argument("--live", action="store_true", help="use the configured model")
    parser.add_argument("--output", type=Path, help="write a private report")
    args = parser.parse_args()
    return command(lambda: run_evaluations(live=args.live, output=args.output))


if __name__ == "__main__":
    raise SystemExit(main())
