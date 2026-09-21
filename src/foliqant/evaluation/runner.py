"""Bounded async golden evaluation without storage, model judges or hidden retries."""

import asyncio
import math
from collections.abc import Iterable, Mapping
from dataclasses import replace
from time import perf_counter
from typing import cast

from foliqant.contracts.execution import ExecutionResult
from foliqant.core.bindings import resolve_binding
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import MAX_JSON_DEPTH, FrozenJson, freeze_json
from foliqant.core.plan import BindingPlan

from .contracts import (
    CaseDetails,
    CaseReport,
    CheckDetails,
    CheckOutcome,
    CheckReport,
    CheckSummary,
    EvaluationCase,
    EvaluationReport,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    RegisteredScorer,
    StepReport,
    StepSummary,
    canonical,
)
from .metrics import (
    MetricObservation,
    MetricSpec,
    observe_metrics,
    summarize_metrics,
    validate_metrics,
)
from .spans import matches_source_span
from .summaries import summarize_latency, summarize_usage


def _step(path: str) -> str | None:
    parts = path.split("/")
    return (
        parts[2].replace("~1", "/").replace("~0", "~")
        if len(parts) >= 3 and parts[1] == "decisions"
        else None
    )


async def _check(
    expected: Expectation,
    document: FrozenJson,
    result: ExecutionResult,
    scorers: Mapping[str, RegisteredScorer],
    deadline: float,
    include_details: bool,
    target_step: str | None,
    case_input: FrozenJson,
) -> CheckReport:
    step = _step(expected.path) or target_step
    outcome: CheckOutcome
    try:
        actual = resolve_binding(BindingPlan(kind="pointer", pointer=expected.path), document)
    except ServiceError:
        record = result.decisions.get(step) if step is not None else None
        outcome = (
            "skipped"
            if record is not None and record.status == "skipped"
            else "error"
            if record is not None and record.status in {"failed", "cancelled"}
            else "missing"
        )
        return CheckReport(
            expected.name,
            expected.path,
            outcome,
            step,
            outcome,
            CheckDetails(False, None, expected.expected) if include_details else None,
        )
    try:
        if expected.comparison == "custom":
            assert expected.scorer is not None
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError
            async with asyncio.timeout_at(deadline):
                passed = await scorers[expected.scorer].score(actual, expected.expected)
            if type(passed) is not bool:
                raise ValueError("scorer must return bool")
        elif expected.comparison == "source_span":
            passed = matches_source_span(actual, expected.expected, case_input)
        elif expected.comparison == "set":
            passed = isinstance(actual, tuple) and {canonical(item) for item in actual} == {
                canonical(item) for item in cast(tuple[FrozenJson, ...], expected.expected)
            }
        else:
            passed = canonical(actual) == canonical(expected.expected)
        outcome = "passed" if passed else "failed"
    except Exception:
        # Never retain customer/provider/scorer exception messages.
        outcome = "error"
    return CheckReport(
        expected.name,
        expected.path,
        outcome,
        step,
        "match" if outcome == "passed" else "mismatch" if outcome == "failed" else "scorer_error",
        CheckDetails(True, actual, expected.expected) if include_details else None,
    )


async def _case(
    case: EvaluationCase,
    variant: EvaluationVariant,
    scorers: Mapping[str, RegisteredScorer],
    timeout: float,
    metrics: tuple[MetricSpec, ...],
    include_details: bool,
) -> tuple[CaseReport, tuple[MetricObservation, ...]]:
    started = perf_counter()
    deadline = asyncio.get_running_loop().time() + timeout
    case_input = freeze_json(case.envelope().model_dump(mode="json"), max_depth=MAX_JSON_DEPTH + 1)
    private_input = case_input if include_details else None
    try:
        async with asyncio.timeout_at(deadline):
            returned = await variant.run(case.envelope())
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError
        if not isinstance(returned, ExecutionResult):
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        # Revalidate and detach any nested mutable dictionaries before scoring.
        result = ExecutionResult.model_validate(returned.model_dump(mode="json"), strict=True)
        # Payload and each step result retain their business-value depth bound.
        # The public result adds at most three container levels around those values.
        freeze_json(result.payload)
        for record in result.decisions.values():
            if "result" in record.model_fields_set:
                freeze_json(record.result)
        document = freeze_json(result.model_dump(mode="json"), max_depth=MAX_JSON_DEPTH + 3)
    except Exception as error:
        code = (
            "timeout"
            if isinstance(error, TimeoutError)
            else error.code.value
            if isinstance(error, ServiceError)
            else "evaluation_execution_error"
        )
        report = CaseReport(
            case.id,
            "error",
            tuple(
                CheckReport(
                    check.name,
                    check.path,
                    "error",
                    _step(check.path) or variant.step,
                    "execution_error",
                    CheckDetails(False, None, check.expected) if include_details else None,
                )
                for check in case.expectations
            ),
            (),
            perf_counter() - started,
            None,
            None,
            None,
            code,
            CaseDetails(private_input, case.expectations, None) if include_details else None,
        )
        return report, observe_metrics(metrics, case, None, "error")
    elapsed = perf_counter() - started
    checks = tuple(
        [
            await _check(
                check,
                document,
                result,
                scorers,
                deadline,
                include_details,
                variant.step,
                case_input,
            )
            for check in case.expectations
        ]
    )
    steps = tuple(
        StepReport(name, step.status, step.elapsed_seconds, step.usage)
        for name, step in result.decisions.items()
    )
    report = CaseReport(
        case.id,
        result.execution.status,
        checks,
        steps,
        elapsed,
        result.execution.usage,
        result.execution.workflow,
        result.execution.revision,
        result.execution.error.code.value if result.execution.error is not None else None,
        CaseDetails(private_input, case.expectations, document) if include_details else None,
    )
    return report, observe_metrics(metrics, case, document, result.execution.status)


def _summary(checks: Iterable[CheckReport]) -> CheckSummary:
    outcomes = [check.outcome for check in checks]
    return CheckSummary(
        len(outcomes),
        outcomes.count("passed"),
        outcomes.count("failed"),
        outcomes.count("missing"),
        outcomes.count("skipped"),
        outcomes.count("error"),
    )


def _step_summaries(cases: tuple[CaseReport, ...]) -> tuple[StepSummary, ...]:
    names = sorted(
        {step.name for case in cases for step in case.steps}
        | {check.step for case in cases for check in case.checks if check.step is not None}
    )
    summaries = []
    for name in names:
        records = [step for case in cases for step in case.steps if step.name == name]
        summaries.append(
            StepSummary(
                name,
                _summary(check for case in cases for check in case.checks if check.step == name),
                len(records),
                sum(step.status == "skipped" for step in records),
                sum(step.status in {"failed", "cancelled"} for step in records),
                sum(step.status == "needs_review" for step in records),
                summarize_latency(
                    next((step.elapsed_seconds for step in case.steps if step.name == name), None)
                    for case in cases
                ),
                summarize_usage(
                    next((step.usage for step in case.steps if step.name == name), None)
                    for case in cases
                ),
            )
        )
    return tuple(summaries)


def _options(
    suite: EvaluationSuite,
    max_concurrency: int,
    timeout: float,
    scorers: tuple[RegisteredScorer, ...],
    repeat: int = 1,
) -> dict[str, RegisteredScorer]:
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    if type(max_concurrency) is not int or not 1 <= max_concurrency <= 64:
        raise ValueError("max_concurrency must be an integer from 1 to 64")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    registry = {scorer.name: scorer for scorer in scorers}
    if len(registry) != len(scorers):
        raise ValueError("scorer names must be unique")
    needed = {
        check.scorer
        for case in suite.cases
        for check in case.expectations
        if check.scorer is not None
    }
    if not needed <= registry.keys():
        raise ValueError("every custom expectation requires a registered scorer")
    return registry


async def evaluate(
    suite: EvaluationSuite,
    variant: EvaluationVariant,
    *,
    max_concurrency: int = 1,
    timeout: float = 300.0,
    scorers: tuple[RegisteredScorer, ...] = (),
    metrics: tuple[MetricSpec, ...] = (),
    include_details: bool = False,
    repeat: int = 1,
) -> EvaluationReport:
    """Run each case ``repeat`` times and score each attempt against unchanged gold.

    The timeout bounds each case's invocation plus async custom scoring. Cancellation
    propagates and owned workers are joined. At most max_concurrency workers exist;
    results retain case order, then one-based repetition order. Every source case
    has the same number of attempts; failures remain in summary denominators.
    Repeated inputs are not independent new gold. Repeat scheduling is lazy; only
    completed reports accumulate. Async callables must cooperate with cancellation.
    No content is logged, no report is saved, and no provider endpoint is discovered.
    ``include_details=True`` retains private immutable input, gold and complete
    results for explicit caller-owned serialization. Defaults remain content-free.
    """
    registry = _options(suite, max_concurrency, timeout, scorers, repeat)
    if type(include_details) is not bool:
        raise ValueError("include_details must be a boolean")
    metrics = tuple(metrics)
    validate_metrics(suite, metrics)
    fingerprint = suite.fingerprint
    started = perf_counter()
    attempt_count = len(suite.cases) * repeat
    iterator = iter(
        (index * repeat + repetition - 1, case, repetition)
        for index, case in enumerate(suite.cases)
        for repetition in range(1, repeat + 1)
    )
    completed: dict[int, tuple[CaseReport, tuple[MetricObservation, ...]]] = {}
    workers: list[asyncio.Task[None]] = []

    async def worker() -> None:
        try:
            for index, case, repetition in iterator:
                await asyncio.sleep(0)
                report, observations = await _case(
                    case, variant, registry, timeout, metrics, include_details
                )
                completed[index] = replace(report, repetition=repetition), observations
        except asyncio.CancelledError:
            # TaskGroup treats a self-cancelled child as successful termination.
            # Cancel siblings and propagate it explicitly after they are joined.
            for task in workers:
                if task is not asyncio.current_task():
                    task.cancel()
            raise

    async with asyncio.TaskGroup() as group:
        for _ in range(min(max_concurrency, attempt_count)):
            workers.append(group.create_task(worker()))
    if len(completed) != attempt_count:
        raise asyncio.CancelledError
    cases = tuple(completed[index][0] for index in range(attempt_count))
    return EvaluationReport(
        suite.name,
        suite.revision,
        fingerprint,
        variant.name,
        variant.revision,
        variant.configuration_revision,
        tuple(sorted((scorer.name, scorer.revision) for scorer in scorers)),
        max_concurrency,
        timeout,
        cases,
        _summary(check for case in cases for check in case.checks),
        _step_summaries(cases),
        sum(case.passed for case in cases) / len(cases),
        sum(case.status in {"failed", "cancelled", "error"} for case in cases) / len(cases),
        sum(case.status == "needs_review" for case in cases) / len(cases),
        perf_counter() - started,
        summarize_metrics(
            metrics, [completed[index][1] for index in range(attempt_count)], repeat=repeat
        ),
        variant.step,
        variant.workflow,
        repeat,
        len(suite.cases),
        summarize_latency(case.elapsed_seconds for case in cases),
        summarize_usage(case.usage for case in cases),
    )


async def compare_variants(
    suite: EvaluationSuite,
    variants: tuple[EvaluationVariant, ...],
    *,
    max_concurrency: int = 1,
    timeout: float = 300.0,
    scorers: tuple[RegisteredScorer, ...] = (),
    metrics: tuple[MetricSpec, ...] = (),
    include_details: bool = False,
    repeat: int = 1,
) -> tuple[EvaluationReport, ...]:
    """Evaluate variants sequentially on identical case snapshots and gold.

    Callers configure prompt/model alternatives explicitly. This does not optimize
    prompts, certify held-out data, or make stochastic model runs reproducible.
    """
    if not variants or len({variant.name for variant in variants}) != len(variants):
        raise ValueError("comparison requires variants with unique names")
    _options(suite, max_concurrency, timeout, scorers, repeat)
    return tuple(
        [
            await evaluate(
                suite,
                variant,
                max_concurrency=max_concurrency,
                timeout=timeout,
                scorers=scorers,
                metrics=metrics,
                include_details=include_details,
                repeat=repeat,
            )
            for variant in variants
        ]
    )
