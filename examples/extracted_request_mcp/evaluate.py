"""Evaluate extraction-to-MCP wiring for English and German requests."""

import argparse
from pathlib import Path

from examples.common import (
    command,
    evaluation_output,
    private_output_path,
    suite_document,
    write_example_dataset,
)
from examples.extracted_request_mcp.run import CONFIG_PATH, open_example, runtime_environment
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

_CASES: tuple[dict[str, str], ...] = (
    {
        "id": "english_request",
        "message": "Check the status of public request FOI-2026-0142 in English.",
        "language": "en",
        "email": "private-en@example.test",
        "reference": "FOI-2026-0142",
        "summary": "English request-status lookup.",
        "due_date": "2026-10-05",
    },
    {
        "id": "german_request",
        "message": "Bitte Status für Antrag FOI-2026-0310 auf Deutsch prüfen.",
        "language": "de",
        "email": "private-de@example.test",
        "reference": "FOI-2026-0310",
        "summary": "Deutsche Statusabfrage.",
        "due_date": "05.10.2026",
    },
)


def _lookup_expectations(case: dict[str, str]) -> tuple[Expectation, ...]:
    return (
        Expectation("completed", "/execution/status", "completed"),
        Expectation("reference", "/payload/reference", case["reference"]),
        Expectation("language", "/payload/language", case["language"]),
        Expectation("status", "/payload/status", "in_review"),
        Expectation("due_date", "/payload/due_date", case["due_date"]),
    )


def _pipeline_suite() -> EvaluationSuite:
    return EvaluationSuite(
        "extracted_request_lookup",
        "1",
        tuple(
            EvaluationCase(
                case["id"],
                Envelope(
                    payload={
                        "message": case["message"],
                        "language": case["language"],
                        "contact_email": case["email"],
                    }
                ),
                _lookup_expectations(case)
                + (
                    Expectation(
                        "internal_summary",
                        "/decisions/extract/result/internal_summary",
                        case["summary"],
                    ),
                    Expectation("one_model", "/execution/usage/model_requests", 1),
                    Expectation("one_tool", "/execution/usage/tool_calls", 1),
                ),
            )
            for case in _CASES
        ),
    )


def _extract_suite() -> EvaluationSuite:
    return EvaluationSuite(
        "extracted_request_extract",
        "1",
        tuple(
            EvaluationCase(
                case["id"],
                Envelope(payload={"message": case["message"], "language": case["language"]}),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation("reference", "/payload/reference", case["reference"]),
                    Expectation("language", "/payload/language", case["language"]),
                    Expectation("internal_summary", "/payload/internal_summary", case["summary"]),
                    Expectation("one_model", "/execution/usage/model_requests", 1),
                    Expectation("no_tool", "/execution/usage/tool_calls", 0),
                ),
            )
            for case in _CASES
        ),
    )


def _lookup_suite() -> EvaluationSuite:
    return EvaluationSuite(
        "extracted_request_lookup_step",
        "1",
        tuple(
            EvaluationCase(
                case["id"],
                Envelope(payload={"reference": case["reference"], "language": case["language"]}),
                _lookup_expectations(case)
                + (
                    Expectation("no_model", "/execution/usage/model_requests", 0),
                    Expectation("one_tool", "/execution/usage/tool_calls", 1),
                ),
            )
            for case in _CASES
        ),
    )


def dataset() -> EvaluationDataset:
    """Return authored EN/DE gold for the pipeline and both isolated operations."""

    return EvaluationDataset.model_validate(
        {
            "version": 1,
            "name": "extracted_request_mcp_examples",
            "revision": "1",
            "suites": [
                suite_document(_pipeline_suite(), workflow="extracted_request_lookup"),
                suite_document(
                    _extract_suite(), workflow="extracted_request_lookup", step="extract"
                ),
                suite_document(_lookup_suite(), workflow="extracted_request_lookup", step="lookup"),
            ],
        },
        strict=True,
    )


async def run_evaluations(
    *, live: bool = False, output: Path | None = None, repeat: int = 1
) -> dict[str, JsonValue]:
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    output = private_output_path(output)
    gold = dataset()
    prepared = prepare_application(CONFIG_PATH)
    environment = runtime_environment(live=live)
    async with open_example(live=live, environment=environment, prepared=prepared) as app:
        reports = []
        for spec in gold.suites:
            step = spec.step

            async def invoke(envelope: Envelope, selected: str | None = step) -> ExecutionResult:
                if selected is None:
                    return await app.run("extracted_request_lookup", envelope)
                return await app.run_step("extracted_request_lookup", selected, envelope)

            reports.append(
                await evaluate(
                    gold.to_suite(spec),
                    EvaluationVariant(
                        name="local_qwen" if live else "scripted_wiring",
                        revision=environment["FOLIQANT_CURATION_MODEL"],
                        configuration_revision=prepared.configuration_digest,
                        workflow="extracted_request_lookup",
                        run=invoke,
                        step=step,
                    ),
                    include_details=True,
                    metrics=metric_specs(spec),
                    max_concurrency=1,
                    repeat=repeat,
                    timeout=620 if live else 15,
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
    parser.add_argument(
        "--live", action="store_true", help="measure configured local Qwen extraction"
    )
    parser.add_argument("--output", type=Path, help="write a private full report artifact")
    parser.add_argument("--repeat", type=int, default=1, help="attempts per authored case")
    parser.add_argument(
        "--write-dataset", type=Path, help="export synthetic gold without running clients"
    )
    args = parser.parse_args()
    if args.write_dataset is not None:
        return command(lambda: write_example_dataset(dataset(), args.write_dataset))
    return command(lambda: run_evaluations(live=args.live, output=args.output, repeat=args.repeat))


if __name__ == "__main__":
    raise SystemExit(main())
