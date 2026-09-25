"""Text / contains comparisons, array projections and absent-as-null assertions."""

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


def one_case(*checks: Expectation) -> EvaluationSuite:
    return EvaluationSuite(
        "comparisons", "1", (EvaluationCase("c", Envelope(payload={"x": 1}), checks),)
    )


def outcomes(report) -> dict[str, str]:
    return {check.name: check.outcome for check in report.cases[0].checks}


async def test_text_and_contains_normalize_case_and_whitespace_only() -> None:
    async def run(envelope):
        return result(
            {
                "name": "  Stadtwerke   MUSTERSTADT ",
                "text": "The Monthly\nFactsheet was late.",
                "n": 3,
            }
        )

    report = await evaluate(
        one_case(
            Expectation("name", "/payload/name", "stadtwerke musterstadt", "text"),
            Expectation("prefix", "/payload/name", "stadtwerke", "text"),
            Expectation("contains", "/payload/text", "monthly factsheet", "contains"),
            Expectation("absent_fact", "/payload/text", "quarterly", "contains"),
            Expectation("punctuation", "/payload/text", "factsheet was late!", "contains"),
            Expectation("non_string", "/payload/n", "3", "text"),
        ),
        variant(run),
    )
    assert outcomes(report) == {
        "name": "passed",
        "prefix": "failed",
        "contains": "passed",
        "absent_fact": "failed",
        "punctuation": "failed",
        "non_string": "failed",
    }


@pytest.mark.parametrize(
    ("comparison", "expected", "each"),
    [
        ("text", 1, None),
        ("contains", "  ", None),
        ("contains", None, None),
        ("text", "a", "/x"),
        ("source_span", {"input_path": "/payload/x", "required": [0, 1], "allowed": [0, 1]}, "/x"),
        ("exact", ["a"], "x"),
        ("exact", ["a"], ""),
        ("exact", ["a"], "/bad~2"),
    ],
)
def test_invalid_comparison_gold_fails_before_execution(comparison, expected, each) -> None:
    with pytest.raises(ValueError):
        Expectation("bad", "/payload", expected, comparison, each=each)


async def test_each_projects_array_items_for_exact_set_and_metrics() -> None:
    plans = [
        {"intents": [{"intent": "new", "s": "1"}, {"intent": "incident", "s": "2"}]},
        {"intents": [{"intent": "new"}, {"intent": "new"}]},
        {"intents": [{"other": "x"}]},
        {"intents": "not an array"},
    ]
    gold = [["incident", "new"], ["new"], ["new"], ["new"]]
    suite = EvaluationSuite(
        "projection",
        "1",
        tuple(
            EvaluationCase(
                str(index),
                Envelope(payload={"index": index}),
                (
                    Expectation("set", "/payload/intents", expected, "set", each="/intent"),
                    Expectation("order", "/payload/intents", ["new", "incident"], each="/intent"),
                ),
            )
            for index, expected in enumerate(gold)
        ),
    )

    async def run(envelope):
        return result(plans[envelope.payload["index"]])

    report = await evaluate(
        suite,
        variant(run),
        include_details=True,
        metrics=(
            MetricSpec(
                "intents",
                "/payload/intents",
                "multilabel",
                ("new", "incident"),
                each="/intent",
                expectation="set",
            ),
        ),
    )
    assert [outcomes_of(case) for case in report.cases] == [
        ("passed", "passed"),
        ("passed", "failed"),
        ("failed", "failed"),  # an item without the member is a mismatch, not missing
        ("failed", "failed"),
    ]
    assert report.cases[0].checks[0].details.actual == ("new", "incident")
    metric = report.metrics[0]
    assert (metric.each, metric.expectation) == ("/intent", "set")
    assert (metric.support, metric.observed, metric.invalid, metric.correct) == (4, 2, 2, 2)
    assert report.to_dict()["metrics"][0]["each"] == "/intent"


def outcomes_of(case) -> tuple[str, ...]:
    return tuple(check.outcome for check in case.checks)


async def test_absent_as_null_only_inside_executed_owners() -> None:
    async def run(envelope):
        if envelope.payload["x"] == 2:
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
        return result({"fields": {"client": "A"}})

    checks = (
        Expectation("null_ok", "/payload/fields/identifier", None, absent_as_null=True),
        Expectation("missing", "/payload/fields/identifier", None),
        Expectation("missed", "/payload/fields/deadline", "2026-06-30", absent_as_null=True),
        Expectation("text_null", "/payload/fields/other", "x", "text", absent_as_null=True),
        Expectation(
            "skipped_owner", "/flows/main/steps/unused/result/value", None, absent_as_null=True
        ),
        Expectation("no_flow", "/flows/absent/result/value", None, absent_as_null=True),
    )
    suite = EvaluationSuite(
        "absence",
        "1",
        (
            EvaluationCase("ok", Envelope(payload={"x": 1}), checks),
            EvaluationCase("failed", Envelope(payload={"x": 2}), checks[:1]),
        ),
    )
    report = await evaluate(suite, variant(run), include_details=True)
    assert outcomes_of(report.cases[0]) == (
        "passed",
        "missing",
        "failed",
        "failed",
        "skipped",
        "missing",
    )
    first = report.cases[0].checks[0]
    assert first.details.actual_present is False and first.details.actual is None
    # A failed run never turns an absent value into an observed null.
    assert outcomes_of(report.cases[1]) == ("missing",)


def test_dataset_boundary_accepts_new_comparisons_and_rejects_unknown_keys() -> None:
    case = {
        "id": "a",
        "input": {"payload": {"x": 1}},
        "expectations": [
            {"name": "t", "path": "/payload/a", "expected": "x", "comparison": "text"},
            {"name": "c", "path": "/payload/b", "expected": "x", "comparison": "contains"},
            {
                "name": "e",
                "path": "/payload/c",
                "expected": ["x"],
                "comparison": "set",
                "each": "/v",
            },
            {"name": "n", "path": "/payload/d", "expected": None, "absent_as_null": True},
        ],
    }
    dataset = {
        "name": "d",
        "revision": "1",
        "suites": [{"name": "s", "workflow": "w", "cases": [case]}],
    }
    parsed = EvaluationDataset.model_validate(dataset, strict=True)
    checks = parsed.to_suite(parsed.suites[0]).cases[0].expectations
    assert [(c.comparison, c.each, c.absent_as_null) for c in checks] == [
        ("text", None, False),
        ("contains", None, False),
        ("set", "/v", False),
        ("exact", None, True),
    ]
    case["expectations"][0]["normalize"] = True
    with pytest.raises(ValueError):
        EvaluationDataset.model_validate(dataset, strict=True)


def test_new_options_extend_the_fingerprint_only_when_used() -> None:
    plain = one_case(Expectation("a", "/payload/a", ["x"]))
    same = one_case(Expectation("a", "/payload/a", ["x"], each=None, absent_as_null=False))
    projected = one_case(Expectation("a", "/payload/a", ["x"], each="/v"))
    nullable = one_case(Expectation("a", "/payload/a", ["x"], absent_as_null=True))
    assert plain.fingerprint == same.fingerprint
    assert len({plain.fingerprint, projected.fingerprint, nullable.fingerprint}) == 3


def test_metric_without_expectation_name_rejects_several_assertions_at_its_path() -> None:
    from foliqant.evaluation.metrics import validate_metrics

    suite = one_case(
        Expectation("set", "/payload/labels", ["a"], "set", each="/v"),
        Expectation("order", "/payload/labels", ["a"], each="/v"),
    )
    with pytest.raises(ValueError, match="unambiguous"):
        validate_metrics(suite, (MetricSpec("m", "/payload/labels", "multilabel", ("a",), "/v"),))
    validate_metrics(
        suite, (MetricSpec("m", "/payload/labels", "multilabel", ("a",), "/v", "set"),)
    )


async def test_one_of_accepts_listed_alternatives_and_counts_them_in_the_metric() -> None:
    predictions = ["risk", "exposure", "other", None]
    suite = EvaluationSuite(
        "alternatives",
        "1",
        tuple(
            EvaluationCase(
                str(index),
                Envelope(payload={"index": index}),
                (Expectation("type", "/payload/type", ["risk", "exposure"], "one_of"),),
            )
            for index in range(len(predictions))
        ),
    )

    async def run(envelope):
        return result({"type": predictions[envelope.payload["index"]]})

    report = await evaluate(
        suite,
        variant(run),
        metrics=(
            MetricSpec("type", "/payload/type", "classification", ("risk", "exposure", "other")),
        ),
    )
    assert [case.checks[0].outcome for case in report.cases] == [
        "passed",
        "passed",
        "failed",
        "failed",
    ]
    metric = report.metrics[0]
    assert (metric.correct, metric.observed, metric.abstained) == (2, 3, 1)
    # An accepted alternative is on the diagonal of its own label; a wrong label is a
    # confusion of the primary (first listed) gold.
    assert metric.confusion_matrix == ((1, 0, 1), (0, 1, 0), (0, 0, 0))


@pytest.mark.parametrize("expected", [[], ["a", "a"], "a", None])
def test_one_of_requires_distinct_alternatives(expected) -> None:
    with pytest.raises(ValueError, match="one_of"):
        Expectation("bad", "/payload", expected, "one_of")


def test_one_of_gold_must_be_catalog_labels_of_a_classification_metric() -> None:
    from foliqant.evaluation.metrics import validate_metrics

    suite = one_case(Expectation("t", "/payload/t", ["a", "z"], "one_of"))
    with pytest.raises(ValueError, match="label catalog"):
        validate_metrics(suite, (MetricSpec("m", "/payload/t", "classification", ("a", "b")),))
    with pytest.raises(ValueError, match="label catalog"):
        validate_metrics(suite, (MetricSpec("m", "/payload/t", "multilabel", ("a", "z")),))
