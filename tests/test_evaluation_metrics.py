"""Metrics preserve unavailable observations and private details are explicitly opt-in."""

import asyncio
import json
from collections.abc import Mapping

import pytest
from test_evaluation import result, suite, variant

from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import StepResult, to_execution_result
from foliqant.core.errors import ErrorCode
from foliqant.core.execution import Failure, RunResult, Usage
from foliqant.core.json import freeze_json
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    MetricSpec,
    compare_variants,
    evaluate,
)


def cases(*gold):
    return EvaluationSuite(
        "metrics",
        "1",
        tuple(
            EvaluationCase(str(index), Envelope(payload={"index": index}), checks)
            for index, checks in enumerate(gold)
        ),
    )


async def test_classification_matrix_orientation_and_unavailable_denominators():
    gold = cases(
        *[
            (Expectation("label", "/payload/label", label),)
            for label in ["b", "b", "a", "a", "a", "a", "a"]
        ],
        (Expectation("other", "/payload", {}),),
    )
    predictions = [
        {"label": "a"},
        {"label": "b"},
        {"label": None},
        {},
        {"label": 1},
        {"label": "outside"},
        {"label": "a"},
        {},
    ]

    async def run(envelope):
        index = envelope.payload["index"]
        if index == 6:
            return to_execution_result(
                RunResult(
                    "failed",
                    "inbox",
                    "r1",
                    "failed",
                    freeze_json(predictions[index]),
                    {},
                    (),
                    Usage(),
                    Failure(ErrorCode.TIMEOUT),
                )
            )
        return result(predictions[index])

    report = await evaluate(
        gold,
        variant(run),
        metrics=(MetricSpec("label", "/payload/label", "classification", ("b", "a")),),
    )
    metric = report.metrics[0]
    assert metric.labels == ("b", "a")
    assert metric.confusion_matrix == ((1, 1), (0, 0))
    assert (metric.support, metric.excluded, metric.observed) == (7, 1, 2)
    assert (metric.abstained, metric.missing, metric.errors, metric.invalid) == (1, 1, 1, 2)
    assert metric.accuracy == 1 / 7
    assert metric.coverage == 2 / 7
    assert metric.correct == 1
    assert all(
        sum((label.true_positive, label.false_positive, label.false_negative, label.true_negative))
        == 2
        for label in metric.per_label
    )
    assert report.to_dict()["metrics"][0]["confusion_matrix"] == [[1, 1], [0, 0]]


async def test_multilabel_counts_are_observed_only_but_accuracy_keeps_all_gold():
    values = [
        (["a", "b"], ["b", "b"]),
        ([], []),
        (["a"], None),
        (["b"], ["unknown"]),
        (["a"], [True]),
    ]
    gold = cases(*[(Expectation("labels", "/payload", expected, "set"),) for expected, _ in values])

    async def run(envelope):
        return result(values[envelope.payload["index"]][1])

    metric = (
        await evaluate(
            gold,
            variant(run),
            metrics=(MetricSpec("labels", "/payload", "multilabel", ("a", "b")),),
        )
    ).metrics[0]
    assert (metric.support, metric.observed, metric.abstained, metric.invalid) == (5, 2, 1, 2)
    assert metric.confusion_matrix == ()
    assert metric.accuracy == 1 / 5
    assert metric.coverage == 2 / 5
    a, b = metric.per_label
    assert (a.true_positive, a.false_positive, a.false_negative, a.true_negative) == (0, 0, 1, 1)
    assert (b.true_positive, b.false_positive, b.false_negative, b.true_negative) == (1, 0, 0, 1)


async def test_metrics_distinguish_skipped_steps_and_zero_support():
    async def run(envelope):
        return result()

    report = await evaluate(
        suite(Expectation("unused", "/decisions/unused/result", "a")),
        variant(run),
        metrics=(
            MetricSpec("skipped", "/decisions/unused/result", "classification", ("a",)),
            MetricSpec("excluded", "/payload/other", "classification", ("a",)),
        ),
    )
    skipped, excluded = report.metrics
    assert skipped.skipped == skipped.support == 1
    assert skipped.accuracy == skipped.coverage == 0
    assert excluded.excluded == 1
    assert excluded.support == 0
    assert excluded.accuracy is excluded.coverage is None


@pytest.mark.parametrize(
    "expectations",
    [
        (Expectation("invalid", "/payload", 1),),
        (Expectation("unknown", "/payload", "unknown"),),
        (Expectation("one", "/payload", "a"), Expectation("two", "/payload", "a")),
    ],
)
async def test_invalid_gold_fails_before_execution(expectations):
    called = False

    async def run(envelope):
        nonlocal called
        called = True
        return result()

    with pytest.raises(ValueError, match="gold"):
        await evaluate(
            suite(*expectations),
            variant(run),
            metrics=(MetricSpec("label", "/payload", "classification", ("a",)),),
        )
    assert not called


def test_metric_catalog_is_explicit_unique_and_typed():
    for labels in [(), ("a", "a"), ("a", 1), "ab"]:
        with pytest.raises(ValueError):
            MetricSpec("label", "/payload", "classification", labels)


async def test_details_are_opt_in_detached_complete_and_json_serializable():
    returned = result({"label": "a", "reason": "public business explanation"})
    gold = suite(
        Expectation("label", "/payload/label", "a"),
        Expectation("missing", "/payload/missing", None),
        Expectation("null", "/payload/nothing", None),
    )
    returned.payload["nothing"] = None

    async def run(envelope):
        return returned

    safe = await evaluate(gold, variant(run))
    safe_json = json.dumps(safe.to_dict())
    assert "public business explanation" not in safe_json
    assert "details" not in safe_json
    assert safe.cases[0].details is None
    detailed = await evaluate(gold, variant(run), include_details=True)
    case = detailed.cases[0]
    assert isinstance(case.details.result, Mapping)
    returned.payload["reason"] = "changed"
    assert case.details.result["payload"]["reason"] == "public business explanation"
    with pytest.raises(TypeError):
        case.details.result["payload"]["reason"] = "cannot mutate"
    assert case.details.input["payload"] == {"labels": ("a", "b")}
    assert case.details.expectations == gold.cases[0].expectations
    assert case.checks[0].details.actual == case.checks[0].details.expected == "a"
    assert case.checks[0].reason_code == "match"
    assert case.checks[1].details.actual_present is False
    assert case.checks[2].details.actual_present is True
    encoded = json.dumps(detailed.to_dict())
    assert "public business explanation" in encoded
    detached = detailed.to_dict()
    detached["cases"][0]["details"]["result"]["payload"]["reason"] = "another mutation"
    assert case.details.result["payload"]["reason"] == "public business explanation"


async def test_error_details_retain_input_and_gold_without_exception_message():
    async def run(envelope):
        raise RuntimeError("private exception must not be stored")

    report = await evaluate(
        suite(Expectation("gold", "/payload", "a")),
        variant(run),
        include_details=True,
        metrics=(MetricSpec("label", "/payload", "classification", ("a",)),),
    )
    case = report.cases[0]
    assert case.details.input["payload"] == {"labels": ("a", "b")}
    assert case.details.result is None
    assert case.details.expectations[0].expected == "a"
    assert case.checks[0].details.actual_present is False
    assert case.error_code == "evaluation_execution_error"
    assert report.metrics[0].errors == 1
    assert "private exception" not in json.dumps(report.to_dict())
    assert case.usage is None


async def test_depth64_payload_and_step_result_work_and_invalid_depth_is_per_case():
    deep = "leaf"
    for _ in range(64):
        deep = [deep]
    deep_result = result(deep)
    deep_result.decisions["classify"] = StepResult(status="completed", result=deep)

    async def run(envelope):
        if envelope.payload["index"] == 0:
            return deep_result
        return result("okay")

    gold = cases(
        (Expectation("deep", "/payload", deep),), (Expectation("okay", "/payload", "okay"),)
    )
    report = await evaluate(gold, variant(run), include_details=True)
    assert report.case_pass_rate == 1
    assert json.loads(json.dumps(report.to_dict()))["case_count"] == 2
    # Public models allow construction of a deeper value, but evaluator catches it per case.
    deep_result = deep_result.model_copy(update={"payload": [deep]})
    report = await evaluate(gold, variant(run), include_details=True, max_concurrency=2)
    assert report.cases[0].status == "error"
    assert report.cases[0].details.result is None
    assert report.cases[1].passed


async def test_explicit_step_attribution_and_compare_options():
    async def run(envelope):
        return result({"label": "a"})

    target = EvaluationVariant("step", "r1", run, "config", step="classify")
    reports = await compare_variants(
        suite(Expectation("label", "/payload/label", "a")),
        (target,),
        include_details=True,
        metrics=(MetricSpec("label", "/payload/label", "classification", ("a",)),),
    )
    report = reports[0]
    assert report.target_step == "classify"
    assert report.cases[0].checks[0].step == "classify"
    assert next(step for step in report.steps if step.name == "classify").checks.passed == 1
    assert report.metrics[0].accuracy == 1
    assert report.cases[0].details is not None


async def test_details_and_metrics_preserve_cancellation():
    entered = asyncio.Event()
    finished = asyncio.Event()

    async def run(envelope):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    task = asyncio.create_task(
        evaluate(
            suite(Expectation("label", "/payload", "a")),
            variant(run),
            include_details=True,
            metrics=(MetricSpec("label", "/payload", "classification", ("a",)),),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
