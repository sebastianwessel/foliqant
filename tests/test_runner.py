"""Compiler-to-runner tests use deterministic adapters and no external endpoints."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from foliqant.compiler import compile_workflow
from foliqant.compiler._loader import load_yaml
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.core.admission import CapacityLimiter
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome, TokenUsage
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenJson, FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import WorkflowPlan
from foliqant.core.runner import ExecutionLimits, WorkflowRunner
from foliqant.ports.execution import OperationStep, StepContext


class NoSchema:
    def validate_input(self, payload: FrozenJson) -> None:
        pass

    def validate_flow_input(self, flow_id: str, payload: FrozenJson) -> None:
        pass


class Scripted:
    def __init__(
        self, handler: Callable[[OperationStep, FrozenObject, StepContext], Awaitable[StepOutcome]]
    ) -> None:
        self.handler = handler

    async def execute(
        self, step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        return await self.handler(step, inputs, context)


def make_plan(
    tmp_path: Path,
    steps: dict[str, str],
    *,
    output=None,
    transition=None,
    on_unresolved=None,
    additional_flows=None,
    flow_input=None,
) -> WorkflowPlan:
    flow = {
        "input": flow_input
        if flow_input is not None
        else {"message": {"pointer": "/payload/message", "optional": True, "default": None}},
        "definition": {
            "steps": [
                {"id": name, "definition": load_yaml(body, relative_path="workflow.yaml")}
                for name, body in steps.items()
            ]
        },
        "transition": transition or {"outcome": "completed"},
    }
    if output is not None:
        flow["definition"]["output"] = output
    if on_unresolved is not None:
        flow["on_unresolved"] = on_unresolved
    document = {
        "name": "inbox",
        "start": "main",
        "defaults": {"model": "local"},
        "flows": {"main": flow, **(additional_flows or {})},
    }
    (tmp_path / "workflow.yaml").write_text(json.dumps(document))
    return compile_workflow(
        tmp_path, model_aliases={"local": "test-model"}, tool_catalogs={}, handler_names={"echo"}
    )


def branch_flow(outcome="completed"):
    return {
        "input": {},
        "definition": {
            "steps": [
                {"id": "record", "definition": {"type": "handler", "handler": "echo", "input": {}}}
            ]
        },
        "transition": {"outcome": outcome},
    }


def accepted() -> AcceptedEnvelope:
    return accept_envelope(
        Envelope.model_validate(
            {"payload": {"message": "Bitte helfen."}, "metadata": {"source": "email"}}
        ),
        Identity(),
    )


def runner(
    plan: WorkflowPlan, executor: Scripted, *, limits: ExecutionLimits | None = None
) -> WorkflowRunner:
    return WorkflowRunner(
        plan,
        executor=executor,
        validator=NoSchema(),
        admission=CapacityLimiter(concurrency=4, queue_limit=0),
        limits=limits or ExecutionLimits(),
    )


_HANDLER = "type: handler\nhandler: echo\ninput: {message: {pointer: /payload/message}}\n"
_HANDLER_WORKFLOW = {
    "first": _HANDLER,
}
_DECISION = """type: decision
instructions: Does the message request help?
sources: {message: {pointer: /payload/message}}
question: {type: predicate, criteria: [Use explicit statements.]}
"""


async def test_compiled_workflow_binds_prior_outputs_and_preserves_input(tmp_path: Path) -> None:
    plan = make_plan(
        tmp_path,
        {
            "first": _HANDLER,
            "second": (
                "type: handler\nhandler: echo\ninput: {previous: {pointer: /steps/first/result}}\n"
            ),
        },
    )
    seen: list[str] = []

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        seen.append(step.name)
        assert context.step_id == step.name
        assert context.caller.identity == Identity()
        if step.name == "first":
            assert inputs["message"] == "Bitte helfen."
            return StepOutcome(freeze_json({"route": "support"}))
        assert thaw_json(inputs["previous"]) == {"route": "support"}
        return StepOutcome(None)

    envelope = accepted()
    result = await runner(plan, Scripted(handle)).run(envelope, identity=Identity())
    assert result.status == "completed"
    assert result.payload == envelope.payload
    assert result.metadata == envelope.metadata
    assert seen == ["first", "second"]
    records = dict(dict(result.flows)["main"].steps)
    assert records["second"].has_result and records["second"].result is None
    assert result.usage.model_requests == result.usage.tool_calls == 0
    assert result.usage.tokens == TokenUsage.zero()


async def test_uncertainty_stops_local_sequence_before_successful_dispatch(tmp_path: Path) -> None:
    plan = make_plan(
        tmp_path,
        {"first": _DECISION, "later": _HANDLER},
        transition={"flow": "specialist"},
        additional_flows={"specialist": branch_flow()},
    )

    async def handle(step, inputs, context):
        assert step.name == "first"
        return StepOutcome(freeze_json({"answerability": "undetermined"}), needs_review=True)

    result = await runner(plan, Scripted(handle)).run(accepted(), identity=Identity())
    assert result.status == "needs_review"
    records = dict(dict(result.flows)["main"].steps)
    assert records["first"].has_result and records["first"].status == "needs_review"
    assert records["later"].status == "skipped" and not records["later"].has_result
    assert dict(result.flows)["specialist"].status == "skipped"


@pytest.mark.parametrize("route,selected", [("true", "yes"), ("false", "no")])
async def test_routes_only_to_configured_flow_target(
    tmp_path: Path, route: str, selected: str
) -> None:
    plan = make_plan(
        tmp_path,
        {"first": _DECISION},
        output={"pointer": "/steps/first/result"},
        transition={
            "binding": {"pointer": "/flows/main/result"},
            "cases": {"true": {"flow": "yes"}, "false": {"flow": "no"}},
            "default": {"outcome": "needs_review"},
        },
        additional_flows={"yes": branch_flow(), "no": branch_flow()},
    )

    async def handle(step, inputs, context):
        if context.flow_id == "main":
            ticket = await context.budget.start_model_request()
            await context.budget.finish_model_request(ticket, TokenUsage(10, 5))
            return StepOutcome(route)
        assert context.flow_id == selected
        return StepOutcome(None)

    result = await runner(plan, Scripted(handle)).run(accepted(), identity=Identity())
    assert result.status == "completed"
    assert dict(result.flows)[selected].status == "completed"
    assert dict(result.flows)["no" if selected == "yes" else "yes"].status == "skipped"
    assert result.usage.model_requests == 1
    assert result.usage.tokens.input_tokens == 10


async def test_execution_error_is_safe_and_failed_attempts_remain_counted(tmp_path: Path) -> None:
    plan = make_plan(tmp_path, {"first": _HANDLER, "later": _HANDLER})

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        await context.budget.start_model_request()
        raise RuntimeError("PRIVATE-USER-CONTENT")

    result = await runner(plan, Scripted(handle)).run(accepted(), identity=Identity())
    assert result.status == "failed"
    assert result.error is not None and result.error.code == ErrorCode.DEPENDENCY_FAILURE
    assert "PRIVATE" not in result.error.message
    assert dict(dict(result.flows)["main"].steps)["later"].status == "skipped"
    assert result.usage.model_requests == 1 and result.usage.tokens == TokenUsage()


async def test_projection_failure_preserves_completed_step_and_original_payload(
    tmp_path: Path,
) -> None:
    plan = make_plan(tmp_path, _HANDLER_WORKFLOW, output={"pointer": "/steps/first/result/missing"})

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        return StepOutcome(freeze_json({"present": 1}))

    envelope = accepted()
    result = await runner(plan, Scripted(handle)).run(envelope, identity=Identity())
    assert result.status == "failed" and result.error is not None
    assert result.error.code == ErrorCode.MISSING_BINDING
    assert result.payload == envelope.payload
    assert dict(dict(result.flows)["main"].steps)["first"].status == "completed"


async def test_cancellation_releases_capacity_without_detached_work(tmp_path: Path) -> None:
    plan = make_plan(tmp_path, _HANDLER_WORKFLOW)
    entered = asyncio.Event()
    stopped = asyncio.Event()
    limiter = CapacityLimiter(concurrency=1, queue_limit=0)

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("never reached")

    engine = WorkflowRunner(
        plan, executor=Scripted(handle), validator=NoSchema(), admission=limiter
    )
    task = asyncio.create_task(engine.run(accepted(), identity=Identity()))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert limiter.active == limiter.waiting == 0


async def test_deadline_stops_work_and_returns_typed_failure(tmp_path: Path) -> None:
    plan = make_plan(tmp_path, _HANDLER_WORKFLOW)
    stopped = asyncio.Event()

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("never reached")

    result = await runner(plan, Scripted(handle)).run(
        accepted(),
        identity=Identity(),
        deadline=asyncio.get_running_loop().time() + 0.02,
    )
    assert stopped.is_set()
    assert result.status == "failed" and result.error is not None
    assert result.error.code == ErrorCode.TIMEOUT
    assert dict(dict(result.flows)["main"].steps)["first"].status == "failed"


async def test_two_runs_overlap_with_identity_metadata_result_and_budget_isolation(
    tmp_path: Path,
) -> None:
    plan = make_plan(tmp_path, _HANDLER_WORKFLOW)
    both_entered = asyncio.Event()
    entered = 0

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), 1)
        await context.budget.start_tool_call()
        assert context.caller.metadata["tenant_id"] == context.caller.identity.tenant_id
        return StepOutcome(inputs["message"])

    engine = runner(plan, Scripted(handle))

    async def run_one(name: str):
        identity = Identity(tenant_id=name, principal_id=f"user-{name}")
        envelope = accept_envelope(Envelope(payload={"message": name}), identity)
        return await engine.run(envelope, identity=identity)

    async with asyncio.TaskGroup() as group:
        first = group.create_task(run_one("first"))
        second = group.create_task(run_one("second"))
    assert first.result().execution_id != second.result().execution_id
    for task, name in [(first, "first"), (second, "second")]:
        result = task.result()
        assert result.metadata["tenant_id"] == name
        assert dict(dict(result.flows)["main"].steps)["first"].result == name
        assert result.usage.tool_calls == 1


async def test_unverified_metadata_rejected_before_adapter_call(tmp_path: Path) -> None:
    plan = make_plan(tmp_path, _HANDLER_WORKFLOW)

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        pytest.fail("forged identity must never reach a handler")

    engine = runner(plan, Scripted(handle))
    with pytest.raises(ServiceError) as error:
        await engine.run(
            AcceptedEnvelope(payload={}, metadata={"tenant_id": "other"}), identity=Identity()
        )
    assert error.value.code == ErrorCode.FORBIDDEN


async def test_validator_exception_does_not_expose_content(tmp_path: Path) -> None:
    plan = make_plan(tmp_path, _HANDLER_WORKFLOW)

    class FailingValidator:
        def validate_input(self, payload: FrozenJson) -> None:
            raise RuntimeError("PRIVATE_SENTINEL")

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        pytest.fail("invalid input cannot reach a handler")

    engine = WorkflowRunner(
        plan,
        executor=Scripted(handle),
        validator=FailingValidator(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
    )
    with pytest.raises(ServiceError) as error:
        await engine.run(accepted(), identity=Identity())
    assert error.value.code == ErrorCode.DEPENDENCY_FAILURE
    assert "PRIVATE" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("claim", ["a\nb", "x" * 257, None])
async def test_direct_embedded_metadata_cannot_bypass_identity_constraints(
    tmp_path: Path, claim: object
) -> None:
    plan = make_plan(tmp_path, _HANDLER_WORKFLOW)

    async def handle(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        pytest.fail("invalid identity cannot reach a handler")

    with pytest.raises(ServiceError) as error:
        await runner(plan, Scripted(handle)).run(
            AcceptedEnvelope(payload={}, metadata={"tenant_id": claim}),
            identity=Identity(),
        )
    assert error.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize(
    "route,status", [(None, "needs_review"), ("unexpected", "needs_review"), ([], "failed")]
)
async def test_unmatched_routes_review_and_invalid_types_fail_safely(
    tmp_path: Path, route, status
) -> None:
    plan = make_plan(
        tmp_path,
        {"first": _DECISION},
        output={"pointer": "/steps/first/result"},
        transition={
            "binding": {"pointer": "/flows/main/result"},
            "cases": {"true": {"flow": "specialist"}},
            "default": {"outcome": "needs_review"},
        },
        additional_flows={"specialist": branch_flow()},
    )

    async def handle(step, inputs, context):
        return StepOutcome(freeze_json(route))

    envelope = accepted()
    result = await runner(plan, Scripted(handle)).run(envelope, identity=Identity())
    assert result.status == status
    if status == "failed":
        assert result.error is not None and result.error.code == ErrorCode.INVALID_INPUT
    assert result.payload == envelope.payload
    assert dict(dict(result.flows)["main"].steps)["first"].status == "completed"
    assert dict(result.flows)["specialist"].status == "skipped"
