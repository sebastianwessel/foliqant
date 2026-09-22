"""Source-span gold permits valid boundaries without weakening extractive checks."""

from typing import cast

import pytest

from foliqant import Envelope
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.core.execution import RunResult, Usage
from foliqant.core.json import FrozenJson, JsonValue, freeze_json
from foliqant.evaluation import EvaluationCase, EvaluationSuite, EvaluationVariant, Expectation
from foliqant.evaluation.dataset import EvaluationDataset
from foliqant.evaluation.runner import evaluate
from foliqant.evaluation.spans import matches_source_span, source_span


def _gold(source: str, required: str, allowed: str) -> FrozenJson:
    allowed_start = source.index(allowed)
    required_start = source.index(required, allowed_start, allowed_start + len(allowed))
    return freeze_json(
        {
            "input_path": "/payload/message",
            "required": [required_start, required_start + len(required)],
            "allowed": [allowed_start, allowed_start + len(allowed)],
        }
    )


def _input(source: object) -> FrozenJson:
    return freeze_json({"payload": {"message": source}, "metadata": {}})


def _result(action: object) -> ExecutionResult:
    return to_execution_result(
        RunResult(
            "span-test",
            "extract",
            "revision",
            "completed",
            freeze_json({"requested_action": action}),
            {},
            (),
            Usage(),
        )
    )


def test_source_span_accepts_legitimate_boundaries_and_unicode_code_points() -> None:
    source = "Bitte ändern Sie den Tarif für Konto Ä-7 bis morgen."
    expected = _gold(
        source,
        "ändern Sie den Tarif",
        "ändern Sie den Tarif für Konto Ä-7",
    )
    case_input = _input(source)
    assert matches_source_span("ändern Sie den Tarif", expected, case_input)
    assert matches_source_span("ändern Sie den Tarif für Konto Ä-7", expected, case_input)
    assert not matches_source_span("ändern Sie", expected, case_input)
    assert not matches_source_span("change the plan", expected, case_input)
    assert not matches_source_span("den Tarif für Konto Ä-7", expected, case_input)
    assert not matches_source_span(
        "ändern Sie den Tarif für Konto Ä-7 bis morgen", expected, case_input
    )
    assert not matches_source_span("Ändern Sie den Tarif", expected, case_input)
    assert not matches_source_span("", expected, case_input)
    assert not matches_source_span(7, expected, case_input)


def test_repeated_candidate_must_have_an_occurrence_containing_the_required_range() -> None:
    source = "add support later; correction: add support to account A-7"
    expected = _gold(source, "add support", "add support to account A-7")
    assert matches_source_span("add support", expected, _input(source))
    assert matches_source_span("add support to account A-7", expected, _input(source))
    assert not matches_source_span("add support later", expected, _input(source))


@pytest.mark.parametrize(
    "expected",
    [
        None,
        {},
        {"input_path": "/payload/message", "required": (0, 1), "allowed": (0, 1), "x": 1},
        {"input_path": "payload/message", "required": (0, 1), "allowed": (0, 1)},
        {"input_path": "/bad~escape", "required": (0, 1), "allowed": (0, 1)},
        {"input_path": "/payload/message", "required": (False, 1), "allowed": (0, 1)},
        {"input_path": "/payload/message", "required": (0.0, 1), "allowed": (0, 1)},
        {"input_path": "/payload/message", "required": (0,), "allowed": (0, 1)},
        {"input_path": "/payload/message", "required": (-1, 1), "allowed": (0, 1)},
        {"input_path": "/payload/message", "required": (1, 1), "allowed": (0, 1)},
        {"input_path": "/payload/message", "required": (0, 2), "allowed": (0, 1)},
        {"input_path": "/payload/message", "required": (1, 2), "allowed": (2, 3)},
    ],
)
def test_source_span_rejects_invalid_closed_shapes(expected: object) -> None:
    with pytest.raises(ValueError):
        source_span(freeze_json(expected))


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, {"input_path": "/payload/message", "required": (0, 1), "allowed": (0, 1)}),
        (
            {"message": 7},
            {"input_path": "/payload/message", "required": (0, 1), "allowed": (0, 1)},
        ),
        (
            {"message": "short"},
            {
                "input_path": "/payload/message",
                "required": (0, 1),
                "allowed": (0, 10**100),
            },
        ),
    ],
)
def test_case_construction_rejects_missing_nonstring_or_overflow_sources(
    payload: JsonValue, expected: object
) -> None:
    check = Expectation(
        "action",
        "/payload/requested_action",
        cast(FrozenJson, expected),
        "source_span",
    )
    with pytest.raises(ValueError):
        EvaluationCase("case", Envelope(payload=payload), (check,))


async def test_runner_scores_source_span_without_a_model_or_custom_scorer() -> None:
    source = "Please add priority support to account A-2205 by Friday."
    expected = _gold(
        source,
        "add priority support",
        "add priority support to account A-2205",
    )
    suite = EvaluationSuite(
        "spans",
        "v1",
        (
            EvaluationCase(
                "case",
                Envelope(payload={"message": source}),
                (
                    Expectation(
                        "action",
                        "/payload/requested_action",
                        expected,
                        "source_span",
                    ),
                ),
            ),
        ),
    )

    async def valid(envelope: Envelope) -> ExecutionResult:
        return _result("add priority support to account A-2205")

    async def truncated(envelope: Envelope) -> ExecutionResult:
        return _result("priority support to account A-2205")

    valid_variant = EvaluationVariant("span", "v1", valid, "configuration-v1")
    truncated_variant = EvaluationVariant("span", "v1", truncated, "configuration-v1")
    assert (await evaluate(suite, valid_variant)).checks.passed == 1
    failed = await evaluate(suite, truncated_variant)
    assert failed.checks.failed == 1
    assert failed.cases[0].checks[0].reason_code == "mismatch"


def test_dataset_accepts_source_span_and_validates_it_against_case_input() -> None:
    source = "Cancel renewal for account C-1."
    required_start = source.index("Cancel renewal")
    dataset = EvaluationDataset.model_validate(
        {
            "name": "span-gold",
            "revision": "v1",
            "suites": [
                {
                    "name": "pipeline",
                    "workflow": "support",
                    "cases": [
                        {
                            "id": "case",
                            "input": {"payload": {"message": source}},
                            "expectations": [
                                {
                                    "name": "action",
                                    "path": "/payload/requested_action",
                                    "comparison": "source_span",
                                    "expected": {
                                        "input_path": "/payload/message",
                                        "required": [required_start, required_start + 14],
                                        "allowed": [required_start, len(source) - 1],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        strict=True,
    )
    check = dataset.to_suite(dataset.suites[0]).cases[0].expectations[0]
    assert check.comparison == "source_span"
