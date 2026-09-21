"""Evaluate the real local MCP workflow and its isolated lookup step."""

import argparse
from pathlib import Path

from examples.common import (
    command,
    evaluation_output,
    private_output_path,
    suite_document,
    write_example_dataset,
)
from examples.public_request_mcp.run import CONFIG_PATH, open_example
from foliqant import Envelope, ExecutionResult, prepare_application
from foliqant.core.json import JsonValue
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    evaluate,
)
from foliqant.evaluation.dataset import EvaluationDataset, metric_specs


def _gold_suite() -> EvaluationSuite:
    return EvaluationSuite(
        name="public_request_lookup",
        revision="1",
        cases=tuple(
            EvaluationCase(
                f"request_{index}",
                Envelope(payload={"reference": reference}),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation("reference", "/payload/reference", reference),
                    Expectation("status", "/payload/status", "in_review"),
                    Expectation("deadline", "/payload/due_date", "2026-10-05"),
                    Expectation("team", "/payload/assigned_team", "records_review"),
                    Expectation("one_tool", "/execution/usage/tool_calls", 1),
                    Expectation("no_model", "/execution/usage/model_requests", 0),
                ),
            )
            for index, reference in enumerate(("FOI-2026-0142", "FOI-2026-0310"))
        ),
    )


def dataset() -> EvaluationDataset:
    """Reuse the same authored expectations for pipeline and isolated lookup."""
    gold = _gold_suite()
    isolated = EvaluationSuite("public_request_lookup_step", gold.revision, gold.cases)
    return EvaluationDataset.model_validate(
        {
            "version": 1,
            "name": "public_request_mcp_examples",
            "revision": "1",
            "suites": [
                suite_document(gold, workflow="public_request_lookup"),
                suite_document(isolated, workflow="public_request_lookup", step="lookup"),
            ],
        },
        strict=True,
    )


def suite() -> EvaluationSuite:
    gold = dataset()
    return gold.to_suite(gold.suites[0])


async def run_evaluations(*, output: Path | None = None, repeat: int = 1) -> dict[str, JsonValue]:
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    output = private_output_path(output)
    gold = dataset()
    prepared = prepare_application(CONFIG_PATH)
    async with open_example(prepared) as app:
        reports = []
        for spec in gold.suites:
            isolated = spec.step is not None

            async def invoke(envelope: Envelope, step_only: bool = isolated) -> ExecutionResult:
                if step_only:
                    return await app.run_step("public_request_lookup", "lookup", envelope)
                return await app.run("public_request_lookup", envelope)

            reports.append(
                await evaluate(
                    gold.to_suite(spec),
                    EvaluationVariant(
                        name="lookup_step" if isolated else "lookup_pipeline",
                        revision="1",
                        configuration_revision=prepared.configuration_digest,
                        workflow="public_request_lookup",
                        run=invoke,
                        step=spec.step,
                    ),
                    include_details=True,
                    metrics=metric_specs(spec),
                    repeat=repeat,
                    timeout=10,
                )
            )
    return await evaluation_output(reports, mode="local_stdio", dataset=gold, output=output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write a private full report artifact")
    parser.add_argument("--repeat", type=int, default=1, help="attempts per authored case")
    parser.add_argument(
        "--write-dataset", type=Path, help="export synthetic gold without tool calls"
    )
    args = parser.parse_args()
    if args.write_dataset is not None:
        return command(lambda: write_example_dataset(dataset(), args.write_dataset))
    return command(lambda: run_evaluations(output=args.output, repeat=args.repeat))


if __name__ == "__main__":
    raise SystemExit(main())
