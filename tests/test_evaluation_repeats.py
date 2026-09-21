"""Repeated execution keeps source identity, unavailable outcomes and task ownership."""

import asyncio

import pytest
from test_evaluation import result, variant

from foliqant.contracts.envelope import Envelope
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    Expectation,
    MetricSpec,
    compare_variants,
    evaluate,
)


def repeated_suite():
    return EvaluationSuite(
        "sources",
        "1",
        tuple(
            EvaluationCase(
                str(index), Envelope(payload=index), (Expectation("label", "/payload", "a"),)
            )
            for index in range(2)
        ),
    )


async def test_repeat_preserves_source_identity_equal_weights_errors_and_order():
    calls = []

    async def run(envelope):
        calls.append(envelope.payload)
        if len(calls) == 2:
            raise RuntimeError("private failure")
        return result("a" if envelope.payload == 0 else "b")

    report = await evaluate(
        repeated_suite(),
        variant(run),
        repeat=3,
        metrics=(MetricSpec("label", "/payload", "classification", ("a", "b")),),
        include_details=True,
    )
    assert calls == [0, 0, 0, 1, 1, 1]
    assert [(case.id, case.repetition) for case in report.cases] == [
        ("0", 1),
        ("0", 2),
        ("0", 3),
        ("1", 1),
        ("1", 2),
        ("1", 3),
    ]
    assert report.case_pass_rate == 1 / 3
    assert report.failure_rate == 1 / 6
    assert report.checks.total == 6
    assert report.checks.errors == 1
    assert report.repeat == 3
    assert report.source_case_count == report.to_dict()["case_count"] == 2
    assert report.to_dict()["attempt_count"] == 6
    metric = report.metrics[0]
    assert (metric.source_support, metric.support, metric.repeat) == (2, 6, 3)
    assert (metric.observed, metric.errors) == (5, 1)
    assert metric.accuracy == 1 / 3
    assert metric.coverage == 5 / 6
    assert report.latency.count == 6  # The raised attempt was timed too.
    assert report.usage.input_tokens.unknown == 1
    assert report.usage.input_tokens.total is None
    assert report.cases[1].details.input["payload"] == 0


@pytest.mark.parametrize("repeat", [True, False, 0, -1, 1.0, "2", None])
async def test_invalid_repeat_fails_before_invocation_for_both_apis(repeat):
    async def run(envelope):
        pytest.fail("invalid repetition must not invoke a pipeline")

    for invoke, argument in [(evaluate, variant(run)), (compare_variants, (variant(run),))]:
        with pytest.raises(ValueError, match="repeat"):
            await invoke(repeated_suite(), argument, repeat=repeat)


async def test_concurrent_repeats_are_bounded_ordered_and_fresh():
    active = maximum = 0
    calls = []

    async def run(envelope):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        calls.append(envelope.payload)
        try:
            await asyncio.sleep(0.001 if envelope.payload == 0 else 0)
            envelope.payload = "mutated"
            return result("a")
        finally:
            active -= 1

    report = await evaluate(repeated_suite(), variant(run), repeat=4, max_concurrency=3)
    assert maximum == 3
    assert active == 0
    assert calls == [0] * 4 + [1] * 4
    assert [case.id for case in report.cases] == ["0"] * 4 + ["1"] * 4
    assert [case.repetition for case in report.cases] == [1, 2, 3, 4] * 2
    assert report.case_pass_rate == 1


async def test_repeated_variants_share_source_fingerprint_and_run_sequentially():
    calls = []

    def run_variant(name):
        async def run(envelope):
            calls.append(name)
            return result("a")

        return variant(run, name)

    reports = await compare_variants(
        repeated_suite(), (run_variant("first"), run_variant("second")), repeat=2
    )
    assert calls == ["first"] * 4 + ["second"] * 4
    assert (
        reports[0].suite_fingerprint == reports[1].suite_fingerprint == repeated_suite().fingerprint
    )
    assert all(report.repeat == 2 for report in reports)


async def test_cancelled_repeat_stops_future_admission_and_joins_active_attempts():
    entered = asyncio.Event()
    active = calls = 0

    async def run(envelope):
        nonlocal active, calls
        active += 1
        calls += 1
        if active == 2:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    task = asyncio.create_task(
        evaluate(repeated_suite(), variant(run), repeat=20, max_concurrency=2)
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0
    assert calls == 2
