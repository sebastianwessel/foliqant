"""Check synthetic gold offline, or measure typed evidence decisions with local Qwen."""

import argparse
from pathlib import Path

from examples.common import (
    command,
    evaluation_output,
    example_environment,
    private_output_path,
    write_example_dataset,
)
from foliqant import Envelope, ExecutionResult, open_application, prepare_application
from foliqant.core.json import JsonValue
from foliqant.evaluation import EvaluationVariant, evaluate
from foliqant.evaluation.dataset import EvaluationDataset, metric_specs

CONFIG_PATH = Path(__file__).with_name("foliqant.yaml")

# Authored gold is independent of model responses. The ordinal task explicitly
# permits a qualitative timing estimate; factual extraction would instead abstain.
type Case = tuple[str, str, str, str | None, list[str], str, str, str, str | None]

_CASES: tuple[Case, ...] = (
    (
        "two_requests",
        "en",
        "Freeze my card and send my statement by 5 pm today. I am not disputing any charge.",
        None,
        ["freeze_card", "send_statement"],
        "false",
        "time_sensitive",
        "strong",
        "multiple_valid_options",
    ),
    (
        "no_action",
        "en",
        "No action is requested. This is a routine status note, and I am not disputing any charge.",
        None,
        [],
        "false",
        "routine",
        "strong",
        "no_supported_answer",
    ),
    (
        "relative_timing",
        "en",
        "Please send my statement. I would appreciate it sooner rather than later.",
        "send_statement",
        ["send_statement"],
        "unknown",
        "time_sensitive",
        "limited",
        None,
    ),
    (
        "relative_timing_de",
        "de",
        "Bitte senden Sie mir meinen Kontoauszug. Lieber früher als später.",
        "send_statement",
        ["send_statement"],
        "unknown",
        "time_sensitive",
        "limited",
        None,
    ),
)
_VALIDATION: tuple[Case, ...] = (
    (
        "flexible_dispute",
        "en",
        "Please send my statement whenever convenient; it can wait. I dispute the duplicate fee.",
        "send_statement",
        ["send_statement"],
        "true",
        "routine",
        "strong",
        None,
    ),
    (
        "freeze_dispute_de",
        "de",
        "Bitte sperren Sie meine Karte vor meiner morgigen Abreise um 8 Uhr. "
        "Ich beanstande die doppelte Abbuchung.",
        "freeze_card",
        ["freeze_card"],
        "true",
        "time_sensitive",
        "strong",
        None,
    ),
)


def dataset(*, validation: bool = False) -> EvaluationDataset:
    """Build explicit expectations without reading generated responses."""
    cases: list[JsonValue] = []
    for case_id, language, message, choice, labels, predicate, level, strength, issue in (
        _VALIDATION if validation else _CASES
    ):
        checks: list[JsonValue] = []

        def check(
            name: str,
            path: str,
            expected: JsonValue,
            comparison: str = "exact",
            target: list[JsonValue] = checks,
        ) -> None:
            target.append(
                {"name": name, "path": path, "expected": expected, "comparison": comparison}
            )

        check(
            "review",
            "/execution/status",
            "needs_review" if issue or predicate == "unknown" else "completed",
        )
        label_values: list[JsonValue] = list(labels)
        answers: list[JsonValue] = [
            {"optionId": choice} if choice else None,
            {"optionIds": label_values},
            {"value": predicate},
            {"levelId": level},
        ]
        for index, question_id in enumerate(
            ("triage", "labels", "dispute", "priority", "requests")
        ):
            base = f"/decisions/assess/result/results/{index}"
            check(f"{question_id}_identity", f"{base}/questionId", question_id)
            abstains = (index == 0 and choice is None) or (index == 2 and predicate == "unknown")
            check(
                f"{question_id}_status",
                f"{base}/answerability/status",
                "not_answerable" if abstains else "answerable",
            )
            issues: list[JsonValue] = (
                [issue]
                if index == 0 and issue
                else ["no_supported_answer"]
                if index == 2 and predicate == "unknown"
                else []
            )
            check(f"{question_id}_issues", f"{base}/answerability/issues", issues, "set")
            check(
                f"{question_id}_strength",
                f"{base}/evidence_strength",
                None if abstains else strength if index == 3 else "strong",
            )
            if index < 4:
                if index == 1:
                    check("selected_labels", f"{base}/answer/optionIds", label_values, "set")
                else:
                    check(f"{question_id}_answer", f"{base}/answer", answers[index])
            else:
                check("relations", f"{base}/answer/relations", [])
                if not labels:
                    check("no_requests", f"{base}/answer/units", [])
                for position, label in enumerate(labels):
                    check(
                        f"request_{position}", f"{base}/answer/units/{position}/categoryId", label
                    )
                    check(
                        f"request_{position}_status",
                        f"{base}/answer/units/{position}/status",
                        "active",
                    )
                    check(
                        f"request_{position}_subject",
                        f"{base}/answer/units/{position}/subject",
                        None,
                    )
        cases.append(
            {
                "id": case_id,
                "input": {"payload": {"message": message}, "metadata": {"language": language}},
                "expectations": checks,
            }
        )
    return EvaluationDataset.model_validate(
        {
            "version": 1,
            "name": "decision_evidence_validation" if validation else "decision_evidence",
            "revision": "2",
            "suites": [
                {
                    "name": "typed_support",
                    "workflow": "decision_evidence",
                    "cases": cases,
                    "metrics": [
                        {
                            "name": f"{name}_strength",
                            "path": f"/decisions/assess/result/results/{index}/evidence_strength",
                            "kind": "classification",
                            "labels": ["limited", "strong", None],
                        }
                        for index, name in enumerate(
                            ("triage", "labels", "dispute", "priority", "requests")
                        )
                    ],
                }
            ],
        },
        strict=True,
    )


async def run_evaluations(
    *, live: bool = False, validation: bool = False, output: Path | None = None
) -> dict[str, JsonValue]:
    gold = dataset(validation=validation)
    prepared = prepare_application(CONFIG_PATH)
    spec = gold.suites[0]
    suite = gold.to_suite(spec)
    if not live:
        return {"ok": True, "mode": "offline_check", "cases": len(suite.cases)}
    output = private_output_path(output)
    environment = example_environment()
    async with open_application(prepared, environment=environment) as app:

        async def run(envelope: Envelope) -> ExecutionResult:
            return await app.run("decision_evidence", envelope)

        report = await evaluate(
            suite,
            EvaluationVariant(
                "local_qwen",
                environment["FOLIQANT_CURATION_MODEL"],
                run,
                prepared.configuration_digest,
                workflow="decision_evidence",
            ),
            include_details=True,
            metrics=metric_specs(spec),
            max_concurrency=1,
            timeout=620,
        )
    return await evaluation_output((report,), mode="live_model", dataset=gold, output=output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call local Qwen sequentially")
    parser.add_argument("--validation", action="store_true", help="use separate validation cases")
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
