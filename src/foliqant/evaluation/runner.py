"""Bounded async golden evaluation without storage, model judges or hidden retries."""

import asyncio
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from time import perf_counter

from foliqant.contracts.execution import ExecutionResult
from foliqant.core.bindings import resolve_binding
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import MAX_JSON_DEPTH, FrozenJson, freeze_json
from foliqant.core.plan import BindingPlan
from foliqant.core.runner import ExecutionLimits

from .checkpoint import (
    EvaluationCheckpoint,
    EvaluationProgress,
    ProgressCallback,
    SavedAttempt,
    checkpoint_identity,
)
from .comparisons import EXECUTED_STATUSES, ProjectionError, canonical, matches, project
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
    FlowReport,
    FlowSummary,
    RegisteredScorer,
    StepReport,
    StepSummary,
)
from .metrics import (
    MetricObservation,
    MetricSpec,
    observe_metrics,
    summarize_metrics,
    validate_metrics,
)
from .records import FlowObservation, StepObservation, path_observation, snapshot_execution
from .summaries import count_failures, summarize_latency, summarize_usage


def _scope(path: str) -> tuple[str | None, str | None]:
    parts = path.split("/")
    if len(parts) < 3 or parts[1] != "flows":
        return None, None
    flow = parts[2].replace("~1", "/").replace("~0", "~")
    step = None
    if len(parts) >= 5 and parts[3] == "steps":
        step = parts[4].replace("~1", "/").replace("~0", "~")
    return flow, step


async def _check(
    expected: Expectation,
    document: FrozenJson,
    result: ExecutionResult,
    scorers: Mapping[str, RegisteredScorer],
    deadline: float,
    include_details: bool,
    target_flow: str | None,
    target_step: str | None,
    case_input: FrozenJson,
    flow_records: tuple[FlowObservation, ...],
    step_records: tuple[StepObservation, ...],
) -> CheckReport:
    path_flow, path_step = _scope(expected.path)
    flow = path_flow or target_flow
    step = path_step or target_step
    owner = path_observation(expected.path, flow_records, step_records)
    if isinstance(owner, StepObservation):
        flow, step = owner.flow, owner.name
    elif isinstance(owner, FlowObservation):
        flow, step = owner.name, None
    invocation_path = owner.path if owner is not None else None
    outcome: CheckOutcome
    present = True
    try:
        actual = resolve_binding(BindingPlan(kind="pointer", pointer=expected.path), document)
    except ServiceError:
        flow_record = result.flows.get(flow) if flow is not None else None
        record = (
            flow_record.steps.get(step) if flow_record is not None and step is not None else None
        )
        status = (
            owner.record.status
            if owner is not None
            else record.status
            if record is not None
            else flow_record.status
            if flow_record
            else None
        )
        outcome = (
            "skipped"
            if status == "skipped"
            else "error"
            if status in {"failed", "cancelled"}
            else "missing"
        )
        # An absent value counts as null only inside an executed owner: a missing
        # flow or step record never becomes an observed null.
        executed = (
            status in EXECUTED_STATUSES
            if flow is not None
            else result.execution.status in EXECUTED_STATUSES
        )
        if not (outcome == "missing" and expected.absent_as_null and executed):
            return CheckReport(
                expected.name,
                expected.path,
                outcome,
                flow,
                step,
                outcome,
                CheckDetails(False, None, expected.expected) if include_details else None,
                invocation_path,
            )
        actual, present = None, False
    try:
        if expected.each is not None:
            actual = project(actual, expected.each)
        if expected.comparison == "custom":
            assert expected.scorer is not None
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError
            async with asyncio.timeout_at(deadline):
                passed = await scorers[expected.scorer].score(actual, expected.expected)
            if type(passed) is not bool:
                raise ValueError("scorer must return bool")
        else:
            passed = matches(expected, actual, case_input)
        outcome = "passed" if passed else "failed"
    except ProjectionError:
        outcome = "failed"
    except Exception:
        # Never retain customer/provider/scorer exception messages.
        outcome = "error"
    return CheckReport(
        expected.name,
        expected.path,
        outcome,
        flow,
        step,
        "match" if outcome == "passed" else "mismatch" if outcome == "failed" else "scorer_error",
        CheckDetails(present, actual, expected.expected) if include_details else None,
        invocation_path,
    )


type _Journal = Callable[[str, float, ExecutionResult], None]

#: Time for async custom scoring after a case's run.
SCORING_GRACE_SECONDS = 60.0
#: Default per-case deadline: the default ``execution.run_timeout`` plus scoring.
DEFAULT_CASE_TIMEOUT = ExecutionLimits().run_timeout + SCORING_GRACE_SECONDS


async def _case(
    case: EvaluationCase,
    variant: EvaluationVariant,
    scorers: Mapping[str, RegisteredScorer],
    timeout: float,
    metrics: tuple[MetricSpec, ...],
    include_details: bool,
    saved: SavedAttempt | None = None,
    journal: _Journal | None = None,
) -> tuple[CaseReport, tuple[MetricObservation, ...]]:
    started = perf_counter()
    deadline = asyncio.get_running_loop().time() + timeout
    case_input = freeze_json(case.envelope().model_dump(mode="json"), max_depth=MAX_JSON_DEPTH + 1)
    private_input = case_input if include_details else None
    try:
        if saved is not None:
            # A checkpointed attempt: its recorded result, scored against current gold.
            returned = ExecutionResult.model_validate(saved.result, strict=True)
        else:
            async with asyncio.timeout_at(deadline):
                returned = await variant.run(case.envelope())
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError
        if not isinstance(returned, ExecutionResult):
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        # Revalidate and detach any nested mutable dictionaries before scoring.
        result = ExecutionResult.model_validate(returned.model_dump(mode="json"), strict=True)
        document, flow_records, step_records = snapshot_execution(result)
    except Exception as error:
        code = (
            # The case timeout bounds the whole run.
            ErrorCode.RUN_TIMEOUT.value
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
                    _scope(check.path)[0] or variant.flow,
                    _scope(check.path)[1] or variant.step,
                    "execution_error",
                    CheckDetails(False, None, check.expected) if include_details else None,
                )
                for check in case.expectations
            ),
            (),
            (),
            perf_counter() - started,
            None,
            None,
            None,
            code,
            CaseDetails(private_input, case.expectations, None) if include_details else None,
        )
        return report, observe_metrics(metrics, case, None, "error")
    elapsed = saved.elapsed_seconds if saved is not None else perf_counter() - started
    if saved is None and journal is not None and result.execution.status in EXECUTED_STATUSES:
        journal(case.id, elapsed, result)
    checks = tuple(
        [
            await _check(
                check,
                document,
                result,
                scorers,
                deadline,
                include_details,
                variant.flow,
                variant.step,
                case_input,
                flow_records,
                step_records,
            )
            for check in case.expectations
        ]
    )
    flows = tuple(
        FlowReport(
            item.name,
            item.record.status,
            item.record.elapsed_seconds,
            item.record.usage,
            item.path,
            item.record.error.code.value if item.record.error is not None else None,
        )
        for item in flow_records
    )
    steps = tuple(
        StepReport(
            item.flow,
            item.name,
            item.record.status,
            item.record.elapsed_seconds,
            item.record.usage,
            item.record.selection.origin if item.record.selection is not None else None,
            item.path,
            item.record.error.code.value if item.record.error is not None else None,
            item.record.error.reason if item.record.error is not None else None,
        )
        for item in step_records
    )
    report = CaseReport(
        case.id,
        result.execution.status,
        checks,
        flows,
        steps,
        elapsed,
        result.execution.usage,
        result.execution.workflow,
        result.execution.revision,
        result.execution.error.code.value if result.execution.error is not None else None,
        CaseDetails(private_input, case.expectations, document) if include_details else None,
        resumed=saved is not None,
        error_reason=result.execution.error.reason if result.execution.error is not None else None,
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
        {(step.flow, step.name) for case in cases for step in case.steps}
        | {
            (check.flow, check.step)
            for case in cases
            for check in case.checks
            if check.flow is not None and check.step is not None
        }
    )
    summaries = []
    for flow, name in names:
        assert flow is not None and name is not None
        records = [
            step for case in cases for step in case.steps if (step.flow, step.name) == (flow, name)
        ]
        summaries.append(
            StepSummary(
                flow,
                name,
                _summary(
                    check
                    for case in cases
                    for check in case.checks
                    if (check.flow, check.step) == (flow, name)
                ),
                len(records),
                sum(step.status == "skipped" for step in records),
                sum(step.status in {"failed", "cancelled"} for step in records),
                sum(step.status == "needs_review" for step in records),
                summarize_latency(
                    value
                    for case in cases
                    for value in (
                        [
                            step.elapsed_seconds
                            for step in case.steps
                            if (step.flow, step.name) == (flow, name)
                        ]
                        or [None]
                    )
                ),
                summarize_usage(
                    value
                    for case in cases
                    for value in (
                        [
                            step.usage
                            for step in case.steps
                            if (step.flow, step.name) == (flow, name)
                        ]
                        or [None]
                    )
                ),
                model_selected_invocations=sum(
                    step.selection_origin == "model" for step in records
                ),
                fallback_selected_invocations=sum(
                    step.selection_origin == "fallback" for step in records
                ),
                fallback_rate=(
                    sum(step.selection_origin == "fallback" for step in records) / len(records)
                    if records
                    else None
                ),
                failures_by_code=count_failures((step.status, step.error_code) for step in records),
                failures_by_reason=count_failures(
                    (step.status, step.error_reason) for step in records
                ),
                output_retries=sum(
                    step.usage.output_retries for step in records if step.usage is not None
                ),
                output_retry_recoveries=sum(
                    step.status in {"completed", "needs_review"}
                    and step.usage is not None
                    and step.usage.output_retries > 0
                    for step in records
                ),
            )
        )
    return tuple(summaries)


def _flow_summaries(cases: tuple[CaseReport, ...]) -> tuple[FlowSummary, ...]:
    names = sorted({flow.name for case in cases for flow in case.flows})
    return tuple(
        FlowSummary(
            name,
            len(records := [flow for case in cases for flow in case.flows if flow.name == name]),
            sum(flow.status == "skipped" for flow in records),
            sum(flow.status in {"failed", "cancelled"} for flow in records),
            sum(flow.status == "needs_review" for flow in records),
            summarize_latency(
                value
                for case in cases
                for value in (
                    [flow.elapsed_seconds for flow in case.flows if flow.name == name] or [None]
                )
            ),
            summarize_usage(
                value
                for case in cases
                for value in ([flow.usage for flow in case.flows if flow.name == name] or [None])
            ),
            count_failures((flow.status, flow.error_code) for flow in records),
        )
        for name in names
    )


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
    timeout: float = DEFAULT_CASE_TIMEOUT,
    scorers: tuple[RegisteredScorer, ...] = (),
    metrics: tuple[MetricSpec, ...] = (),
    include_details: bool = False,
    repeat: int = 1,
    checkpoint: EvaluationCheckpoint | None = None,
    progress: ProgressCallback | None = None,
) -> EvaluationReport:
    """Run each case ``repeat`` times and score each attempt against unchanged gold.

    The timeout bounds each case's invocation plus async custom scoring; the default
    covers the default ``run_timeout`` plus ``SCORING_GRACE_SECONDS``. Cancellation
    propagates and owned workers are joined. At most max_concurrency workers exist;
    results retain case order, then one-based repetition order. Every source case
    has the same number of attempts; failures remain in summary denominators.
    Repeated inputs are not independent new gold. Repeat scheduling is lazy; only
    completed reports accumulate. Async callables must cooperate with cancellation.
    No content is logged, no report is saved, and no provider endpoint is discovered.
    ``include_details=True`` retains private immutable input, gold and complete
    results for explicit caller-owned serialization. Defaults remain content-free.

    ``checkpoint`` records every completed or reviewed attempt as it finishes and
    reuses recorded attempts of the same identity and input instead of calling the
    pipeline (resume; see ``EvaluationCheckpoint``). ``progress`` receives a
    content-free ``EvaluationProgress`` after every finished attempt.
    """
    registry = _options(suite, max_concurrency, timeout, scorers, repeat)
    if type(include_details) is not bool:
        raise ValueError("include_details must be a boolean")
    if checkpoint is not None and not isinstance(checkpoint, EvaluationCheckpoint):
        raise ValueError("checkpoint must be an EvaluationCheckpoint")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be callable")
    metrics = tuple(metrics)
    validate_metrics(suite, metrics)
    identity = checkpoint_identity(suite.name, variant)
    recorded = await asyncio.to_thread(checkpoint.load, identity) if checkpoint is not None else {}
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
    counts = {"resumed": 0, "failed": 0}

    def saved_attempt(case: EvaluationCase, repetition: int) -> SavedAttempt | None:
        saved = recorded.get((case.id, repetition))
        if saved is None or canonical(freeze_json(saved.input)) != canonical(
            freeze_json(case.envelope().model_dump(mode="json"))
        ):
            return None  # never recorded, or recorded for another input
        return saved

    def journal_for(case: EvaluationCase, repetition: int) -> _Journal | None:
        if checkpoint is None:
            return None

        def journal(case_id: str, elapsed: float, result: ExecutionResult) -> None:
            checkpoint.record(
                identity,
                case_id,
                repetition,
                case.envelope().model_dump(mode="json"),
                elapsed,
                result.model_dump(mode="json"),
            )

        return journal

    async def worker() -> None:
        try:
            for index, case, repetition in iterator:
                await asyncio.sleep(0)
                saved = saved_attempt(case, repetition)
                report, observations = await _case(
                    case,
                    variant,
                    registry,
                    timeout,
                    metrics,
                    include_details,
                    saved,
                    journal_for(case, repetition),
                )
                completed[index] = replace(report, repetition=repetition), observations
                counts["resumed"] += saved is not None
                counts["failed"] += report.status in {"failed", "cancelled", "error"}
                if progress is not None:
                    progress(
                        EvaluationProgress(
                            suite.name,
                            len(completed),
                            attempt_count,
                            counts["resumed"],
                            counts["failed"],
                            perf_counter() - started,
                        )
                    )
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
        _flow_summaries(cases),
        _step_summaries(cases),
        sum(case.passed for case in cases) / len(cases),
        sum(case.status in {"failed", "cancelled", "error"} for case in cases) / len(cases),
        sum(case.status == "needs_review" for case in cases) / len(cases),
        perf_counter() - started,
        summarize_metrics(
            metrics, [completed[index][1] for index in range(attempt_count)], repeat=repeat
        ),
        variant.step,
        variant.flow,
        variant.workflow,
        repeat,
        len(suite.cases),
        summarize_latency(case.elapsed_seconds for case in cases),
        summarize_usage(case.usage for case in cases),
        counts["resumed"],
        count_failures((case.status, case.error_code) for case in cases),
        count_failures((case.status, case.error_reason) for case in cases),
    )


async def compare_variants(
    suite: EvaluationSuite,
    variants: tuple[EvaluationVariant, ...],
    *,
    max_concurrency: int = 1,
    timeout: float = DEFAULT_CASE_TIMEOUT,
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
