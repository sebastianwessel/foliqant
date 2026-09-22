"""Check synthetic gold offline, or measure typed evidence decisions with local Qwen."""

import argparse
from pathlib import Path

from examples.common import (
    command,
    evaluation_output,
    example_environment,
    private_output_path,
    read_example_dataset,
    write_example_dataset,
)
from foliqant import Envelope, ExecutionResult, open_application, prepare_application
from foliqant.core.json import FrozenJson, JsonValue
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    RegisteredScorer,
    evaluate,
)
from foliqant.evaluation.dataset import EvaluationDataset, SuiteSpec, metric_specs, validate_targets

CONFIG_PATH = Path(__file__).with_name("config") / "settings.yaml"


QUESTION_IDS = ("triage", "labels", "dispute", "priority", "requests")
DATASET_PATH = Path(__file__).with_name("evaluation") / "dataset.json"


def dataset(*, validation: bool = False) -> EvaluationDataset:
    """Read development gold or the separate held-out synthetic JSON dataset."""
    selected = DATASET_PATH.with_name("validation.json") if validation else DATASET_PATH
    return read_example_dataset(selected)


async def _unit_count(actual: FrozenJson, expected: FrozenJson) -> bool:
    return isinstance(actual, tuple) and type(expected) is int and len(actual) == expected


SCORERS = (RegisteredScorer("request_unit_count", "1", _unit_count),)


def build_suite(gold: EvaluationDataset, spec: SuiteSpec) -> EvaluationSuite:
    """Add Python-only unit-count gold with the existing custom-scorer API.

    JSON datasets intentionally contain no executable scorer registration. The
    exported positional checks remain usable with the normal evaluation CLI.
    """
    suite = gold.to_suite(spec)
    if spec.workflow != "decision_evidence":
        return suite
    cases = []
    for case in suite.cases:
        checks = case.expectations
        unit_prefix = "/flows/assessment/steps/assess/result/results/4/answer/units/"
        positions = {
            check.path.removeprefix(unit_prefix).split("/", 1)[0]
            for check in checks
            if check.path.startswith(unit_prefix)
        }
        if positions:
            checks += (
                Expectation(
                    "request_unit_count",
                    unit_prefix.rstrip("/"),
                    len(positions),
                    "custom",
                    "request_unit_count",
                ),
            )
        cases.append(EvaluationCase(case.id, case.envelope(), checks))
    return EvaluationSuite(suite.name, suite.revision, tuple(cases))


async def run_evaluations(
    *, live: bool = False, validation: bool = False, output: Path | None = None
) -> dict[str, JsonValue]:
    gold = dataset(validation=validation)
    prepared = prepare_application(CONFIG_PATH)
    validate_targets(gold, prepared)
    suites = tuple(build_suite(gold, spec) for spec in gold.suites)
    if not live:
        return {
            "ok": True,
            "mode": "offline_check",
            "cases": sum(len(suite.cases) for suite in suites),
        }
    output = private_output_path(output)
    environment = example_environment()
    reports = []
    async with open_application(prepared, environment=environment) as app:
        for spec, suite in zip(gold.suites, suites, strict=True):

            async def run(
                envelope: Envelope,
                workflow: str = spec.workflow,
                flow: str | None = spec.flow,
                step: str | None = spec.step,
            ) -> ExecutionResult:
                if flow is None:
                    return await app.run(workflow, envelope)
                if step is None:
                    return await app.run_flow(workflow, flow, envelope)
                return await app.run_step(workflow, flow, step, envelope)

            reports.append(
                await evaluate(
                    suite,
                    EvaluationVariant(
                        "local_qwen",
                        environment["MODEL_ID"],
                        run,
                        prepared.configuration_digest,
                        workflow=spec.workflow,
                        flow=spec.flow,
                        step=spec.step,
                    ),
                    scorers=SCORERS,
                    include_details=True,
                    metrics=metric_specs(spec),
                    max_concurrency=1,
                    timeout=620,
                )
            )
    return await evaluation_output(reports, mode="live_model", dataset=gold, output=output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call local Qwen sequentially")
    parser.add_argument(
        "--validation", action="store_true", help="use reserved validation families"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--write-dataset", type=Path)
    args = parser.parse_args()
    if args.write_dataset is not None:
        return command(
            lambda: write_example_dataset(dataset(validation=args.validation), args.write_dataset)
        )
    return command(
        lambda: run_evaluations(live=args.live, validation=args.validation, output=args.output)
    )


if __name__ == "__main__":
    raise SystemExit(main())
