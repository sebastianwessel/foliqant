"""Isolated steps use the same bounded runtime without upstream model requests."""

import asyncio
from pathlib import Path

import pytest
import yaml

from foliqant.adapters.validation import WorkflowSchemas
from foliqant.bootstrap import WorkflowApplication
from foliqant.compiler import compile_workflow
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome, TokenUsage
from foliqant.core.identity import Identity
from foliqant.core.json import freeze_json
from foliqant.core.runner import ExecutionLimits, WorkflowRunner


class Scripted:
    def __init__(self, execute):
        self.execute = execute


def _plan(tmp_path: Path, steps: list[tuple[str, str]], *, review_route=False):
    flow = {
        "input": {},
        "definition": {
            "steps": [{"id": name, "definition": yaml.safe_load(body)} for name, body in steps]
        },
        "transition": {"outcome": "completed"},
    }
    flows = {"main": flow}
    if review_route:
        flow["on_unresolved"] = {"flow": "review"}
        flows["review"] = {
            "input": {},
            "transition": {"outcome": "needs_review"},
            "definition": {
                "steps": [
                    {
                        "id": "review",
                        "definition": {
                            "type": "handler",
                            "handler": "echo",
                            "input": {},
                        },
                    }
                ]
            },
        }
    (tmp_path / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "inbox",
                "start": "main",
                "flows": flows,
            }
        )
    )
    return compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names={"echo"})


def _runner(plan, execute, *, limits=None):
    return WorkflowRunner(
        plan,
        executor=Scripted(execute),
        validator=WorkflowSchemas(plan),
        admission=CapacityLimiter(concurrency=4, queue_limit=0),
        limits=limits or ExecutionLimits(),
    )


async def test_step_isolation_uses_explicit_inputs_and_returns_measurements(tmp_path: Path):
    plan = _plan(
        tmp_path,
        [
            ("first", "type: handler\nhandler: echo\ninput: {}\n"),
            (
                "second",
                "type: handler\nhandler: echo\ninput: {message: {pointer: /steps/first/result}}\n",
            ),
        ],
    )
    calls = []

    async def execute(step, inputs, context):
        calls.append((context.flow_id, step.name))
        assert inputs["message"] == "resolved golden input"
        ticket = await context.budget.start_model_request("test-model")
        await context.budget.finish_model_request(ticket, TokenUsage(12, 3))
        return StepOutcome(freeze_json({"label": "request_information"}))

    app = WorkflowApplication({"inbox": _runner(plan, execute)})
    result = await app.run_step(
        "inbox", "main", "second", Envelope(payload={"message": "resolved golden input"})
    )
    assert calls == [("main", "second")]
    assert list(result.flows) == ["main"]
    assert list(result.flows["main"].steps) == ["second"]
    assert result.payload == {"label": "request_information"}
    assert result.execution.revision == plan.revision
    assert result.flows["main"].steps["second"].elapsed_seconds >= 0
    assert result.flows["main"].steps["second"].usage.input_tokens == 12
    assert result.execution.usage.model_requests == 1
    with pytest.raises(ServiceError) as error:
        await app.run_step("inbox", "main", "second", Envelope(payload={"wrong": 1}))
    assert error.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(ServiceError) as error:
        await app.run_step("inbox", "main", "missing", Envelope(payload={}))
    assert error.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(ServiceError) as error:
        await app.run_step("inbox", "missing", "second", Envelope(payload={}))
    assert error.value.code == ErrorCode.NOT_FOUND
    assert calls == [("main", "second")]
    for invalid in (None, "", 12):
        with pytest.raises(ServiceError) as error:
            await app.run_step("inbox", "main", invalid, Envelope(payload={}))
        assert error.value.code == ErrorCode.INVALID_INPUT
        with pytest.raises(ServiceError) as error:
            await app.run_step("inbox", invalid, "second", Envelope(payload={}))
        assert error.value.code == ErrorCode.INVALID_INPUT
    assert calls == [("main", "second")]
    await app.aclose()
    with pytest.raises(ServiceError):
        await app.run_step("inbox", "main", "second", Envelope(payload={"message": "x"}))


async def test_failed_step_keeps_measured_attempts_and_skipped_steps_unknown(tmp_path: Path):
    plan = _plan(
        tmp_path,
        [
            ("first", "type: handler\nhandler: echo\ninput: {}\n"),
            ("second", "type: handler\nhandler: echo\ninput: {}\n"),
        ],
    )

    async def execute(step, inputs, context):
        await context.budget.start_model_request("test-model")
        raise ServiceError(ErrorCode.INVALID_OUTPUT)

    app = WorkflowApplication({"inbox": _runner(plan, execute)})
    result = await app.run("inbox", Envelope(payload={}))
    measured = result.flows["main"].steps["first"]
    assert measured.status == "failed" and measured.elapsed_seconds >= 0
    assert measured.usage.model_requests == 1
    assert measured.usage.input_tokens is None
    assert result.flows["main"].steps["second"].elapsed_seconds is None
    assert result.flows["main"].steps["second"].usage is None
    await app.aclose()


async def test_isolated_review_does_not_follow_route(tmp_path: Path):
    plan = _plan(
        tmp_path,
        [
            ("first", "type: handler\nhandler: echo\ninput: {}\n"),
        ],
        review_route=True,
    )
    calls = []

    async def execute(step, inputs, context):
        calls.append((context.flow_id, step.name))
        return StepOutcome(None, needs_review=True)

    app = WorkflowApplication({"inbox": _runner(plan, execute)})
    result = await app.run_step("inbox", "main", "first", Envelope(payload={}))
    assert result.execution.status == "needs_review"
    assert list(result.flows) == ["main"]
    assert list(result.flows["main"].steps) == ["first"]
    assert result.transitions == []
    assert calls == [("main", "first")]
    assert result.payload is None
    await app.aclose()


async def test_isolated_step_deadline_is_enforced(tmp_path: Path):
    plan = _plan(
        tmp_path,
        [
            ("first", "type: handler\nhandler: echo\ninput: {}\n"),
        ],
    )

    async def execute(step, inputs, context):
        await asyncio.Event().wait()
        return StepOutcome(None)

    configured = _runner(plan, execute, limits=ExecutionLimits(run_timeout=0.01))
    result = await configured.run_step(
        "main", "first", accept_envelope(Envelope(payload={}), Identity()), identity=Identity()
    )
    assert result.status == "failed" and result.error.code == ErrorCode.RUN_TIMEOUT
    assert dict(dict(result.flows)["main"].steps)["first"].elapsed_seconds >= 0.01
