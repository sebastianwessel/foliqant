"""Synthetic golden cases exercise reporting, isolation and async ownership."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import ExecutionResult, StepResult, to_execution_result
from foliqant.core.execution import FlowRecord, RunResult, StepRecord, TokenUsage, Usage
from foliqant.core.json import FrozenJson, freeze_json
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    RegisteredScorer,
    compare_variants,
    evaluate,
)


def result(payload: object = None) -> ExecutionResult:
    return to_execution_result(
        RunResult(
            "test-id",
            "inbox",
            "workflow-revision",
            "completed",
            freeze_json(payload),
            {},
            (
                (
                    "main",
                    FlowRecord(
                        "completed",
                        (
                            (
                                "classify",
                                StepRecord("completed", freeze_json({"label": "a"}), True),
                            ),
                            ("unused", StepRecord("skipped")),
                        ),
                        freeze_json(payload),
                        True,
                        Usage(),
                    ),
                ),
            ),
            Usage(),
        )
    )


def suite(*expectations: Expectation, count: int = 1) -> EvaluationSuite:
    return EvaluationSuite(
        "synthetic",
        "gold-v1",
        tuple(
            EvaluationCase(str(index), Envelope(payload={"labels": ["a", "b"]}), expectations)
            for index in range(count)
        ),
    )


def variant(run: object, name: str = "baseline") -> EvaluationVariant:
    return EvaluationVariant(name, "prompt-v1", run, "configuration-v1")  # type: ignore[arg-type]


def test_isolated_step_variant_requires_its_flow_scope() -> None:
    async def run(envelope: Envelope) -> ExecutionResult:
        return result(envelope.payload)

    with pytest.raises(ValueError, match="flow"):
        EvaluationVariant("step", "v1", run, "configuration-v1", step="classify")


async def test_gold_counts_missing_skipped_and_type_sensitive_mismatches() -> None:
    async def run(envelope: Envelope) -> ExecutionResult:
        return result({"value": True, "nothing": None, "labels": ["b", "a", "a"]})

    gold = suite(
        Expectation("boolean", "/payload/value", 1),
        Expectation("null", "/payload/nothing", None),
        Expectation("absent", "/payload/absent", None),
        Expectation("set", "/payload/labels", ("a", "b"), "set"),
        Expectation("step", "/flows/main/steps/classify/result/label", "a"),
        Expectation("skipped", "/flows/main/steps/unused/result", None),
        Expectation("expected-skip", "/flows/main/steps/unused/status", "skipped"),
    )
    report = await evaluate(gold, variant(run))
    assert (report.checks.total, report.checks.passed, report.checks.failed) == (7, 4, 1)
    assert (report.checks.missing, report.checks.skipped) == (1, 1)
    assert report.checks.pass_rate == 4 / 7
    assert report.checks.coverage == 5 / 7
    assert report.case_pass_rate == 0
    assert report.failure_rate == 0
    assert report.cases[0].workflow_revision == "workflow-revision"
    assert report.cases[0].steps[0].elapsed_seconds is None
    assert report.cases[0].usage is not None
    assert report.cases[0].usage.input_tokens == 0
    assert report.to_dict()["case_count"] == 1
    assert "expected" not in str(report.to_dict()).replace("expected-skip", "")
    with pytest.raises(FrozenInstanceError):
        report.variant_name = "edited"  # type: ignore[misc]


async def test_variants_receive_fresh_inputs_and_same_fingerprint() -> None:
    received: list[object] = []

    async def first(envelope: Envelope) -> ExecutionResult:
        assert isinstance(envelope.payload, dict)
        received.append(envelope.payload["labels"][:])  # type: ignore[index]
        envelope.payload["labels"] = ["changed"]
        return result({"label": "wrong"})

    async def second(envelope: Envelope) -> ExecutionResult:
        received.append(envelope.payload)
        return result({"label": "a"})

    reports = await compare_variants(
        suite(Expectation("label", "/payload/label", "a")),
        (variant(first), variant(second, "candidate")),
    )
    assert reports[0].case_pass_rate == 0
    assert reports[1].case_pass_rate == 1
    assert reports[0].suite_fingerprint == reports[1].suite_fingerprint
    assert received == [["a", "b"], {"labels": ["a", "b"]}]


async def test_errors_are_safe_and_timeout_unknown_usage_is_not_zero() -> None:
    async def broken(envelope: Envelope) -> ExecutionResult:
        raise RuntimeError("secret-customer-provider-message")

    report = await evaluate(
        suite(Expectation("status", "/execution/status", "completed")), variant(broken)
    )
    assert report.failure_rate == 1
    assert report.checks.errors == 1
    assert report.cases[0].usage is None
    assert "secret-customer" not in str(report.to_dict())

    async def slow(envelope: Envelope) -> ExecutionResult:
        await asyncio.sleep(10)
        return result()

    report = await evaluate(
        suite(Expectation("payload", "/payload", None)), variant(slow), timeout=0.001
    )
    assert report.cases[0].error_code == "timeout"


async def test_custom_scorers_are_versioned_and_fail_honestly() -> None:
    async def run(envelope: Envelope) -> ExecutionResult:
        return result("ACTUAL")

    async def normalized(actual: FrozenJson, expected: FrozenJson) -> bool:
        assert isinstance(actual, str) and isinstance(expected, str)
        return actual.lower() == expected.lower()

    gold = suite(Expectation("normalized", "/payload", "actual", "custom", "lower"))
    with pytest.raises(ValueError):
        await evaluate(gold, variant(run))
    report = await evaluate(
        gold, variant(run), scorers=(RegisteredScorer("lower", "v1", normalized),)
    )
    assert report.case_pass_rate == 1
    assert report.scorer_revisions == (("lower", "v1"),)

    async def broken(actual: FrozenJson, expected: FrozenJson) -> bool:
        raise RuntimeError("private")

    report = await evaluate(
        gold, variant(run), scorers=(RegisteredScorer("lower", "broken", broken),)
    )
    assert report.checks.errors == 1
    assert report.checks.coverage == 0
    assert report.failure_rate == 0  # The workflow succeeded; its scorer did not.


async def test_bounded_workers_keep_input_order_and_cancellation_propagates() -> None:
    active = peak = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def run(envelope: Envelope) -> ExecutionResult:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        try:
            await release.wait()
            return result()
        finally:
            active -= 1

    gold = suite(Expectation("payload", "/payload", None), count=50)
    task = asyncio.create_task(evaluate(gold, variant(run), max_concurrency=2))
    await entered.wait()
    await asyncio.sleep(0)
    assert active == peak == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0
    release.set()
    report = await evaluate(gold, variant(run), max_concurrency=2)
    assert tuple(case.id for case in report.cases) == tuple(str(i) for i in range(50))


async def test_review_and_failure_metrics_independent_of_expected_status() -> None:
    from foliqant.core.errors import ErrorCode
    from foliqant.core.execution import Failure

    async def run(envelope: Envelope) -> ExecutionResult:
        return to_execution_result(
            RunResult(
                "failure",
                "inbox",
                "r1",
                "failed",
                None,
                {},
                (
                    (
                        "main",
                        FlowRecord(
                            "failed",
                            (("classify", StepRecord("failed", error=Failure(ErrorCode.TIMEOUT))),),
                            error=Failure(ErrorCode.TIMEOUT),
                        ),
                    ),
                ),
                Usage(),
                Failure(ErrorCode.TIMEOUT),
            )
        )

    report = await evaluate(
        suite(
            Expectation("status", "/execution/status", "failed"),
            Expectation("unavailable", "/flows/main/steps/classify/result", None),
        ),
        variant(run),
    )
    assert report.failure_rate == 1
    assert report.checks.passed == report.checks.errors == 1

    async def review(envelope: Envelope) -> ExecutionResult:
        return to_execution_result(
            RunResult(
                "review",
                "inbox",
                "r1",
                "needs_review",
                None,
                {},
                (),
                Usage(),
            )
        )

    report = await evaluate(
        suite(Expectation("status", "/execution/status", "needs_review")), variant(review)
    )
    assert report.review_rate == report.case_pass_rate == 1
    assert report.failure_rate == 0


def test_gold_is_copied_and_invalid_contracts_fail_before_execution() -> None:
    envelope = Envelope(payload={"labels": ["a"]})
    expected = {"label": "a"}
    check = Expectation("label", "/payload", expected)
    case = EvaluationCase("case", envelope, (check,))
    expected["label"] = "changed"
    assert isinstance(envelope.payload, dict)
    envelope.payload["labels"] = []
    assert case.envelope().payload == {"labels": ["a"]}
    assert check.expected == {"label": "a"}
    with pytest.raises(ValueError):
        Expectation("bad", "/bad~escape", None)
    with pytest.raises(ValueError):
        Expectation("bad", "/payload", "not-an-array", "set")
    with pytest.raises(ValueError):
        EvaluationSuite("empty", "v1", ())
    with pytest.raises(ValueError):
        EvaluationSuite("duplicate", "v1", (case, case))
    with pytest.raises(ValueError):
        EvaluationCase("case", envelope, (check, check))


async def test_pointer_escaping_and_measured_step_fields() -> None:
    async def run(envelope: Envelope) -> ExecutionResult:
        base = result({"a/b": {"~key": [3]}})
        base.flows["main"].steps["classify"] = StepResult(
            status="completed",
            result={"label": "a"},
            elapsed_seconds=0.25,
            usage=base.execution.usage,
        )
        return base

    report = await evaluate(suite(Expectation("escaped", "/payload/a~1b/~0key/0", 3)), variant(run))
    assert report.case_pass_rate == 1
    assert report.cases[0].steps[0].elapsed_seconds == 0.25
    assert report.cases[0].steps[0].usage is not None


async def test_early_review_keeps_flow_scoped_step_denominators_and_usage_separate() -> None:
    measured = Usage(model_requests=1, tokens=TokenUsage(input_tokens=7, output_tokens=2))

    async def run(envelope: Envelope) -> ExecutionResult:
        del envelope
        return to_execution_result(
            RunResult(
                "review",
                "inbox",
                "r1",
                "needs_review",
                None,
                {},
                (
                    (
                        "main",
                        FlowRecord(
                            "needs_review",
                            (
                                (
                                    "assess",
                                    StepRecord(
                                        "needs_review",
                                        freeze_json({"answer": "review"}),
                                        True,
                                        usage=measured,
                                    ),
                                ),
                                ("later", StepRecord("skipped")),
                            ),
                            usage=measured,
                            elapsed_seconds=0.5,
                        ),
                    ),
                ),
                measured,
            )
        )

    report = await evaluate(
        suite(
            Expectation("answer", "/flows/main/steps/assess/result/answer", "review"),
            Expectation("later", "/flows/main/steps/later/result", None),
        ),
        EvaluationVariant("review", "v1", run, "configuration-v1", flow="main", workflow="inbox"),
    )

    assert report.target_flow == "main"
    assert report.review_rate == 1
    assert (report.checks.passed, report.checks.skipped) == (1, 1)
    assert [(check.flow, check.step) for check in report.cases[0].checks] == [
        ("main", "assess"),
        ("main", "later"),
    ]
    assert [(step.flow, step.name) for step in report.steps] == [
        ("main", "assess"),
        ("main", "later"),
    ]
    assert report.usage is not None and report.usage.model_requests.known_total == 1
    assert report.flows[0].usage.model_requests.known_total == 1
    assert report.steps[0].usage.model_requests.known_total == 1


async def test_pipeline_self_cancellation_cancels_other_workers() -> None:
    active = 0

    async def cancelled(envelope: Envelope) -> ExecutionResult:
        nonlocal active
        active += 1
        try:
            if active == 1:
                await asyncio.sleep(0)
                raise asyncio.CancelledError
            await asyncio.sleep(10)
            return result()
        finally:
            active -= 1

    with pytest.raises(asyncio.CancelledError):
        await evaluate(
            suite(Expectation("payload", "/payload", None), count=4),
            variant(cancelled),
            max_concurrency=2,
        )
    assert active == 0


async def test_evaluate_an_isolated_real_runner_step(tmp_path) -> None:
    del tmp_path
    from test_flow_runner import Executor, flow, plan, runner

    from foliqant.bootstrap import WorkflowApplication

    executor = Executor()
    app = WorkflowApplication({"inbox": runner(plan(flow("main")), executor)})

    async def isolated(envelope: Envelope) -> ExecutionResult:
        return await app.run_step("inbox", "main", "step_0", envelope)

    gold = EvaluationSuite(
        "step-gold",
        "v1",
        (
            EvaluationCase(
                "case",
                Envelope(payload={"message": "Bitte helfen."}),
                (
                    Expectation("label", "/flows/main/steps/step_0/result/text", "Bitte helfen."),
                    Expectation("final", "/payload/text", "Bitte helfen."),
                ),
            ),
        ),
    )
    report = await evaluate(gold, variant(isolated))
    assert [call[2] for call in executor.calls] == ["step_0"]
    assert report.case_pass_rate == 1
    assert report.cases[0].steps[0].elapsed_seconds is not None


def test_fingerprint_accepts_valid_deep_gold_and_unicode_before_execution() -> None:
    deep: object = "\ud800"
    for _ in range(64):
        deep = [deep]
    gold = suite(Expectation("deep", "/payload", deep))  # type: ignore[arg-type]
    assert len(gold.fingerprint) == 64
    assert gold.fingerprint == gold.fingerprint
