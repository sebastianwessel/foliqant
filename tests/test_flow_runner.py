"""Execution invariants for workflow boundaries and sequential flow scopes."""

import asyncio

import pytest

from foliqant.core.admission import CapacityLimiter
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ErrorCode
from foliqant.core.execution import StepOutcome
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenJson, FrozenObject, freeze_json
from foliqant.core.plan import (
    BindingPlan,
    FlowPlan,
    HandlerStepPlan,
    SourceLocation,
    TransitionTargetPlan,
    WorkflowPlan,
)
from foliqant.core.runner import ExecutionLimits, WorkflowRunner
from foliqant.ports.execution import OperationStep, StepContext

LOCATION = SourceLocation("workflow.yaml", 1, 1)


class Validator:
    def validate_input(self, payload: FrozenJson) -> None:
        pass

    def validate_flow_input(self, flow_id: str, payload: FrozenJson) -> None:
        pass


def pointer(path: str) -> BindingPlan:
    return BindingPlan(kind="pointer", pointer=path)


def flow(name: str, *, next_flow: str | None = None, steps: int = 1) -> FlowPlan:
    return FlowPlan(
        name=name,
        input=(("message", pointer("/payload/message")),),
        steps=tuple(
            HandlerStepPlan(
                name=f"step_{index}",
                type="handler",
                location=LOCATION,
                handler="echo",
                input=(("message", pointer("/payload/message")),),
            )
            for index in range(steps)
        ),
        input_schema_path=None,
        input_schema=None,
        output=pointer("/steps/step_0/result"),
        transition=TransitionTargetPlan(flow=next_flow)
        if next_flow
        else TransitionTargetPlan(outcome="completed"),
        on_unresolved=None,
        location=LOCATION,
    )


def plan(*flows: FlowPlan) -> WorkflowPlan:
    return WorkflowPlan(
        name="inbox",
        revision="test-revision",
        start=flows[0].name,
        default_model=None,
        input_schema_path=None,
        input_schema=None,
        schema_resources=(),
        output=None,
        flows=flows,
        location=LOCATION,
    )


def envelope() -> AcceptedEnvelope:
    return AcceptedEnvelope(payload={"message": "Bitte helfen."}, metadata={})


class Executor:
    def __init__(self, *, review: bool = False, delay: float = 0) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.review, self.delay = review, delay

    async def execute(
        self, step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        self.calls.append((context.execution_id, context.flow_id, context.step_id))
        if self.delay:
            await asyncio.sleep(self.delay)
        assert inputs["message"] == "Bitte helfen."
        return StepOutcome(freeze_json({"text": inputs["message"]}), needs_review=self.review)


def runner(value: WorkflowPlan, executor: Executor, *, max_steps: int = 10) -> WorkflowRunner:
    return WorkflowRunner(
        value,
        executor=executor,
        validator=Validator(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        limits=ExecutionLimits(max_steps=max_steps),
    )


async def test_two_flows_share_execution_and_one_admission_slot() -> None:
    executor = Executor()
    result = await runner(
        plan(flow("triage", next_flow="specialist"), flow("specialist")), executor
    ).run(envelope(), identity=Identity())
    assert result.status == "completed"
    assert [call[1:] for call in executor.calls] == [("triage", "step_0"), ("specialist", "step_0")]
    assert len({call[0] for call in executor.calls}) == 1
    assert [entry.flow for entry in result.transitions] == ["specialist", None]
    assert all(record.status == "completed" for _, record in result.flows)


async def test_review_stops_sequence_and_never_dispatches_success_route() -> None:
    executor = Executor(review=True)
    result = await runner(
        plan(flow("triage", next_flow="specialist", steps=2), flow("specialist")), executor
    ).run(envelope(), identity=Identity())
    assert result.status == "needs_review"
    assert len(executor.calls) == 1
    records = dict(result.flows)
    assert records["triage"].status == "needs_review"
    assert dict(records["triage"].steps)["step_1"].status == "skipped"
    assert records["specialist"].status == "skipped"
    assert result.transitions[0].outcome == "needs_review"


async def test_root_step_budget_does_not_reset_at_flow_boundary() -> None:
    executor = Executor()
    result = await runner(
        plan(flow("triage", next_flow="specialist"), flow("specialist")), executor, max_steps=1
    ).run(envelope(), identity=Identity())
    assert result.status == "failed"
    assert result.error is not None and result.error.code == ErrorCode.BUDGET_EXHAUSTED
    assert len(executor.calls) == 1


async def test_isolated_flow_bypasses_boundary_dispatch() -> None:
    executor = Executor()
    result = await runner(
        plan(flow("triage", next_flow="specialist"), flow("specialist")), executor
    ).run_flow("triage", envelope(), identity=Identity())
    assert result.status == "completed"
    assert len(executor.calls) == 1
    assert not result.transitions
    assert result.payload == freeze_json({"text": "Bitte helfen."})


async def test_cancellation_propagates_without_dispatching_next_flow() -> None:
    executor = Executor(delay=10)
    task = asyncio.create_task(
        runner(plan(flow("triage", next_flow="specialist"), flow("specialist")), executor).run(
            envelope(), identity=Identity()
        )
    )
    while not executor.calls:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(executor.calls) == 1


async def test_routing_uses_exact_flow_result_and_never_coerces() -> None:
    from dataclasses import replace

    from foliqant.core.plan import MatchRoutingPlan

    class RouteExecutor(Executor):
        async def execute(
            self, step: OperationStep, inputs: FrozenObject, context: StepContext
        ) -> StepOutcome:
            self.calls.append((context.execution_id, context.flow_id, context.step_id))
            return StepOutcome("billing" if context.flow_id == "triage" else "handled")

    first = replace(
        flow("triage"),
        transition=MatchRoutingPlan(
            pointer("/flows/triage/result"),
            (("billing", TransitionTargetPlan(flow="billing")),),
            TransitionTargetPlan(outcome="needs_review"),
        ),
    )
    executor = RouteExecutor()
    result = await runner(plan(first, flow("billing")), executor).run(
        envelope(), identity=Identity()
    )
    assert result.status == "completed"
    assert [call[1] for call in executor.calls] == ["triage", "billing"]


async def test_unresolved_review_flow_preserves_original_assessment() -> None:
    from dataclasses import replace

    class ReviewExecutor(Executor):
        async def execute(
            self, step: OperationStep, inputs: FrozenObject, context: StepContext
        ) -> StepOutcome:
            self.calls.append((context.execution_id, context.flow_id, context.step_id))
            if context.flow_id == "triage":
                return StepOutcome("unclear", needs_review=True)
            assert inputs["message"] == "unclear"
            return StepOutcome("review prepared")

    first = replace(flow("triage"), on_unresolved=TransitionTargetPlan(flow="review"))
    second = replace(flow("review"), input=(("message", pointer("/flows/triage/result")),))
    executor = ReviewExecutor()
    result = await runner(plan(first, second), executor).run(envelope(), identity=Identity())
    assert result.status == "completed"
    assert dict(result.flows)["triage"].status == "needs_review"
    assert dict(result.flows)["triage"].result == "unclear"
    assert result.transitions[0].reason == "needs_review"


async def test_early_review_can_project_explicit_missing_default() -> None:
    from dataclasses import replace

    first = replace(
        flow("triage", steps=2),
        output=BindingPlan(
            kind="pointer",
            pointer="/steps/step_1/result",
            has_default=True,
            default=None,
        ),
    )
    result = await runner(plan(first), Executor(review=True)).run(envelope(), identity=Identity())
    assert result.status == "needs_review"
    assert dict(result.flows)["triage"].has_result
    assert dict(result.flows)["triage"].result is None
    assert dict(dict(result.flows)["triage"].steps)["step_0"].has_result


async def test_deadline_stops_before_later_flow() -> None:
    executor = Executor(delay=0.03)
    result = await runner(
        plan(flow("triage", next_flow="specialist"), flow("specialist")), executor
    ).run(envelope(), identity=Identity(), deadline=asyncio.get_running_loop().time() + 0.005)
    assert result.status == "failed"
    assert result.error is not None and result.error.code == ErrorCode.TIMEOUT
    assert len(executor.calls) == 1
    assert dict(result.flows)["triage"].status == "failed"
    assert dict(dict(result.flows)["triage"].steps)["step_0"].status == "failed"


async def test_usage_counted_once_across_flow_subtotals() -> None:
    from foliqant.core.execution import TokenUsage

    class MeteredExecutor(Executor):
        async def execute(
            self, step: OperationStep, inputs: FrozenObject, context: StepContext
        ) -> StepOutcome:
            ticket = await context.budget.start_model_request("test-model")
            await context.budget.finish_model_request(ticket, TokenUsage(100, 20, 80, None, 10))
            return await super().execute(step, inputs, context)

    result = await runner(
        plan(flow("triage", next_flow="specialist"), flow("specialist")), MeteredExecutor()
    ).run(envelope(), identity=Identity())
    assert result.usage.model_requests == 2
    assert result.usage.tokens.input_tokens == 200
    assert result.usage.tokens.output_tokens == 40
    assert result.usage.tokens.reasoning_output_tokens == 20
    assert result.usage.tokens.cache_write_input_tokens is None
    assert all(
        record.usage is not None and record.usage.model_requests == 1 for _, record in result.flows
    )


async def test_internal_unknown_flow_target_returns_safe_configuration_failure() -> None:
    executor = Executor()
    result = await runner(plan(flow("triage", next_flow="missing"), flow("unused")), executor).run(
        envelope(), identity=Identity()
    )
    assert result.status == "failed"
    assert result.error is not None and result.error.code == ErrorCode.INVALID_CONFIGURATION
    assert [name for name, _ in result.flows] == ["triage", "unused"]
    assert dict(result.flows)["unused"].status == "skipped"
    assert dict(result.flows)["triage"].status == "completed"
    assert len(executor.calls) == 1


async def test_isolated_step_bypasses_upstream_flow_step_bindings_and_all_projections() -> None:
    from dataclasses import replace

    class FailOnValidation(Validator):
        def validate_input(self, payload: FrozenJson) -> None:
            pytest.fail("isolated step must not validate workflow input")

        def validate_flow_input(self, flow_id: str, payload: FrozenJson) -> None:
            pytest.fail("isolated step must not validate flow input")

    selected = replace(
        flow("specialist", steps=2),
        input=(("message", pointer("/flows/triage/result/absent")),),
        output=pointer("/steps/step_0/result/absent"),
    )
    value = replace(
        plan(flow("triage", next_flow="specialist"), selected),
        output=pointer("/flows/triage/result/absent"),
    )
    executor = Executor()
    engine = WorkflowRunner(
        value,
        executor=executor,
        validator=FailOnValidation(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
    )
    result = await engine.run_step("specialist", "step_1", envelope(), identity=Identity())
    assert result.status == "completed"
    assert [call[1:] for call in executor.calls] == [("specialist", "step_1")]
    assert [name for name, _ in result.flows] == ["specialist"]
    assert [name for name, _ in result.flows[0][1].steps] == ["step_1"]
    assert result.payload == freeze_json({"text": "Bitte helfen."})
    assert not result.transitions


async def test_isolated_flow_validates_only_selected_flow_input_once() -> None:
    from dataclasses import replace

    validations = []

    class RecordingValidator(Validator):
        def validate_input(self, payload: FrozenJson) -> None:
            pytest.fail("isolated flow must not validate workflow input")

        def validate_flow_input(self, flow_id: str, payload: FrozenJson) -> None:
            validations.append((flow_id, payload))

    selected = replace(
        flow("specialist"), input=(("message", pointer("/flows/triage/result/absent")),)
    )
    value = replace(
        plan(flow("triage", next_flow="specialist"), selected),
        output=pointer("/flows/triage/result/absent"),
    )
    executor = Executor()
    engine = WorkflowRunner(
        value,
        executor=executor,
        validator=RecordingValidator(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
    )
    result = await engine.run_flow("specialist", envelope(), identity=Identity())
    assert result.status == "completed"
    assert validations == [("specialist", envelope().payload)]
    assert [call[1:] for call in executor.calls] == [("specialist", "step_0")]
    assert not result.transitions


@pytest.mark.parametrize("payload", [{}, {"message": "ok", "ambient": "must not enter"}, "text"])
async def test_isolated_step_requires_exact_resolved_input_names(payload: object) -> None:
    from foliqant.core.errors import ServiceError

    executor = Executor()
    with pytest.raises(ServiceError) as caught:
        await runner(plan(flow("triage")), executor).run_step(
            "triage", "step_0", AcceptedEnvelope(payload=payload, metadata={}), identity=Identity()
        )
    assert caught.value.code == ErrorCode.INVALID_INPUT
    assert not executor.calls


async def test_flow_boundaries_preserve_identity_metadata_and_one_deadline() -> None:
    from dataclasses import replace

    contexts = []
    identity = Identity("tenant", "principal")
    metadata = freeze_json({"tenant_id": "tenant", "principal_id": "principal", "source": "email"})

    class ContextExecutor(Executor):
        async def execute(self, step, inputs, context):
            contexts.append(context)
            assert context.caller.identity is identity
            assert context.caller.metadata == metadata
            assert dict(inputs) == {"message": "Bitte helfen."}
            return await super().execute(step, inputs, context)

    # Only the declared projection crosses the boundary; internal first-flow
    # step records and caller identity do not appear in the second flow payload.
    second = replace(flow("specialist"), input=(("message", pointer("/flows/triage/result/text")),))
    deadline = asyncio.get_running_loop().time() + 10
    result = await runner(
        plan(flow("triage", next_flow="specialist"), second), ContextExecutor()
    ).run(
        AcceptedEnvelope(
            payload={"message": "Bitte helfen.", "secret": "not selected"}, metadata=metadata
        ),
        identity=identity,
        deadline=deadline,
    )
    assert result.status == "completed"
    assert [context.flow_id for context in contexts] == ["triage", "specialist"]
    assert len({context.execution_id for context in contexts}) == 1
    assert all(context.deadline == deadline for context in contexts)
    assert contexts[0].budget is not contexts[1].budget


async def test_each_step_has_own_attempt_limit_but_failure_usage_is_not_lost() -> None:
    from foliqant.core.execution import TokenUsage

    class BudgetedExecutor(Executor):
        async def execute(self, step, inputs, context):
            ticket = await context.budget.start_model_request("test-model")
            await context.budget.finish_model_request(ticket, TokenUsage(10, 2, 0, 0, 0))
            if context.flow_id == "specialist":
                await context.budget.start_model_request("test-model")
                pytest.fail("exhausted per-step budget must reject the next attempt")
            return await super().execute(step, inputs, context)

    engine = WorkflowRunner(
        plan(flow("triage", next_flow="specialist"), flow("specialist")),
        executor=BudgetedExecutor(),
        validator=Validator(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        limits=ExecutionLimits(model_requests_per_step=1),
    )
    result = await engine.run(envelope(), identity=Identity())
    assert result.status == "failed"
    assert result.error is not None and result.error.code == ErrorCode.BUDGET_EXHAUSTED
    assert result.usage.model_requests == 2
    assert result.usage.tokens.input_tokens == 20
    assert [record.usage.model_requests for _, record in result.flows] == [1, 1]
    assert dict(dict(result.flows)["specialist"].steps)["step_0"].status == "failed"


async def test_unknown_token_usage_in_later_failure_keeps_root_total_unknown() -> None:
    from foliqant.core.execution import TokenUsage

    class PartiallyMeasuredExecutor(Executor):
        async def execute(self, step, inputs, context):
            ticket = await context.budget.start_model_request("test-model")
            if context.flow_id == "specialist":
                raise RuntimeError("private failure")
            await context.budget.finish_model_request(ticket, TokenUsage(10, 2, 0, 0, 0))
            return await super().execute(step, inputs, context)

    result = await runner(
        plan(flow("triage", next_flow="specialist"), flow("specialist")),
        PartiallyMeasuredExecutor(),
    ).run(envelope(), identity=Identity())
    assert result.status == "failed"
    assert result.usage.model_requests == 2
    assert result.usage.tokens.input_tokens is None
    assert dict(result.flows)["triage"].usage.tokens.input_tokens == 10
    assert dict(result.flows)["specialist"].usage.tokens.input_tokens is None


@pytest.mark.parametrize(
    "mode", ["foreign_object", "nonboolean_review", "nonjson_result", "bad_selection", "bad_issues"]
)
async def test_malformed_executor_outcomes_stop_before_another_flow(mode: str) -> None:
    from dataclasses import replace

    from foliqant.core.plan import DecisionStepPlan

    class InvalidExecutor(Executor):
        async def execute(self, step, inputs, context):
            self.calls.append((context.execution_id, context.flow_id, context.step_id))
            await context.budget.start_model_request("test-model")
            if mode == "foreign_object":
                return object()
            if mode == "nonboolean_review":
                return StepOutcome(None, needs_review=1)
            if mode == "nonjson_result":
                return StepOutcome({object()})
            if mode == "bad_selection":
                return StepOutcome(None, selection="private malformed selection")
            return StepOutcome(
                None, needs_review=True, unresolved_issues=("private unknown issue",)
            )

    first = replace(flow("triage", next_flow="specialist"), output=None)
    first = replace(
        first,
        steps=(
            DecisionStepPlan(
                name="step_0",
                type="decision",
                location=LOCATION,
                sources=(("message", pointer("/payload/message")),),
            ),
        ),
    )
    executor = InvalidExecutor()
    result = await runner(plan(first, flow("specialist")), executor).run(
        envelope(), identity=Identity()
    )
    assert result.status == "failed"
    assert result.error is not None and result.error.code == ErrorCode.INVALID_OUTPUT
    assert "private" not in result.error.message
    assert len(executor.calls) == 1
    assert result.usage.model_requests == 1
    assert dict(result.flows)["specialist"].status == "skipped"
    record = dict(dict(result.flows)["triage"].steps)["step_0"]
    assert record.status == "failed"
    assert not record.has_result
