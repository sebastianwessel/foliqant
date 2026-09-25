"""Check paired synthetic security wiring, or opt into sequential local inference."""

import argparse
import asyncio
from pathlib import Path
from typing import Literal, cast

from examples.common import (
    command,
    evaluation_output,
    example_environment,
    private_output_path,
    read_example_dataset,
    write_example_dataset,
)
from examples.security_evaluation import offline
from foliqant import (
    Envelope,
    ExecutionResult,
    RuntimePlugins,
    open_application,
    prepare_application,
)
from foliqant.core.errors import TIMEOUT_CODES, ErrorCode, ServiceError
from foliqant.core.json import JsonValue
from foliqant.evaluation import EvaluationVariant, evaluate
from foliqant.evaluation.dataset import EvaluationDataset, metric_specs, validate_targets

CONFIG_PATH = Path(__file__).with_name("config") / "settings.yaml"
Selection = Literal["all", "workflow", "flow", "assess", "confirm"]
SCOPES = ("workflow", "flow", "assess", "confirm")


DATASET_PATH = Path(__file__).with_name("evaluation") / "dataset.json"


def dataset() -> EvaluationDataset:
    """Load editable canonical synthetic gold; no inference creates expectations."""
    return read_example_dataset(DATASET_PATH)


async def run_evaluations(
    *,
    live: bool = False,
    scope: Selection = "workflow",
    repeat: int = 1,
    config: Path = CONFIG_PATH,
    output: Path | None = None,
) -> dict[str, JsonValue]:
    """Run the shared evaluator; each live invocation is serialized and bounded.

    After a timeout, remaining cases produce counted errors without new model
    requests. Starting a new command still requires checking backend health.
    """
    if scope not in {"all", *SCOPES} or type(repeat) is not int or not 1 <= repeat <= 10:
        raise ValueError("Invalid scope or repeat count")
    gold = dataset()
    prepared = prepare_application(config)
    validate_targets(gold, prepared)
    selected = tuple(
        spec for spec in gold.suites if scope == "all" or spec.name == f"prompt_security_{scope}"
    )
    output = private_output_path(output)
    environment = example_environment() if live else offline.ENVIRONMENT
    if live and (not environment.get("MODEL_ID") or not environment.get("MODEL_BASE_URL")):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    plugins = RuntimePlugins() if live else RuntimePlugins(model_factory=offline.model_factory)
    reports = []
    halted = False
    async with open_application(prepared, environment=environment, plugins=plugins) as app:
        for spec in selected:

            async def run(
                envelope: Envelope,
                flow: str | None = spec.flow,
                step: str | None = spec.step,
            ) -> ExecutionResult:
                nonlocal halted
                if halted:
                    raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
                try:
                    if flow is None:
                        result = await app.run("prompt_security", envelope)
                    elif step is None:
                        result = await app.run_flow("prompt_security", flow, envelope)
                    else:
                        result = await app.run_step("prompt_security", flow, step, envelope)
                except ServiceError as error:
                    if error.code in TIMEOUT_CODES:
                        halted = True
                    raise
                except asyncio.CancelledError:
                    halted = True
                    raise
                if (
                    result.execution.error is not None
                    and result.execution.error.code in TIMEOUT_CODES
                ):
                    halted = True
                return result

            reports.append(
                await evaluate(
                    gold.to_suite(spec),
                    EvaluationVariant(
                        "local_model" if live else "offline_fixture",
                        environment["MODEL_ID"],
                        run,
                        prepared.configuration_digest,
                        workflow=spec.workflow,
                        flow=spec.flow,
                        step=spec.step,
                    ),
                    include_details=True,
                    metrics=metric_specs(spec),
                    max_concurrency=1,
                    timeout=620,
                    repeat=repeat,
                )
            )
    result = await evaluation_output(
        reports, mode="live_model" if live else "offline_wiring", dataset=gold, output=output
    )
    workflow_cases = gold.to_suite(gold.suites[0]).cases
    families = {
        family
        for case in workflow_cases
        if isinstance(family := case.envelope().metadata.model_dump().get("family"), str)
    }
    result.update(
        {
            "families": len(families),
            "paired_inputs": len(workflow_cases),
            "evidence_kind": "authored_synthetic_not_human_adjudicated",
            "halted_after_timeout": halted,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="call the configured local model sequentially"
    )
    parser.add_argument("--scope", choices=("all", *SCOPES), default="workflow")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="repeat each case 1–10 times without changing its gold",
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--write-dataset", type=Path)
    args = parser.parse_args()
    if args.write_dataset is not None:
        return command(lambda: write_example_dataset(dataset(), args.write_dataset))
    return command(
        lambda: run_evaluations(
            live=args.live,
            scope=cast(Selection, args.scope),
            repeat=args.repeat,
            config=args.config,
            output=args.output,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
