"""Real in-process MCP protocol tests; no network or model endpoint calls."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast

import pytest
from mcp import Client, types
from mcp.server import MCPServer
from tests.flow_fixtures import operation_flow

from foliqant.adapters.mcp.runtime import McpExecutor, McpRuntime, McpTools
from foliqant.contracts.mcp import McpProfiles
from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import CallerContext
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import McpStepPlan, SourceLocation
from foliqant.ports.execution import StepContext


def context(identity: Identity | None = None) -> StepContext:
    return StepContext(
        flow_id="main",
        execution_id="run",
        workflow="test",
        revision="a" * 64,
        step_id="lookup",
        caller=CallerContext(identity or Identity(), cast(FrozenObject, freeze_json({}))),
        deadline=asyncio.get_running_loop().time() + 3,
        model_timeout=1,
        tool_timeout=1,
        budget=StepBudget(model_requests=3, tool_calls=3),
    )


class Allow:
    def __init__(self) -> None:
        self.identities: list[Identity] = []

    async def authorize(
        self, server: str, tool: str, arguments: FrozenObject, ctx: StepContext
    ) -> None:
        self.identities.append(ctx.caller.identity)


class InProcessFactory:
    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self.opened = 0
        self.closed = 0

    @asynccontextmanager
    async def open(self, server_alias: str, ctx: StepContext) -> AsyncIterator[Client]:
        self.opened += 1
        async with Client(self.server, cache=None) as client:
            try:
                yield client
            finally:
                self.closed += 1


def profiles(tools: list[types.Tool], *, effect: str = "read") -> McpProfiles:
    return McpProfiles.model_validate(
        {
            "servers": {
                "records": {
                    "transport": {
                        "type": "streamable_http",
                        "endpoint": "https://tools.example.test/mcp",
                    },
                    "identity_meta_key": "example.test/identity",
                    "catalog": {
                        "tools": {
                            tool.name: {
                                "input_schema": tool.input_schema,
                                "output_schema": tool.output_schema,
                                "effect": effect,
                            }
                            for tool in tools
                        }
                    },
                }
            }
        }
    )


async def fixture_runtime() -> tuple[McpRuntime, InProcessFactory, Allow]:
    server = MCPServer("Test")

    @server.tool()
    async def lookup(key: str) -> dict[str, str]:
        return {"value": key.upper()}

    factory = InProcessFactory(server)
    async with Client(server, cache=None) as client:
        catalog = (await client.list_tools()).tools
    authorizer = Allow()
    return McpRuntime(profiles(catalog), factory, authorizer), factory, authorizer


async def test_direct_tool_uses_actual_sdk_validates_results_and_retains_budget() -> None:
    runtime, factory, authorizer = await fixture_runtime()
    ctx = context(Identity("tenant", "user"))
    step = McpStepPlan(
        "lookup", "mcp", SourceLocation("steps/lookup.yaml", 1, 1), server="records", tool="lookup"
    )
    result = await McpExecutor(runtime).execute(
        step, cast(FrozenObject, freeze_json({"key": "test"})), ctx
    )
    assert thaw_json(result.result) == {"value": "TEST"}
    assert ctx.budget.snapshot().tool_calls == 1
    assert authorizer.identities == [Identity("tenant", "user")]
    assert factory.opened == factory.closed == 1


async def test_argument_validation_and_authorization_fail_before_call_reservation() -> None:
    runtime, factory, _ = await fixture_runtime()
    ctx = context()
    async with runtime.open("records", ("lookup",), ctx) as tools:
        with pytest.raises(ServiceError) as error:
            await tools.call("lookup", cast(FrozenObject, freeze_json({"key": 1})))
        assert error.value.code == ErrorCode.INVALID_INPUT
        with pytest.raises(ServiceError) as error:
            await tools.call("undeclared", {})
        assert error.value.code == ErrorCode.FORBIDDEN
    assert ctx.budget.snapshot().tool_calls == 0
    assert factory.closed == 1


async def test_concurrent_callers_own_separate_sessions_and_callback_lifetimes() -> None:
    runtime, factory, authorizer = await fixture_runtime()
    entered = asyncio.Event()
    scopes: list[McpTools] = []

    async def run(identity: Identity) -> object:
        async with runtime.open("records", ("lookup",), context(identity)) as tools:
            scopes.append(tools)
            if len(scopes) == 2:
                entered.set()
            await entered.wait()
            return await tools.call("lookup", {"key": "one"})

    await asyncio.gather(run(Identity("a", None)), run(Identity(None, "b")))
    assert set(authorizer.identities) == {Identity("a", None), Identity(None, "b")}
    assert factory.opened == factory.closed == 2
    with pytest.raises(ServiceError) as error:
        await scopes[0].call("lookup", {"key": "late"})
    assert error.value.code == ErrorCode.FORBIDDEN


async def test_forbidden_authorization_and_cancellation_do_not_discard_lifespan() -> None:
    runtime, factory, _ = await fixture_runtime()

    class Deny:
        async def authorize(
            self, server: str, tool: str, arguments: FrozenObject, ctx: StepContext
        ) -> None:
            raise ServiceError(ErrorCode.FORBIDDEN)

    runtime._authorizer = Deny()
    ctx = context()
    async with runtime.open("records", ("lookup",), ctx) as tools:
        with pytest.raises(ServiceError) as error:
            await tools.call("lookup", {"key": "one"})
        assert error.value.code == ErrorCode.FORBIDDEN
    assert ctx.budget.snapshot().tool_calls == 0
    assert factory.closed == 1


async def test_sdk_input_required_maps_to_review_without_automatic_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, factory, _ = await fixture_runtime()
    from mcp.client.session import ClientSession

    calls = 0

    original = ClientSession.send_request

    async def need_input(self: ClientSession, request: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        if request.method == "tools/call":
            calls += 1
            return types.InputRequiredResult(requestState="private-state")
        return await original(self, request, *args, **kwargs)

    monkeypatch.setattr(ClientSession, "send_request", need_input)
    ctx = context()
    step = McpStepPlan(
        "lookup", "mcp", SourceLocation("steps/lookup.yaml", 1, 1), server="records", tool="lookup"
    )
    outcome = await McpExecutor(runtime).execute(step, {"key": "x"}, ctx)
    assert outcome.needs_review and outcome.result is None
    assert calls == ctx.budget.snapshot().tool_calls == 1
    assert factory.closed == 1


async def test_mcp_metadata_forwards_only_host_identity_and_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, _ = await fixture_runtime()
    from mcp.client.session import ClientSession

    received: list[dict[str, object]] = []
    original = ClientSession.send_request

    async def record(self: ClientSession, *args: Any, **kwargs: Any) -> Any:
        if args[0].method == "tools/call":
            received.append(args[0].params.meta)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ClientSession, "send_request", record)
    runtime._trace_carrier = lambda: {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"}
    ctx = replace(
        context(Identity(None, "alice")),
        caller=CallerContext(
            Identity(None, "alice"), {"private": "secret", "tenant_id": "untrusted"}
        ),
    )
    async with runtime.open("records", ("lookup",), ctx) as tools:
        await tools.call("lookup", {"key": "a"})
    assert received == [
        {"example.test/identity": {"principal_id": "alice"}, **runtime._trace_carrier()}
    ]


@pytest.mark.parametrize("choice", ["auto", "required", "named"])
@pytest.mark.parametrize("review", [False, True])
async def test_model_calls_real_mcp_tool_then_returns_final_answer(
    choice: str, review: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from foliqant.adapters.models import ModelBinding, ModelExecutor
    from foliqant.adapters.validation import WorkflowSchemas
    from foliqant.core.admission import CapacityLimiter
    from foliqant.core.plan import LlmStepPlan, ToolPolicyPlan, WorkflowPlan

    runtime, factory, _ = await fixture_runtime()
    step = LlmStepPlan(
        "lookup",
        "llm",
        SourceLocation("steps/lookup.yaml", 1, 1),
        model="deciding",
        instructions="Consult the approved records tool.",
        output_kind="text",
        tools=ToolPolicyPlan(
            "records", ("lookup",), cast(Any, choice), "lookup" if choice == "named" else None
        ),
    )
    plan = WorkflowPlan(
        "test", "a" * 64, "main", None, None, None, (), None, (operation_flow(step),), step.location
    )
    settings: list[object] = []

    if review:
        from mcp.client.session import ClientSession

        original = ClientSession.send_request

        async def needs_input(self: ClientSession, request: Any, *args: Any, **kwargs: Any) -> Any:
            if request.method == "tools/call":
                return types.InputRequiredResult(requestState="private-state")
            return await original(self, request, *args, **kwargs)

        monkeypatch.setattr(ClientSession, "send_request", needs_input)

    async def model(messages: Any, info: Any) -> ModelResponse:
        settings.append(info.model_settings.get("tool_choice"))
        if len(settings) == 1:
            return ModelResponse(parts=[ToolCallPart("lookup", {"key": "one"}, tool_call_id="1")])
        assert "ONE" in str(messages)
        return ModelResponse(parts=[TextPart("Found ONE")])

    executor = ModelExecutor(
        {
            "deciding": ModelBinding(
                FunctionModel(model), {}, CapacityLimiter(concurrency=1, queue_limit=1), "native"
            )
        },
        WorkflowSchemas(plan),
        tools=runtime,
    )
    ctx = context()
    outcome = await executor.execute(step, {"question": "one"}, ctx)
    if review:
        assert outcome.needs_review and outcome.result is None
        assert ctx.budget.snapshot().model_requests == 1
        assert ctx.budget.snapshot().tool_calls == 1
        assert factory.opened == factory.closed == 1
        return
    assert outcome.result == "Found ONE"
    assert settings == [
        {"auto": "auto", "required": "required", "named": ["lookup"]}[choice],
        "auto",
    ]
    assert ctx.budget.snapshot().model_requests == 2
    assert ctx.budget.snapshot().tool_calls == 1
    assert factory.opened == factory.closed == 1


@pytest.mark.parametrize("output_kind", ["text", "schema"])
@pytest.mark.parametrize("max_iterations, succeeds", [(1, False), (2, True)])
async def test_llm_iteration_limit_counts_final_turn_and_resets_per_invocation(
    output_kind: str, max_iterations: int, succeeds: bool
) -> None:
    import json

    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from foliqant.adapters.models import ModelBinding, ModelExecutor
    from foliqant.adapters.validation import WorkflowSchemas
    from foliqant.core.admission import CapacityLimiter
    from foliqant.core.plan import (
        LlmStepPlan,
        SchemaResourcePlan,
        ToolPolicyPlan,
        WorkflowPlan,
    )

    runtime, _, _ = await fixture_runtime()
    schema = cast(
        FrozenObject,
        freeze_json(
            {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            }
        ),
    )
    step = LlmStepPlan(
        "lookup",
        "llm",
        SourceLocation("steps/lookup.yaml", 1, 1),
        model="deciding",
        instructions="Consult records and answer.",
        output_kind=cast(Any, output_kind),
        output_schema_path="answer.json" if output_kind == "schema" else None,
        output_schema=schema if output_kind == "schema" else None,
        tools=ToolPolicyPlan("records", ("lookup",), "required"),
        max_iterations=max_iterations,
    )
    resources = (SchemaResourcePlan("answer.json", schema),) if output_kind == "schema" else ()
    plan = WorkflowPlan(
        "test",
        "a" * 64,
        "main",
        None,
        None,
        None,
        resources,
        None,
        (operation_flow(step),),
        step.location,
    )
    turns = 0

    async def model(_messages: Any, info: Any) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns == 1:
            return ModelResponse(parts=[ToolCallPart("lookup", {"key": "one"}, tool_call_id="1")])
        if output_kind == "text":
            return ModelResponse(parts=[TextPart("Found ONE")])
        assert info.model_request_parameters.output_mode == "native"
        return ModelResponse(parts=[TextPart(json.dumps({"value": {"answer": "ONE"}}))])

    executor = ModelExecutor(
        {
            "deciding": ModelBinding(
                FunctionModel(model), {}, CapacityLimiter(concurrency=1, queue_limit=1), "native"
            )
        },
        WorkflowSchemas(plan),
        tools=runtime,
    )
    for _ in range(2):
        turns = 0
        ctx = context()
        if succeeds:
            outcome = await executor.execute(step, {"question": "one"}, ctx)
            assert thaw_json(outcome.result) == (
                "Found ONE" if output_kind == "text" else {"answer": "ONE"}
            )
            assert ctx.budget.snapshot().model_requests == 2
        else:
            with pytest.raises(ServiceError) as error:
                await executor.execute(step, {"question": "one"}, ctx)
            assert error.value.code == ErrorCode.ITERATION_LIMIT_REACHED
            assert ctx.budget.snapshot().model_requests == 1
        assert ctx.budget.snapshot().tool_calls == 1


@pytest.mark.parametrize("provider_attempt_limit, succeeds", [(2, False), (3, True)])
async def test_llm_iterations_exclude_provider_retries_but_attempt_budget_counts_them(
    provider_attempt_limit: int, succeeds: bool
) -> None:
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from foliqant.adapters.models import ModelBinding, ModelExecutor
    from foliqant.adapters.validation import WorkflowSchemas
    from foliqant.core.admission import CapacityLimiter
    from foliqant.core.plan import LlmStepPlan, ToolPolicyPlan, WorkflowPlan
    from foliqant.core.retry import RetryPolicy

    runtime, _, _ = await fixture_runtime()
    step = LlmStepPlan(
        "lookup",
        "llm",
        SourceLocation("steps/lookup.yaml", 1, 1),
        model="deciding",
        instructions="Consult records.",
        output_kind="text",
        tools=ToolPolicyPlan("records", ("lookup",), "required"),
        max_iterations=2,
    )
    plan = WorkflowPlan(
        "test",
        "a" * 64,
        "main",
        None,
        None,
        None,
        (),
        None,
        (operation_flow(step),),
        step.location,
    )
    calls = 0

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelHTTPError(503, "test", headers={"Retry-After": "0"})
        if calls == 2:
            return ModelResponse(parts=[ToolCallPart("lookup", {"key": "one"}, tool_call_id="1")])
        return ModelResponse(parts=[TextPart("Found ONE")])

    binding = ModelBinding(
        FunctionModel(model),
        {},
        CapacityLimiter(concurrency=1, queue_limit=1),
        "native",
        retry=RetryPolicy(2, 0, 0),
    )
    executor = ModelExecutor({"deciding": binding}, WorkflowSchemas(plan), tools=runtime)
    ctx = replace(context(), budget=StepBudget(model_requests=provider_attempt_limit, tool_calls=3))
    if succeeds:
        assert (await executor.execute(step, {}, ctx)).result == "Found ONE"
        assert calls == 3
    else:
        with pytest.raises(ServiceError) as error:
            await executor.execute(step, {}, ctx)
        assert error.value.code == ErrorCode.MODEL_REQUEST_LIMIT_REACHED
        assert calls == 2
    assert ctx.budget.snapshot().model_requests == provider_attempt_limit
    assert ctx.budget.snapshot().tool_calls == 1


async def test_required_tool_cannot_be_satisfied_by_model_text_alone() -> None:
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from foliqant.adapters.models import ModelBinding, ModelExecutor
    from foliqant.adapters.validation import WorkflowSchemas
    from foliqant.core.admission import CapacityLimiter
    from foliqant.core.plan import LlmStepPlan, ToolPolicyPlan, WorkflowPlan

    runtime, factory, _ = await fixture_runtime()
    step = LlmStepPlan(
        "lookup",
        "llm",
        SourceLocation("steps/lookup.yaml", 1, 1),
        model="deciding",
        instructions="Use records",
        output_kind="text",
        tools=ToolPolicyPlan("records", ("lookup",), "required"),
    )
    plan = WorkflowPlan(
        "test", "a" * 64, "main", None, None, None, (), None, (operation_flow(step),), step.location
    )

    async def model(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I called it, trust me")])

    executor = ModelExecutor(
        {
            "deciding": ModelBinding(
                FunctionModel(model), {}, CapacityLimiter(concurrency=1, queue_limit=1), "native"
            )
        },
        WorkflowSchemas(plan),
        tools=runtime,
    )
    ctx = context()
    with pytest.raises(ServiceError) as error:
        await executor.execute(step, {}, ctx)
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert ctx.budget.snapshot().model_requests == 1
    assert ctx.budget.snapshot().tool_calls == 0
    assert factory.closed == 1


@pytest.mark.parametrize("phase", ["tools/list", "tools/call"])
async def test_sdk_request_timeout_is_safe_and_retains_only_actual_tool_attempts(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from mcp import MCPError
    from mcp.client.session import ClientSession

    runtime, factory, _ = await fixture_runtime()
    original = ClientSession.send_request

    async def timeout(self: ClientSession, request: Any, *args: Any, **kwargs: Any) -> Any:
        if request.method == phase:
            raise MCPError(types.REQUEST_TIMEOUT, "PRIVATE diagnostic")
        return await original(self, request, *args, **kwargs)

    monkeypatch.setattr(ClientSession, "send_request", timeout)
    ctx = context()
    with pytest.raises(ServiceError) as error:
        async with runtime.open("records", ("lookup",), ctx) as tools:
            await tools.call("lookup", {"key": "one"})
    assert error.value.code == ErrorCode.REQUEST_TIMEOUT
    assert "PRIVATE" not in str(error.value)
    assert ctx.budget.snapshot().tool_calls == (1 if phase == "tools/call" else 0)
    assert factory.closed == 1


async def test_call_snapshots_arguments_before_awaiting_authorization() -> None:
    runtime, _, _ = await fixture_runtime()
    arguments: dict[str, Any] = {"key": "original"}

    class ChangeOriginal:
        async def authorize(
            self, server: str, tool: str, actual: FrozenObject, ctx: StepContext
        ) -> None:
            arguments["key"] = 42
            assert actual["key"] == "original"

    runtime._authorizer = ChangeOriginal()
    async with runtime.open("records", ("lookup",), context()) as tools:
        assert thaw_json(await tools.call("lookup", arguments)) == {"value": "ORIGINAL"}


async def test_discovery_gets_same_protected_metadata_as_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.client.session import ClientSession

    runtime, _, _ = await fixture_runtime()
    seen: list[tuple[str, dict[str, object]]] = []
    original = ClientSession.send_request

    async def record(self: ClientSession, request: Any, *args: Any, **kwargs: Any) -> Any:
        if request.method in {"tools/list", "tools/call"}:
            seen.append((request.method, request.params.meta))
        return await original(self, request, *args, **kwargs)

    monkeypatch.setattr(ClientSession, "send_request", record)
    runtime._trace_carrier = lambda: {"traceparent": "00-" + "c" * 32 + "-" + "d" * 16 + "-01"}
    async with runtime.open("records", ("lookup",), context(Identity("org", None))) as tools:
        await tools.call("lookup", {"key": "x"})
    assert [method for method, _ in seen] == ["tools/list", "tools/call"]
    assert (
        seen[0][1]
        == seen[1][1]
        == {
            "example.test/identity": {"tenant_id": "org"},
            **runtime._trace_carrier(),
        }
    )


async def test_cancellation_releases_session_without_refunding_started_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.client.session import ClientSession

    runtime, factory, _ = await fixture_runtime()
    entered = asyncio.Event()
    original = ClientSession.send_request

    async def stalled(self: ClientSession, request: Any, *args: Any, **kwargs: Any) -> Any:
        if request.method == "tools/call":
            entered.set()
            await asyncio.Event().wait()
        return await original(self, request, *args, **kwargs)

    monkeypatch.setattr(ClientSession, "send_request", stalled)
    ctx = context()

    async def run() -> None:
        async with runtime.open("records", ("lookup",), ctx) as tools:
            await tools.call("lookup", {"key": "x"})

    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ctx.budget.snapshot().tool_calls == 1
    assert factory.closed == 1


async def test_direct_step_has_one_deadline_across_connect_discover_and_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.client.session import ClientSession

    runtime, factory, _ = await fixture_runtime()
    original_open = factory.open
    original_request = ClientSession.send_request
    call_entered = asyncio.Event()

    @asynccontextmanager
    async def slow_open(server: str, ctx: StepContext) -> AsyncIterator[Client]:
        await asyncio.sleep(0.02)
        async with original_open(server, ctx) as client:
            yield client

    async def slow_request(self: ClientSession, request: Any, *args: Any, **kwargs: Any) -> Any:
        if request.method == "tools/list":
            await asyncio.sleep(0.02)
        if request.method == "tools/call":
            call_entered.set()
            await asyncio.sleep(0.05)
        return await original_request(self, request, *args, **kwargs)

    monkeypatch.setattr(factory, "open", slow_open)
    monkeypatch.setattr(ClientSession, "send_request", slow_request)
    ctx = replace(context(), tool_timeout=0.075)
    step = McpStepPlan(
        "lookup", "mcp", SourceLocation("steps/lookup.yaml", 1, 1), server="records", tool="lookup"
    )
    with pytest.raises(ServiceError) as error:
        await McpExecutor(runtime).execute(step, {"key": "x"}, ctx)
    assert error.value.code == ErrorCode.REQUEST_TIMEOUT
    assert call_entered.is_set()
    assert ctx.budget.snapshot().tool_calls == 1
    assert factory.closed == 1
