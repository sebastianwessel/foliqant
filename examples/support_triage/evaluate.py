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
from foliqant.contracts.envelope import Metadata
from foliqant.core.json import JsonValue
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    evaluate,
)
from foliqant.evaluation.dataset import EvaluationDataset, metric_specs

_MESSAGES = {
    "explicit_cancellation": "Cancel renewal for account C-1049 by 30 September 2026.",
    "billing_dispute": "I dispute invoice INV-882. Please review the duplicate charge.",
    "insufficient_information": "Please help.",
    "out_of_catalog": "I would like to apply for the advertised accountant position.",
    "german_out_of_catalog": (
        "Ich möchte mich auf die ausgeschriebene Stelle als Buchhalter bewerben."
    ),
    "german_insufficient_information": "Bitte helfen Sie mir.",
    "service_change": "Add priority support to account A-2205 by 15 October 2026.",
    "german_cancellation": (
        "Bitte stoppen Sie die automatische Verlängerung für Kundenkonto "
        "K-771 bis 31. Dezember 2026."
    ),
    "german_billing_dispute": (
        "Ich widerspreche der doppelten Belastung auf Rechnung RE-550. "
        "Bitte prüfen Sie die doppelte Belastung bis 15. Oktober 2026."
    ),
    "multiple_active_intents": (
        "Please cancel renewal for account C-3300 and add priority support "
        "to the same account. Both requests are current."
    ),
    "unresolved_contradiction": (
        "Please cancel renewal for account C-4400. Please also keep the "
        "renewal active. Neither instruction supersedes the other."
    ),
    "explicit_correction": (
        "Earlier I asked to cancel renewal for account A-3100. Correction: "
        "do not cancel it. Please add priority support to account A-3100 "
        "by 1 November 2026."
    ),
}


def _action(case_id: str, required: str, allowed: str) -> Expectation:
    """Author an extractive action core and its legitimate source boundaries."""
    source = _MESSAGES[case_id]
    allowed_start = source.index(allowed)
    required_start = source.index(required, allowed_start, allowed_start + len(allowed))
    return Expectation(
        "action",
        "/payload/requested_action",
        {
            "input_path": "/payload/message",
            "required": (required_start, required_start + len(required)),
            "allowed": (allowed_start, allowed_start + len(allowed)),
        },
        "source_span",
    )


def _gold_suite() -> EvaluationSuite:
    """Independent gold for clear, incomplete, competing, and corrected requests."""
    original = EvaluationSuite(
        name="support_triage",
        revision="7",
        cases=(
            EvaluationCase(
                "explicit_cancellation",
                Envelope(
                    payload={
                        "requestId": "eval-001",
                        "message": _MESSAGES["explicit_cancellation"],
                    },
                    metadata=Metadata.model_validate({"language": "en"}),
                ),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation("language", "/metadata/language", "en"),
                    Expectation(
                        "queue", "/decisions/classify/result/answer/optionId", "cancellation"
                    ),
                    Expectation(
                        "evidence_source",
                        "/decisions/classify/result/explanation/evidence/0/sourceId",
                        "message",
                    ),
                    _action(
                        "explicit_cancellation",
                        "Cancel renewal",
                        "Cancel renewal for account C-1049",
                    ),
                    Expectation("account", "/payload/account_reference", "C-1049"),
                    Expectation("deadline", "/payload/deadline", "by 30 September 2026"),
                ),
            ),
            EvaluationCase(
                "billing_dispute",
                Envelope(
                    payload={
                        "requestId": "eval-002",
                        "message": _MESSAGES["billing_dispute"],
                    },
                    metadata=Metadata.model_validate({"language": "en"}),
                ),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation("language", "/metadata/language", "en"),
                    Expectation(
                        "queue", "/decisions/classify/result/answer/optionId", "billing_dispute"
                    ),
                    Expectation(
                        "evidence_source",
                        "/decisions/classify/result/explanation/evidence/0/sourceId",
                        "message",
                    ),
                    _action(
                        "billing_dispute",
                        "review the duplicate charge",
                        "Please review the duplicate charge",
                    ),
                    Expectation("not_an_account", "/payload/account_reference", None),
                    Expectation("deadline_absent", "/payload/deadline", None),
                ),
            ),
            EvaluationCase(
                "insufficient_information",
                Envelope(
                    payload={
                        "requestId": "eval-003",
                        "message": _MESSAGES["insufficient_information"],
                    },
                    metadata=Metadata.model_validate({"language": "en"}),
                ),
                (
                    Expectation("review", "/execution/status", "needs_review"),
                    Expectation("language", "/metadata/language", "en"),
                    Expectation(
                        "not_answerable",
                        "/decisions/classify/result/answerability/status",
                        "not_answerable",
                    ),
                    Expectation(
                        "no_supported_answer",
                        "/decisions/classify/result/answerability/issues",
                        ("no_supported_answer",),
                        comparison="set",
                    ),
                    Expectation("no_answer", "/decisions/classify/result/answer", None),
                    Expectation("safe_output", "/payload/status", "needs_review"),
                ),
            ),
            EvaluationCase(
                "service_change",
                Envelope(
                    payload={
                        "requestId": "eval-004",
                        "message": _MESSAGES["service_change"],
                    },
                    metadata=Metadata.model_validate({"language": "en"}),
                ),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation("language", "/metadata/language", "en"),
                    Expectation(
                        "queue", "/decisions/classify/result/answer/optionId", "service_change"
                    ),
                    Expectation(
                        "evidence_source",
                        "/decisions/classify/result/explanation/evidence/0/sourceId",
                        "message",
                    ),
                    _action(
                        "service_change",
                        "Add priority support",
                        "Add priority support to account A-2205",
                    ),
                    Expectation("account", "/payload/account_reference", "A-2205"),
                    Expectation("deadline", "/payload/deadline", "by 15 October 2026"),
                ),
            ),
            EvaluationCase(
                "german_cancellation",
                Envelope(
                    payload={
                        "requestId": "eval-005",
                        "message": _MESSAGES["german_cancellation"],
                    },
                    metadata=Metadata.model_validate({"language": "de"}),
                ),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation("language", "/metadata/language", "de"),
                    Expectation(
                        "queue", "/decisions/classify/result/answer/optionId", "cancellation"
                    ),
                    Expectation(
                        "evidence_source",
                        "/decisions/classify/result/explanation/evidence/0/sourceId",
                        "message",
                    ),
                    _action(
                        "german_cancellation",
                        "stoppen Sie die automatische Verlängerung",
                        "stoppen Sie die automatische Verlängerung für Kundenkonto K-771",
                    ),
                    Expectation("account", "/payload/account_reference", "K-771"),
                    Expectation("deadline", "/payload/deadline", "bis 31. Dezember 2026"),
                ),
            ),
            EvaluationCase(
                "german_billing_dispute",
                Envelope(
                    payload={
                        "requestId": "eval-006",
                        "message": _MESSAGES["german_billing_dispute"],
                    },
                    metadata=Metadata.model_validate({"language": "de"}),
                ),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation("language", "/metadata/language", "de"),
                    Expectation(
                        "queue", "/decisions/classify/result/answer/optionId", "billing_dispute"
                    ),
                    Expectation(
                        "evidence_source",
                        "/decisions/classify/result/explanation/evidence/0/sourceId",
                        "message",
                    ),
                    _action(
                        "german_billing_dispute",
                        "prüfen Sie die doppelte Belastung",
                        "Bitte prüfen Sie die doppelte Belastung",
                    ),
                    Expectation("not_an_account", "/payload/account_reference", None),
                    Expectation("deadline", "/payload/deadline", "bis 15. Oktober 2026"),
                ),
            ),
            EvaluationCase(
                "multiple_active_intents",
                Envelope(
                    payload={
                        "requestId": "eval-007",
                        "message": _MESSAGES["multiple_active_intents"],
                    },
                    metadata=Metadata.model_validate({"language": "en"}),
                ),
                (
                    Expectation("review", "/execution/status", "needs_review"),
                    Expectation("language", "/metadata/language", "en"),
                    Expectation(
                        "not_answerable",
                        "/decisions/classify/result/answerability/status",
                        "not_answerable",
                    ),
                    Expectation(
                        "multiple_valid_options",
                        "/decisions/classify/result/answerability/issues",
                        ("multiple_valid_options",),
                        comparison="set",
                    ),
                    Expectation("no_answer", "/decisions/classify/result/answer", None),
                    Expectation("safe_output", "/payload/status", "needs_review"),
                ),
            ),
            EvaluationCase(
                "unresolved_contradiction",
                Envelope(
                    payload={
                        "requestId": "eval-008",
                        "message": _MESSAGES["unresolved_contradiction"],
                    },
                    metadata=Metadata.model_validate({"language": "en"}),
                ),
                (
                    Expectation("review", "/execution/status", "needs_review"),
                    Expectation("language", "/metadata/language", "en"),
                    Expectation(
                        "not_answerable",
                        "/decisions/classify/result/answerability/status",
                        "not_answerable",
                    ),
                    Expectation(
                        "conflicting_information",
                        "/decisions/classify/result/answerability/issues",
                        ("conflicting_information",),
                        comparison="set",
                    ),
                    Expectation("no_answer", "/decisions/classify/result/answer", None),
                    Expectation("safe_output", "/payload/status", "needs_review"),
                ),
            ),
            EvaluationCase(
                "explicit_correction",
                Envelope(
                    payload={
                        "requestId": "eval-009",
                        "message": _MESSAGES["explicit_correction"],
                    },
                    metadata=Metadata.model_validate({"language": "en"}),
                ),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation("language", "/metadata/language", "en"),
                    Expectation(
                        "queue", "/decisions/classify/result/answer/optionId", "service_change"
                    ),
                    Expectation(
                        "evidence_source",
                        "/decisions/classify/result/explanation/evidence/0/sourceId",
                        "message",
                    ),
                    _action(
                        "explicit_correction",
                        "add priority support",
                        "Please add priority support to account A-3100",
                    ),
                    Expectation("account", "/payload/account_reference", "A-3100"),
                    Expectation("deadline", "/payload/deadline", "by 1 November 2026"),
                ),
            ),
        ),
    )

    extra = (
        _unresolved_case("out_of_catalog", "eval-010", "en"),
        _unresolved_case("german_out_of_catalog", "eval-011", "de"),
        _unresolved_case("german_insufficient_information", "eval-012", "de"),
    )
    return EvaluationSuite(
        original.name,
        original.revision,
        tuple(_with_selection_gold(case) for case in (*original.cases, *extra)),
    )


def _unresolved_case(case_id: str, request_id: str, language: str) -> EvaluationCase:
    """Author a distinct unsupported source without deriving gold from predictions."""
    return EvaluationCase(
        case_id,
        Envelope(
            payload={"requestId": request_id, "message": _MESSAGES[case_id]},
            metadata=Metadata.model_validate({"language": language}),
        ),
        (
            Expectation("review", "/execution/status", "needs_review"),
            Expectation("language", "/metadata/language", language),
            Expectation(
                "not_answerable",
                "/decisions/classify/result/answerability/status",
                "not_answerable",
            ),
            Expectation(
                "no_supported_answer",
                "/decisions/classify/result/answerability/issues",
                ("no_supported_answer",),
                "set",
            ),
            Expectation("no_answer", "/decisions/classify/result/answer", None),
            Expectation("safe_output", "/payload/status", "needs_review"),
        ),
    )


def _with_selection_gold(case: EvaluationCase) -> EvaluationCase:
    """Declare policy expectations separately from the native classification gold."""
    category = next(
        (
            check.expected
            for check in case.expectations
            if check.path == "/decisions/classify/result/answer/optionId"
        ),
        None,
    )
    issues = next(
        (
            check.expected
            for check in case.expectations
            if check.path == "/decisions/classify/result/answerability/issues"
        ),
        (),
    )
    extra: tuple[Expectation, ...] = ()
    if category is not None:
        extra = (
            Expectation("selected_category", "/decisions/classify/selection/category/id", category),
            Expectation("selection_origin", "/decisions/classify/selection/origin", "model"),
            Expectation("no_issues", "/decisions/classify/result/answerability/issues", (), "set"),
        )
    elif issues == ("no_supported_answer",):
        extra = (
            Expectation("selected_category", "/decisions/classify/selection/category/id", "misc"),
            Expectation("selection_origin", "/decisions/classify/selection/origin", "fallback"),
            Expectation("unresolved_route", "/decisions/review/status", "needs_review"),
        )
    else:
        extra = (Expectation("unresolved_route", "/decisions/review/status", "needs_review"),)
    return EvaluationCase(case.id, case.envelope(), case.expectations + extra)


def _gold_step_suite(step: str) -> EvaluationSuite:
    """Use resolved inputs and explicit per-step gold; never run upstream steps."""
    cases = []
    for case in _gold_suite().cases:
        status = next(
            check.expected for check in case.expectations if check.path == "/execution/status"
        )
        if step == "extract" and status == "needs_review":
            continue  # Extraction is intentionally skipped on every review branch.
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
            EvaluationCase(
                case.id,
                Envelope(
                    payload={"message": payload["message"]}, metadata=case.envelope().metadata
                ),
                checks,
            )
        )
    return EvaluationSuite(name=f"support_{step}", revision="7", cases=tuple(cases))


def dataset() -> EvaluationDataset:
    """Validate one reusable dataset for pipeline and explicitly isolated steps."""
    queue: dict[str, JsonValue] = {
        "name": "support_queue",
        "path": "/decisions/classify/result/answer/optionId",
        "kind": "classification",
        "labels": ["cancellation", "billing_dispute", "service_change"],
    }
    selection: dict[str, JsonValue] = {
        "name": "effective_category",
        "path": "/decisions/classify/selection/category/id",
        "kind": "classification",
        "labels": ["cancellation", "billing_dispute", "service_change", "misc"],
    }
    origin: dict[str, JsonValue] = {
        "name": "selection_origin",
        "path": "/decisions/classify/selection/origin",
        "kind": "classification",
        "labels": ["model", "fallback"],
    }
    issues: dict[str, JsonValue] = {
        "name": "answerability_issues",
        "path": "/decisions/classify/result/answerability/issues",
        "kind": "multilabel",
        "labels": [
            "no_supported_answer",
            "conflicting_information",
            "multiple_valid_options",
        ],
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
            "revision": "7",
            "suites": [
                suite_document(
                    _gold_suite(),
                    workflow="support_triage",
                    metrics=[queue, status, selection, origin, issues],
                ),
                suite_document(
                    _gold_step_suite("classify"),
                    workflow="support_triage",
                    step="classify",
                    metrics=[queue, selection, origin, issues],
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
