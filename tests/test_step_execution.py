"""Isolated steps use the same bounded runtime without upstream model requests."""

import asyncio
from pathlib import Path

import pytest
from test_runner import Scripted, make_plan, runner

from foliqant.bootstrap import WorkflowApplication
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome, TokenUsage
from foliqant.core.identity import Identity
from foliqant.core.json import freeze_json
from foliqant.core.runner import ExecutionLimits


async def test_step_isolation_uses_explicit_inputs_and_returns_measurements(tmp_path: Path):
    plan = make_plan(
        tmp_path,
        {
            "first": "type: handler\nhandler: echo\ninput: {}\nnext: second\n",
            "second": (
                "type: handler\nhandler: echo\n"
                "input: {message: {pointer: /steps/first/result}}\nnext: done\n"
            ),
            "done": "type: finish\noutcome: completed\n",
        },
    )
    calls = []

    async def execute(step, inputs, context):
        calls.append(step.name)
        assert inputs["message"] == "resolved golden input"
        ticket = await context.budget.start_model_request()
        await context.budget.finish_model_request(ticket, TokenUsage(12, 3))
        return StepOutcome(freeze_json({"label": "request_information"}))

    app = WorkflowApplication({"inbox": runner(plan, Scripted(execute))})
    result = await app.run_step(
        "inbox", "second", Envelope(payload={"message": "resolved golden input"})
    )
    assert calls == ["second"]
    assert list(result.decisions) == ["second"]
    assert result.payload == {"label": "request_information"}
    assert result.execution.revision == plan.revision
    assert result.decisions["second"].elapsed_seconds >= 0
    assert result.decisions["second"].usage.input_tokens == 12
    assert result.execution.usage.model_requests == 1
    with pytest.raises(ServiceError) as error:
        await app.run_step("inbox", "second", Envelope(payload={"wrong": 1}))
    assert error.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(ServiceError) as error:
        await app.run_step("inbox", "missing", Envelope(payload={}))
    assert error.value.code == ErrorCode.NOT_FOUND
    assert calls == ["second"]
    for invalid in (None, "", 12):
        with pytest.raises(ServiceError) as error:
            await app.run_step("inbox", invalid, Envelope(payload={}))
        assert error.value.code == ErrorCode.INVALID_INPUT
    assert calls == ["second"]
    await app.aclose()
    with pytest.raises(ServiceError):
        await app.run_step("inbox", "second", Envelope(payload={"message": "x"}))


async def test_failed_step_keeps_measured_attempts_and_skipped_steps_unknown(tmp_path: Path):
    plan = make_plan(
        tmp_path,
        {
            "first": "type: handler\nhandler: echo\ninput: {}\nnext: done\n",
            "done": "type: finish\noutcome: completed\n",
        },
    )

    async def execute(step, inputs, context):
        await context.budget.start_model_request()
        raise ServiceError(ErrorCode.INVALID_OUTPUT)

    app = WorkflowApplication({"inbox": runner(plan, Scripted(execute))})
    result = await app.run("inbox", Envelope(payload={}))
    measured = result.decisions["first"]
    assert measured.status == "failed" and measured.elapsed_seconds >= 0
    assert measured.usage.model_requests == 1
    assert measured.usage.input_tokens is None
    assert result.decisions["done"].elapsed_seconds is None
    assert result.decisions["done"].usage is None


async def test_isolated_review_does_not_follow_route(tmp_path: Path):
    plan = make_plan(
        tmp_path,
        {
            "first": "type: handler\nhandler: echo\ninput: {}\nnext: done\non_unresolved: done\n",
            "done": "type: finish\noutcome: completed\n",
        },
    )

    async def execute(step, inputs, context):
        return StepOutcome(None, needs_review=True)

    app = WorkflowApplication({"inbox": runner(plan, Scripted(execute))})
    result = await app.run_step("inbox", "first", Envelope(payload={}))
    assert result.execution.status == "needs_review"
    assert list(result.decisions) == ["first"]
    assert result.payload is None


async def test_isolated_step_deadline_is_enforced(tmp_path: Path):
    plan = make_plan(
        tmp_path,
        {
            "first": "type: handler\nhandler: echo\ninput: {}\nnext: done\n",
            "done": "type: finish\noutcome: completed\n",
        },
    )

    async def execute(step, inputs, context):
        await asyncio.Event().wait()
        return StepOutcome(None)

    configured = runner(plan, Scripted(execute), limits=ExecutionLimits(run_timeout=0.01))
    result = await configured.run_step(
        "first", accept_envelope(Envelope(payload={}), Identity()), identity=Identity()
    )
    assert result.status == "failed" and result.error.code == ErrorCode.TIMEOUT
    assert dict(result.decisions)["first"].elapsed_seconds >= 0.01
