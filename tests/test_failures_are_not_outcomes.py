"""A technical failure fails the run with its code; it never becomes a business outcome.

No route, review path, fallback category, default, repeat decision or retry flow
acts on a failed step. Native abstention remains the only unresolved outcome.
"""

from typing import Any

import pytest
from test_conditional_runtime import Handlers, _found_on, _repeat_plan, _run
from test_model_executor import _context
from workflow_documents import compile_document, flow, handler, step

from foliqant.adapters.handlers import HandlerExecutor, HandlerRegistration
from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import Failure, FlowRecord, RunResult, StepOutcome, StepRecord, Usage
from foliqant.core.json import FrozenObject, freeze_json
from foliqant.core.plan import HandlerStepPlan, SourceLocation
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    evaluate,
)
from foliqant.ports.execution import StepContext


def _raising(code: ErrorCode, *, retryable: bool = False) -> Any:
    async def respond(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        raise ServiceError(code, retryable=retryable)

    return respond


async def test_failed_step_never_takes_the_unresolved_route(tmp_path) -> None:
    plan = compile_document(
        tmp_path,
        {
            "classify": flow(
                step("classify", handler("check")),
                transition={"flow": "specialist"},
                on_unresolved={"flow": "manual"},
            ),
            "specialist": flow(step("s", handler())),
            "manual": flow(step("m", handler()), on_unresolved={"outcome": "needs_review"}),
        },
    )
    executor = Handlers(check=_raising(ErrorCode.DEPENDENCY_OVERLOADED, retryable=True))
    result, public = await _run(plan, executor, {})
    assert result.status == "failed"
    assert public["execution"]["error"] == {
        "code": "dependency_overloaded",
        "message": str(ServiceError(ErrorCode.DEPENDENCY_OVERLOADED)),
        "retryable": True,
    }
    assert public["transitions"] == []
    assert public["flows"]["manual"]["status"] == "skipped"
    assert public["flows"]["specialist"]["status"] == "skipped"
    assert [call[0] for call in executor.calls] == ["classify"]


@pytest.mark.parametrize(
    "code", [ErrorCode.OUTPUT_LIMIT_REACHED, ErrorCode.REQUEST_TIMEOUT, ErrorCode.TOOL_ERROR]
)
async def test_failed_attempt_stops_a_repeat_before_until_retry_flow_and_routes(
    tmp_path, code
) -> None:
    plan = _repeat_plan(tmp_path)
    executor = Handlers(lookup=_raising(code), correct=_found_on("never"))
    result, public = await _run(plan, executor, {"identifier": "LU1"})
    assert result.status == "failed" and result.error == Failure(code)
    lookup = public["flows"]["lookup_fund"]
    assert lookup["repeat"] == {"stopped_by": "failure"} and lookup["attempt_count"] == 1
    # Neither the retry flow nor the "not found" route nor a review path ran.
    assert [call[0] for call in executor.calls] == ["lookup_fund"]
    for other in ("correct", "manual", "escalate", "enrich"):
        assert public["flows"][other]["status"] == "skipped"
    assert public["transitions"] == []


async def test_handler_exception_is_handler_failed_never_review() -> None:
    async def broken(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        raise KeyError("PRIVATE handler detail")

    schema = freeze_json({"type": "object"})
    executor = HandlerExecutor(
        {"broken": HandlerRegistration(broken, input_schema=schema, output_schema=schema)}
    )
    handler_step = HandlerStepPlan(
        "work", "handler", SourceLocation("steps/work.yaml", 1, 1), handler="broken"
    )
    with pytest.raises(ServiceError) as raised:
        await executor.execute(handler_step, freeze_json({}), _context("work"))  # type: ignore[arg-type]
    assert raised.value.code is ErrorCode.HANDLER_FAILED and not raised.value.retryable
    assert "PRIVATE" not in str(raised.value)


async def test_failed_case_is_an_evaluation_error_never_a_wrong_answer() -> None:
    failure = Failure(ErrorCode.CONTEXT_LIMIT_EXCEEDED)

    async def run(envelope: Envelope) -> ExecutionResult:
        return to_execution_result(
            RunResult(
                "failed-case",
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
                            (("classify", StepRecord("failed", error=failure)),),
                            error=failure,
                        ),
                    ),
                ),
                Usage(),
                failure,
            )
        )

    suite = EvaluationSuite(
        "synthetic",
        "gold-v1",
        (
            EvaluationCase(
                "long-thread",
                Envelope(payload={"message": "..."}),
                (Expectation("label", "/flows/main/steps/classify/result/label", "billing"),),
            ),
        ),
    )
    report = await evaluate(
        suite, EvaluationVariant("baseline", "prompt-v1", run, "configuration-v1")
    )
    (case,) = report.cases
    assert case.status == "failed" and case.error_code == "context_limit_exceeded"
    # The unavailable answer is an execution error, not a mismatch against the gold.
    assert [check.outcome for check in case.checks] == ["error"] and not case.passed
    assert report.failure_rate == 1.0
    assert dict(report.failures_by_code) == {"context_limit_exceeded": 1}


async def test_failure_reasons_are_public_and_counted_by_the_evaluation() -> None:
    failure = Failure(
        ErrorCode.INVALID_OUTPUT,
        reason="schema_violation",
        location="/units/0/intent",
        constraint="enum",
    )

    async def run(envelope: Envelope) -> ExecutionResult:
        return to_execution_result(
            RunResult(
                "failed-case",
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
                            (("classify", StepRecord("failed", error=failure)),),
                            error=failure,
                        ),
                    ),
                ),
                Usage(),
                failure,
            )
        )

    result = await run(Envelope(payload={}))
    assert result.model_dump(mode="json")["execution"]["error"] == {
        "code": "invalid_output",
        "message": str(ServiceError(ErrorCode.INVALID_OUTPUT)),
        "retryable": False,
        "reason": "schema_violation",
        "location": "/units/0/intent",
        "constraint": "enum",
    }
    suite = EvaluationSuite(
        "synthetic",
        "gold-v1",
        (
            EvaluationCase(
                "one",
                Envelope(payload={}),
                (Expectation("status", "/execution/status", "completed"),),
            ),
        ),
    )
    report = await evaluate(suite, EvaluationVariant("baseline", "p1", run, "c1"))
    assert dict(report.failures_by_reason) == {"schema_violation": 1}
    (step,) = report.steps
    assert dict(step.failures_by_reason) == {"schema_violation": 1}
    assert report.to_dict()["cases"][0]["error_reason"] == "schema_violation"


def test_unsafe_explanations_are_dropped_never_reported() -> None:
    failure = Failure(
        ErrorCode.INVALID_OUTPUT, location="/PRIVATE value with spaces", constraint="Not Safe"
    )
    assert failure.location is None and failure.constraint is None
    error = ServiceError(ErrorCode.INVALID_OUTPUT, reason="free text")  # type: ignore[arg-type]
    assert error.reason is None
