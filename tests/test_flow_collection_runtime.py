"""Sequential callable flows share root ownership and retain truthful child ledgers."""

import asyncio
from dataclasses import replace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_flow_runner import LOCATION, Validator, flow, plan, pointer
from test_runner import Scripted

from foliqant.adapters.telemetry.observation import WorkflowTelemetry
from foliqant.adapters.telemetry.privacy import SafeSpanProcessor, TelemetryLabels
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.contracts.execution import FlowCollectionResult, StepResult, to_execution_result
from foliqant.core.admission import CapacityLimiter
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome, TokenUsage
from foliqant.core.identity import Identity
from foliqant.core.json import freeze_json, thaw_json
from foliqant.core.plan import (
    MAX_COLLECTION_DEPTH,
    BindingPlan,
    FlowCollectionStepPlan,
    SchemaResourcePlan,
    TransitionTargetPlan,
)
from foliqant.core.runner import ExecutionLimits, WorkflowRunner


def item(name="one", value="ok", target="worker"):
    return {"id": name, "flow": target, "input": {"message": value}}


def collection_plan(*, review_route=False, max_items=32, schema=False, child_steps=1):
    dispatch = FlowCollectionStepPlan(
        name="dispatch",
        type="flow_collection",
        location=LOCATION,
        items=pointer("/payload/items"),
        flows=("worker", "other"),
        max_items=max_items,
    )
    main = replace(
        flow("main"),
        input=(("items", pointer("/payload/items")),),
        steps=(dispatch,),
        output=pointer("/steps/dispatch/result"),
        on_unresolved=TransitionTargetPlan(flow="review") if review_route else None,
    )
    child = replace(flow("worker", steps=child_steps), input=(), callable=True, transition=None)
    other = replace(child, name="other")
    flows = [main, child, other]
    if review_route:
        flows.append(replace(flow("review"), input=(("message", pointer("/payload/message")),)))
    result = replace(plan(*flows), output=pointer("/flows/main/result"))
    if schema:
        shape = freeze_json(
            {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            }
        )
        child = replace(child, input_schema_path="worker/input.json", input_schema=shape)
        result = replace(
            result,
            flows=(main, child, other),
            schema_resources=(SchemaResourcePlan("worker/input.json", shape),),
        )
    return result


def envelope(items):
    return AcceptedEnvelope(
        payload=freeze_json({"items": items, "message": "review context"}),
        metadata=freeze_json({"language": "de"}),
    )


def runner(value, execute, *, max_steps=32, timeout=1, validator=None, observer=None):
    return WorkflowRunner(
        value,
        executor=Scripted(execute),
        validator=validator or Validator(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        limits=ExecutionLimits(max_steps=max_steps, run_timeout=timeout),
        observer=observer,
    )


async def test_sequential_items_share_root_identity_deadline_and_exact_usage():
    calls = []

    async def execute(step, inputs, context):
        calls.append((inputs["message"], context))
        ticket = await context.budget.start_model_request()
        await context.budget.finish_model_request(ticket, TokenUsage(2, 3, 0, 0, 0))
        return StepOutcome(freeze_json({"value": inputs["message"]}))

    result = await runner(collection_plan(), execute).run(
        envelope([item(), item("two", "zweite", "other")]),
        identity=Identity(),
    )
    public = to_execution_result(result)
    assert public.execution.status == "completed"
    assert list(public.flows) == ["main"]
    collection = FlowCollectionResult.model_validate(public.payload)
    assert [(row.id, row.flow, row.status) for row in collection.items] == [
        ("one", "worker", "completed"),
        ("two", "other", "completed"),
    ]
    assert [value for value, _ in calls] == ["ok", "zweite"]
    assert {context.execution_id for _, context in calls} == {result.execution_id}
    assert len({context.deadline for _, context in calls}) == 1
    assert all(context.caller.metadata["language"] == "de" for _, context in calls)
    assert public.execution.usage.model_requests == 2
    assert public.execution.usage.input_tokens == 4
    assert public.flows["main"].steps["dispatch"].usage.model_requests == 2
    assert public.flows["main"].steps["dispatch"].kind == "flow_collection"
    assert all(row.usage.model_requests == 1 for row in collection.items)


@pytest.mark.parametrize(
    "items",
    [
        {},
        [item(), item()],
        [item(), {**item("two"), "unexpected": 1}],
        [item(), {"id": "two", "flow": "worker"}],
        [item(), item("two", target="main")],
        [item(), item("two", target="unlisted")],
        [item(), {**item("two"), "input": None}],
        [item(), item("bad-id")],
    ],
)
async def test_invalid_batch_is_rejected_before_any_child_io(items):
    calls = []

    async def execute(*args):
        calls.append(args)
        return StepOutcome(None)

    result = await runner(collection_plan(), execute).run(envelope(items), identity=Identity())
    assert result.status == "failed" and result.error.code is ErrorCode.INVALID_INPUT
    assert calls == []
    record = dict(dict(result.flows)["main"].steps)["dispatch"]
    assert record.partial_result is None and not record.has_result
    assert record.kind == "flow_collection"


async def test_all_child_input_schemas_and_count_checked_before_first_child():
    calls = []

    async def execute(*args):
        calls.append(args)
        return StepOutcome(None)

    value = collection_plan(schema=True)
    result = await runner(value, execute, validator=WorkflowSchemas(value)).run(
        envelope([item(), item("two", 123)]),
        identity=Identity(),
    )
    assert result.status == "failed" and result.error.code is ErrorCode.INVALID_INPUT
    result = await runner(collection_plan(max_items=1), execute).run(
        envelope([item(), item("two")]),
        identity=Identity(),
    )
    assert result.status == "failed" and result.error.code is ErrorCode.INVALID_INPUT
    assert calls == []


async def test_empty_batch_is_completed_without_child_io():
    async def execute(*args):
        raise AssertionError("unexpected child I/O")

    result = await runner(collection_plan(), execute).run(envelope([]), identity=Identity())
    assert result.status == "completed" and thaw_json(result.payload) == {"items": []}
    assert result.usage.model_requests == result.usage.tool_calls == 0


async def test_collection_kind_is_available_to_later_step_bindings():
    value = collection_plan()
    main = value.flow("main")
    after = replace(
        flow("template").steps[0],
        name="inspect",
        input=(("kind", pointer("/steps/dispatch/kind")),),
    )
    main = replace(main, steps=(*main.steps, after), output=pointer("/steps/inspect/result"))
    value = replace(value, flows=(main, *value.flows[1:]))
    observed = []

    async def execute(step, inputs, context):
        observed.append(thaw_json(inputs))
        return StepOutcome(inputs["kind"])

    result = await runner(value, execute).run(envelope([]), identity=Identity())
    assert result.status == "completed"
    assert result.payload == "flow_collection"
    assert observed == [{"kind": "flow_collection"}]


@pytest.mark.parametrize("nested", [False, True])
async def test_valid_deep_child_result_transfers_between_flows(nested):
    value = collection_plan()
    main = replace(value.flow("main"), transition=TransitionTargetPlan(flow="after"))
    worker = value.flow("worker")
    if nested:
        dispatch = replace(
            main.steps[0],
            items=pointer("/payload/message"),
            flows=("other",),
        )
        worker = replace(worker, steps=(dispatch,), output=pointer("/steps/dispatch/result"))
    after = replace(flow("after"), input=(("message", pointer("/flows/main/result")),))
    value = replace(value, flows=(main, worker, value.flow("other"), after))
    leaf = "leaf"
    for _ in range(62):
        leaf = {"value": leaf}
    frozen = freeze_json(leaf)
    observed = []

    async def execute(step, inputs, context):
        if context.flow_id == "after":
            observed.append(inputs["message"])
            with pytest.raises(TypeError):
                inputs["message"]["items"][0]["status"] = "failed"
            return StepOutcome("accepted")
        return StepOutcome(frozen)

    payload = [item(value=[item(target="other")])] if nested else [item()]
    result = await runner(value, execute).run(envelope(payload), identity=Identity())
    assert result.status == "completed"
    assert len(observed) == 1
    ledger = FlowCollectionResult.model_validate(thaw_json(result.payload))
    child = ledger.items[0].result
    if nested:
        child = FlowCollectionResult.model_validate(child).items[0].result
    assert child == leaf


async def test_child_operation_result_still_enforces_business_depth_limit():
    leaf = "leaf"
    for _ in range(65):
        leaf = {"value": leaf}

    async def execute(*args):
        return StepOutcome(leaf)

    result = await runner(collection_plan(), execute).run(envelope([item()]), identity=Identity())
    assert result.status == "failed"
    assert result.error.code is ErrorCode.INVALID_OUTPUT
    record = dict(dict(result.flows)["main"].steps)["dispatch"]
    ledger = FlowCollectionResult.model_validate(thaw_json(record.partial_result))
    assert ledger.items[0].status == "failed"
    assert "result" not in ledger.items[0].model_dump()


@pytest.mark.parametrize("depth", [MAX_COLLECTION_DEPTH, MAX_COLLECTION_DEPTH + 1])
async def test_hand_built_plan_respects_collection_depth_limit(depth):
    value = collection_plan()
    flows = []
    for index in range(depth):
        name = "main" if index == 0 else f"child_{index}"
        target = f"child_{index + 1}"
        dispatch = replace(
            value.flow("main").steps[0],
            items=BindingPlan(kind="literal", literal=freeze_json([item(target=target)])),
            flows=(target,),
        )
        flows.append(
            replace(
                flow(name),
                input=(),
                steps=(dispatch,),
                output=pointer("/steps/dispatch/result"),
                callable=index > 0,
                transition=None if index > 0 else TransitionTargetPlan(outcome="completed"),
            )
        )
    flows.append(replace(flow(f"child_{depth}"), callable=True, input=(), transition=None))
    value = replace(value, flows=tuple(flows))
    calls = []

    async def execute(*args):
        calls.append(True)
        return StepOutcome("ok")

    result = await runner(value, execute).run(envelope([]), identity=Identity())
    assert result.status == ("completed" if depth == MAX_COLLECTION_DEPTH else "failed")
    assert calls == ([True] if depth == MAX_COLLECTION_DEPTH else [])
    public = to_execution_result(result)
    if depth > MAX_COLLECTION_DEPTH:
        assert public.execution.error.code is ErrorCode.INVALID_CONFIGURATION


async def test_review_continues_remaining_items_then_uses_parent_unresolved_route():
    calls = []

    async def execute(step, inputs, context):
        calls.append((context.flow_id, context.step_id, inputs["message"]))
        return StepOutcome(inputs["message"], needs_review=inputs["message"] == "review")

    result = await runner(collection_plan(review_route=True, child_steps=2), execute).run(
        envelope([item(value="review"), item("two")]),
        identity=Identity(),
    )
    main = dict(result.flows)["main"]
    collection = FlowCollectionResult.model_validate(thaw_json(dict(main.steps)["dispatch"].result))
    assert main.status == "needs_review"
    assert [row.status for row in collection.items] == ["needs_review", "completed"]
    assert collection.items[0].steps["step_1"].status == "skipped"
    assert calls[-1] == ("review", "step_0", "review context")
    assert result.transitions[0].reason == "needs_review"
    assert result.transitions[0].flow == "review"


@pytest.mark.parametrize("mode", ["failure", "timeout", "budget"])
async def test_failure_retains_completed_failed_and_unstarted_items_without_double_count(mode):
    calls = []
    cleaned = []

    async def execute(step, inputs, context):
        calls.append(inputs["message"])
        await context.budget.start_model_request()
        if inputs["message"] == "fail":
            if mode == "timeout":
                try:
                    await asyncio.sleep(1)
                finally:
                    cleaned.append("joined")
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        return StepOutcome(inputs["message"])

    result = await runner(
        collection_plan(),
        execute,
        max_steps=2 if mode == "budget" else 32,
        timeout=0.03 if mode == "timeout" else 1,
    ).run(
        envelope([item(), item("two", "fail"), item("three")]),
        identity=Identity(),
    )
    public = to_execution_result(result)
    assert public.execution.status == "failed"
    step = public.flows["main"].steps["dispatch"]
    assert "result" not in step.model_fields_set
    assert step.status == "failed"
    rows = step.partial_result.items
    assert [row.status for row in rows] == ["completed", "failed", "skipped"]
    assert rows[-1].steps["step_0"].status == "skipped"
    assert rows[-1].usage is None and rows[-1].elapsed_seconds is None
    assert public.execution.usage.model_requests == (1 if mode == "budget" else 2)
    assert len(calls) == (1 if mode == "budget" else 2)
    if mode == "timeout":
        assert cleaned == ["joined"]
        assert public.execution.error.code is ErrorCode.TIMEOUT


async def test_cancellation_joins_active_child_and_never_starts_next():
    entered = asyncio.Event()
    cleaned = asyncio.Event()
    calls = []

    async def execute(step, inputs, context):
        calls.append(inputs["message"])
        if len(calls) == 2:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        return StepOutcome(inputs["message"])

    task = asyncio.create_task(
        runner(collection_plan(), execute).run(
            envelope([item(), item("two"), item("three")]),
            identity=Identity(),
        )
    )
    await asyncio.wait_for(entered.wait(), 0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set() and len(calls) == 2


async def test_child_flow_trace_is_nested_under_collection_step():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    labels = TelemetryLabels(
        workflows=frozenset({"inbox"}),
        flows=frozenset({"main", "worker"}),
        steps=frozenset({"dispatch", "step_0"}),
    )
    provider.add_span_processor(SafeSpanProcessor(SimpleSpanProcessor(exporter), labels))

    async def execute(*args):
        return StepOutcome("ok")

    try:
        result = await runner(
            collection_plan(), execute, observer=WorkflowTelemetry(provider, labels=labels)
        ).run(
            envelope([item(), item("two")]),
            identity=Identity(),
        )
        assert result.status == "completed"
        spans = exporter.get_finished_spans()
        parent = next(
            span for span in spans if span.attributes.get("foliqant.step.name") == "dispatch"
        )
        children = [
            span
            for span in spans
            if span.name == "foliqant.flow"
            and span.attributes.get("foliqant.flow.name") == "worker"
        ]
        assert len(children) == 2
        assert all(span.parent.span_id == parent.context.span_id for span in children)
    finally:
        provider.shutdown()


async def test_isolated_collection_step_and_callable_flow_use_existing_public_paths():
    calls = []

    async def execute(step, inputs, context):
        calls.append(context.flow_id)
        return StepOutcome(inputs["message"])

    subject = runner(collection_plan(), execute)
    result = await subject.run_step(
        "main",
        "dispatch",
        AcceptedEnvelope(payload=freeze_json({"items": [item()]}), metadata={}),
        identity=Identity(),
    )
    assert result.status == "completed"
    assert list(to_execution_result(result).flows) == ["main"]
    assert result.payload["items"][0]["result"] == "ok"
    single = await subject.run_flow(
        "worker",
        AcceptedEnvelope(payload=freeze_json({"message": "isolated"}), metadata={}),
        identity=Identity(),
    )
    assert single.status == "completed" and single.payload == "isolated"
    assert calls == ["worker", "worker"]


async def test_nested_collections_share_root_budget_and_propagate_failure_ledger():
    value = collection_plan()
    main, worker, leaf = value.flows
    nested = FlowCollectionStepPlan(
        name="nested",
        type="flow_collection",
        location=LOCATION,
        items=pointer("/payload/items"),
        flows=("other",),
        max_items=32,
    )
    worker = replace(worker, steps=(nested,), output=pointer("/steps/nested/result"))
    value = replace(value, flows=(main, worker, leaf))
    calls = []

    async def execute(step, inputs, context):
        calls.append(inputs["message"])
        return StepOutcome(inputs["message"])

    items = [
        {
            "id": "parent",
            "flow": "worker",
            "input": {
                "items": [item("first", target="other"), item("second", target="other")],
            },
        }
    ]
    result = await runner(value, execute, max_steps=3).run(envelope(items), identity=Identity())
    public = to_execution_result(result)
    assert result.status == "failed" and result.error.code is ErrorCode.BUDGET_EXHAUSTED
    outer = public.flows["main"].steps["dispatch"].partial_result.items[0]
    inner = outer.steps["nested"].partial_result
    assert [row.status for row in inner.items] == ["completed", "failed"]
    assert calls == ["ok"]
    assert list(public.flows) == ["main"]


def test_collection_marker_revalidates_ledgers_without_guessing_business_shape():
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError

    good = {"kind": "flow_collection", "status": "completed", "result": {"items": []}}
    validator = Draft202012Validator(StepResult.model_json_schema())
    assert StepResult.model_validate(good).kind == "flow_collection"
    assert validator.is_valid(good)
    bad = {**good, "result": {"items": [{"id": "x", "flow": "worker"}]}}
    with pytest.raises(ValidationError):
        StepResult.model_validate(bad)
    assert not validator.is_valid(bad)
    # Ordinary handler/model business objects never opt into nested execution.
    ordinary = {"status": "completed", "result": bad["result"]}
    assert StepResult.model_validate(ordinary).kind is None
    assert validator.is_valid(ordinary)
    for status in ("completed", "needs_review", "skipped", "cancelled"):
        with pytest.raises(ValidationError):
            StepResult.model_validate({**good, "status": status, "partial_result": {"items": []}})
    error = {"code": "timeout", "message": str(ServiceError(ErrorCode.TIMEOUT)), "retryable": False}
    failed = {"status": "failed", "error": error, "partial_result": {"items": []}}
    with pytest.raises(ValidationError):
        StepResult.model_validate(failed)
    assert not validator.is_valid(failed)
    duplicate = {"items": [{"id": "same", "flow": "worker", "status": "skipped", "steps": {}}] * 2}
    with pytest.raises(ValidationError):
        FlowCollectionResult.model_validate(duplicate)
