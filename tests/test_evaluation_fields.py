"""Null-aware per-field measurement of structured outputs (`fields` metrics)."""

import pytest
from test_evaluation import result, variant

from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import to_execution_result
from foliqant.core.errors import ErrorCode
from foliqant.core.execution import Failure, RunResult, Usage
from foliqant.core.json import freeze_json
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    Expectation,
    MetricSpec,
    evaluate,
)
from foliqant.evaluation.dataset import EvaluationDataset
from foliqant.evaluation.metrics import field_path, validate_metrics

FIELDS = ("client", "identifier", "deadline")
METRIC = MetricSpec("extraction", "/payload/fields", "fields", FIELDS)


def field_gold(**gold: object) -> tuple[Expectation, ...]:
    return tuple(
        Expectation(
            name,
            field_path("/payload/fields", name),
            value,
            "text" if name == "client" and value is not None else "exact",
            absent_as_null=True,
        )
        for name, value in gold.items()
    )


def cases(*gold: tuple[Expectation, ...]) -> EvaluationSuite:
    return EvaluationSuite(
        "fields",
        "1",
        tuple(
            EvaluationCase(str(index), Envelope(payload={"index": index}), checks)
            for index, checks in enumerate(gold)
        ),
    )


async def test_field_outcomes_separate_correct_nulls_hallucinations_and_misses() -> None:
    outputs = [
        # client matches after normalisation; identifier correctly absent (not found)
        {"fields": {"client": " ACME  ag", "deadline": "2026-06-30"}},
        # identifier invented where null expected; deadline missed; client wrong
        {"fields": {"client": "Other", "identifier": "DE0001"}},
        # an explicit null is a null
        {"fields": {"client": "ACME AG", "identifier": None, "deadline": None}},
        None,  # the run fails: every labelled field is unavailable, never a correct null
        {"other": 1},  # no field object at all: absent fields are nulls
    ]
    gold = cases(
        field_gold(client="Acme AG", identifier=None, deadline="2026-06-30"),
        field_gold(client="Acme AG", identifier=None, deadline="2026-06-30"),
        field_gold(identifier=None, deadline=None),
        field_gold(client="Acme AG", identifier=None),
        field_gold(identifier=None),
        (Expectation("unrelated", "/payload/other", 1),),
    )

    async def run(envelope):
        index = envelope.payload["index"]
        if index == 3:
            return to_execution_result(
                RunResult(
                    "failed",
                    "inbox",
                    "r1",
                    "failed",
                    freeze_json({}),
                    {},
                    (),
                    Usage(),
                    Failure(ErrorCode.RUN_TIMEOUT),
                )
            )
        return result(outputs[index] if index < len(outputs) else {"other": 1})

    report = await evaluate(gold, variant(run), metrics=(METRIC,))
    metric = report.metrics[0]
    assert (metric.support, metric.excluded, metric.observed, metric.errors) == (5, 1, 4, 1)
    assert metric.correct == 3  # attempts whose every gold field is correct
    by_field = {item.label: item for item in metric.per_field}
    client, identifier, deadline = (by_field[name] for name in FIELDS)
    assert (client.correct_value, client.wrong_value, client.unavailable) == (1, 1, 1)
    assert (identifier.null_support, identifier.correct_null, identifier.hallucinated) == (5, 3, 1)
    assert identifier.unavailable == 1
    assert identifier.hallucination_rate == 1 / 5
    assert (deadline.value_support, deadline.correct_value, deadline.missed) == (2, 1, 1)
    assert deadline.miss_rate == 1 / 2
    totals = metric.field_totals
    assert totals is not None and (totals.support, totals.unavailable) == (11, 2)
    assert metric.field_accuracy == 6 / 11
    assert metric.macro_field_accuracy == pytest.approx((1 / 3 + 3 / 5 + 2 / 3) / 3)
    # The per-field assertions agree with the metric outcomes.
    assert [check.outcome for check in report.cases[0].checks] == ["passed"] * 3
    exported = report.to_dict()["metrics"][0]
    assert exported["field_accuracy"] == metric.field_accuracy
    assert exported["per_field"][1]["hallucinated"] == 1


async def test_skipped_owner_makes_fields_unavailable() -> None:
    gold = EvaluationSuite(
        "skip",
        "1",
        (
            EvaluationCase(
                "s",
                Envelope(payload={}),
                (
                    Expectation(
                        "value",
                        "/flows/main/steps/unused/result/value",
                        None,
                        absent_as_null=True,
                    ),
                ),
            ),
        ),
    )

    async def run(envelope):
        return result({})

    metric = (
        await evaluate(
            gold,
            variant(run),
            metrics=(MetricSpec("m", "/flows/main/steps/unused/result", "fields", ("value",)),),
        )
    ).metrics[0]
    assert (metric.skipped, metric.observed, metric.correct) == (1, 0, 0)
    assert metric.per_field[0].unavailable == 1


@pytest.mark.parametrize(
    "checks",
    [
        (
            Expectation("a", "/payload/fields/client", "x"),
            Expectation("b", "/payload/fields/client", "y"),
        ),
        (Expectation("a", "/payload/fields/client", ["x"], "set", each="/v"),),
        (Expectation("a", "/payload/fields/client", "x", "custom", "scorer"),),
    ],
)
def test_fields_gold_must_be_one_builtin_unprojected_assertion(checks) -> None:
    with pytest.raises(ValueError, match="fields metric"):
        validate_metrics(cases(checks), (METRIC,))


@pytest.mark.parametrize("kwargs", [{"labels": ("a", None)}, {"each": "/v"}, {"expectation": "a"}])
def test_fields_metric_specification_limits(kwargs) -> None:
    values = {"labels": ("a",), **kwargs}
    with pytest.raises(ValueError):
        spec = MetricSpec("m", "/payload", "fields", values.pop("labels"), **values)
        validate_metrics(cases(), (spec,))


def test_field_names_are_escaped_as_pointer_tokens() -> None:
    assert field_path("/payload", "a/b~c") == "/payload/a~1b~0c"


def test_dataset_boundary_declares_fields_metrics_with_matching_gold() -> None:
    document = {
        "name": "d",
        "revision": "1",
        "suites": [
            {
                "name": "s",
                "workflow": "w",
                "cases": [
                    {
                        "id": "a",
                        "input": {"payload": {}},
                        "expectations": [
                            {
                                "name": "client",
                                "path": "/payload/fields/client",
                                "expected": None,
                                "absent_as_null": True,
                            }
                        ],
                    }
                ],
                "metrics": [
                    {
                        "name": "extraction",
                        "path": "/payload/fields",
                        "kind": "fields",
                        "labels": ["client", "deadline"],
                    }
                ],
            }
        ],
    }
    dataset = EvaluationDataset.model_validate(document, strict=True)
    assert dataset.suites[0].metrics[0].spec().kind == "fields"
    document["suites"][0]["metrics"][0]["labels"] = ["deadline"]
    with pytest.raises(ValueError, match="matching gold"):
        EvaluationDataset.model_validate(document, strict=True)
