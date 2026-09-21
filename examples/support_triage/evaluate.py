"""Evaluate a whole pipeline and its model steps against authored expectations."""

import argparse
import os
from pathlib import Path

from examples.common import (
    command,
    evaluation_output,
    example_environment,
    private_output_path,
    suite_document,
    write_example_dataset,
)
from examples.support_triage import offline
from examples.support_triage.run import CONFIG_PATH, open_example
from foliqant import Envelope, ExecutionResult, RuntimePlugins, prepare_application
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
    """Independent expected values for two explicit requests and one unclear request."""
    return EvaluationSuite(
        name="support_triage",
        revision="2",
        cases=(
            EvaluationCase(
                "explicit_cancellation",
                Envelope(
                    payload={
                        "requestId": "eval-001",
                        "message": "Cancel renewal for account C-1049 by 30 September 2026.",
                    }
                ),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation(
                        "queue", "/decisions/classify/result/answer/optionId", "cancellation"
                    ),
                    Expectation("account", "/payload/account_reference", "C-1049"),
                    Expectation("deadline", "/payload/deadline", "30 September 2026"),
                ),
            ),
            EvaluationCase(
                "billing_dispute",
                Envelope(
                    payload={
                        "requestId": "eval-002",
                        "message": "I dispute invoice INV-882. Please review the duplicate charge.",
                    }
                ),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation(
                        "queue", "/decisions/classify/result/answer/optionId", "billing_dispute"
                    ),
                    Expectation("not_an_account", "/payload/account_reference", None),
                    Expectation("deadline_absent", "/payload/deadline", None),
                ),
            ),
            EvaluationCase(
                "insufficient_information",
                Envelope(payload={"requestId": "eval-003", "message": "Please help."}),
                (
                    Expectation("review", "/execution/status", "needs_review"),
                    Expectation(
                        "undetermined",
                        "/decisions/classify/result/answerability/status",
                        "undetermined",
                    ),
                    Expectation("no_answer", "/decisions/classify/result/answer", None),
                    Expectation("safe_output", "/payload/status", "needs_review"),
                ),
            ),
        ),
    )


def _gold_step_suite(step: str) -> EvaluationSuite:
    """Use resolved inputs and explicit per-step gold; never run upstream steps."""
    cases = []
    for case in _gold_suite().cases:
        if step == "extract" and case.id == "insufficient_information":
            continue  # Extraction is intentionally skipped on this pipeline branch.
        payload = case.envelope().payload
        assert isinstance(payload, dict)
        checks = tuple(
            check
            for check in case.expectations
            if (
                check.path.startswith("/decisions/classify/")
                if step == "classify"
                else check.path.startswith("/payload/")
            )
        )
        cases.append(
            EvaluationCase(case.id, Envelope(payload={"message": payload["message"]}), checks)
        )
    return EvaluationSuite(name=f"support_{step}", revision="2", cases=tuple(cases))


def dataset() -> EvaluationDataset:
    """Validate one reusable dataset for pipeline and explicitly isolated steps."""
    queue: dict[str, JsonValue] = {
        "name": "support_queue",
        "path": "/decisions/classify/result/answer/optionId",
        "kind": "classification",
        "labels": ["cancellation", "billing_dispute", "service_change"],
    }
    status: dict[str, JsonValue] = {
        "name": "execution_status",
        "path": "/execution/status",
        "kind": "classification",
        "labels": ["completed", "needs_review", "failed"],
    }
    return EvaluationDataset.model_validate(
        {
            "version": 1,
            "name": "support_triage_examples",
            "revision": "2",
            "suites": [
                suite_document(_gold_suite(), workflow="support_triage", metrics=[queue, status]),
                suite_document(
                    _gold_step_suite("classify"),
                    workflow="support_triage",
                    step="classify",
                    metrics=[queue],
                ),
                suite_document(
                    _gold_step_suite("extract"), workflow="support_triage", step="extract"
                ),
            ],
        },
        strict=True,
    )


def suite() -> EvaluationSuite:
    gold = dataset()
    return gold.to_suite(gold.suites[0])


def step_suite(step: str) -> EvaluationSuite:
    gold = dataset()
    spec = next(spec for spec in gold.suites if spec.step == step)
    return gold.to_suite(spec)


async def run_evaluations(
    *, live: bool = False, output: Path | None = None
) -> dict[str, JsonValue]:
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

            async def invoke(envelope: Envelope, selected: str | None = step) -> ExecutionResult:
                if selected is None:
                    return await app.run("support_triage", envelope)
                return await app.run_step("support_triage", selected, envelope)

            reports.append(
                await evaluate(
                    suite() if step is None else step_suite(step),
                    EvaluationVariant(
                        name="local_qwen" if live else "scripted_wiring",
                        revision=environment["FOLIQANT_CURATION_MODEL"],
                        configuration_revision=prepared.configuration_digest,
                        workflow="support_triage",
                        run=invoke,
                        step=step,
                    ),
                    include_details=True,
                    metrics=metric_specs(spec),
                    max_concurrency=1,
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
    parser.add_argument(
        "--write-dataset", type=Path, help="export synthetic gold without inference"
    )
    args = parser.parse_args()
    if args.write_dataset is not None:
        return command(lambda: write_example_dataset(dataset(), args.write_dataset))
    return command(lambda: run_evaluations(live=args.live, output=args.output))


if __name__ == "__main__":
    raise SystemExit(main())
