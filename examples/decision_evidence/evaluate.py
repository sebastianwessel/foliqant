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
from examples.decision_evidence.gold import (
    DEVELOPMENT,
    ISOLATED_PREDICATE_CASES,
    QUESTION_IDS,
    VALIDATION,
    Assessment,
    Case,
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

CONFIG_PATH = Path(__file__).with_name("foliqant.yaml")


def _checks(
    question_id: str, assessment: Assessment, base: str, *, isolated: bool = False
) -> list[JsonValue]:
    checks: list[JsonValue] = []

    def check(name: str, path: str, expected: JsonValue, comparison: str = "exact") -> None:
        checks.append({"name": name, "path": path, "expected": expected, "comparison": comparison})

    check(f"{question_id}_identity", f"{base}/questionId", "assess" if isolated else question_id)
    check(f"{question_id}_status", f"{base}/answerability/status", assessment.status)
    check(f"{question_id}_issues", f"{base}/answerability/issues", list(assessment.issues), "set")
    check(f"{question_id}_strength", f"{base}/evidence_strength", assessment.strength)
    answer = assessment.answer
    if question_id == "labels" and isinstance(answer, dict):
        check("selected_labels", f"{base}/answer/optionIds", answer["optionIds"], "set")
    elif question_id == "requests" and isinstance(answer, dict):
        check("relations", f"{base}/answer/relations", answer["relations"])
        units = answer["units"]
        assert isinstance(units, list)
        if not units:
            check("no_requests", f"{base}/answer/units", [])
        for position, unit in enumerate(units):
            assert isinstance(unit, dict)
            for field in ("categoryId", "subject", "status"):
                check(
                    f"request_{position}_{field}",
                    f"{base}/answer/units/{position}/{field}",
                    unit[field],
                )
    else:
        check(f"{question_id}_answer", f"{base}/answer", answer)
    return checks


def _case_document(case: Case, *, isolated: bool = False) -> JsonValue:
    selected = (
        (("dispute", case.assessments[2]),)
        if isolated
        else tuple(zip(QUESTION_IDS, case.assessments, strict=True))
    )
    checks: list[JsonValue] = [
        {
            "name": "review",
            "path": "/execution/status",
            "expected": "needs_review"
            if any(a.status != "answerable" for _, a in selected)
            else "completed",
            "comparison": "exact",
        }
    ]
    for index, (question_id, assessment) in enumerate(selected):
        base = "/decisions/assess/result" + ("" if isolated else f"/results/{index}")
        checks.extend(_checks(question_id, assessment, base, isolated=isolated))
    return {
        "id": case.id,
        "input": {
            "payload": {"message": case.message},
            "metadata": {"language": case.language, "family": case.family},
        },
        "expectations": checks,
    }


def _metrics(*, isolated: bool = False) -> list[JsonValue]:
    return [
        {
            "name": f"{name}_strength",
            "path": "/decisions/assess/result"
            + ("" if isolated else f"/results/{index}")
            + "/evidence_strength",
            "kind": "classification",
            "labels": ["limited", "strong", None],
        }
        for index, name in enumerate(("dispute",) if isolated else QUESTION_IDS)
    ]


def dataset(*, validation: bool = False) -> EvaluationDataset:
    """Build explicit expectations without reading generated responses.

    The isolated suite repeats two development inputs with the same predicate
    gold. It is a task-mixing diagnostic, not two new independent examples.
    """
    suites: list[JsonValue] = [
        {
            "name": "typed_support",
            "workflow": "decision_evidence",
            "cases": [_case_document(case) for case in (VALIDATION if validation else DEVELOPMENT)],
            "metrics": _metrics(),
        }
    ]
    if not validation:
        suites.append(
            {
                "name": "isolated_predicate",
                "workflow": "decision_predicate",
                "cases": [
                    _case_document(case, isolated=True)
                    for case in DEVELOPMENT
                    if case.id in ISOLATED_PREDICATE_CASES
                ],
                "metrics": _metrics(isolated=True),
            }
        )
    return EvaluationDataset.model_validate(
        {
            "version": 1,
            "name": "decision_evidence_validation" if validation else "decision_evidence",
            "revision": "4",
            "suites": suites,
        },
        strict=True,
    )


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
    authored = {case.id: case for case in (*DEVELOPMENT, *VALIDATION)}
    cases = []
    for case in suite.cases:
        checks = case.expectations
        answer = authored[case.id].assessments[4].answer
        if isinstance(answer, dict):
            units = answer["units"]
            assert isinstance(units, list)
            if units:  # Empty arrays already have an exact JSON expectation.
                checks += (
                    Expectation(
                        "request_unit_count",
                        "/decisions/assess/result/results/4/answer/units",
                        len(units),
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

            async def run(envelope: Envelope, workflow: str = spec.workflow) -> ExecutionResult:
                return await app.run(workflow, envelope)

            reports.append(
                await evaluate(
                    suite,
                    EvaluationVariant(
                        "local_qwen",
                        environment["FOLIQANT_CURATION_MODEL"],
                        run,
                        prepared.configuration_digest,
                        workflow=spec.workflow,
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
