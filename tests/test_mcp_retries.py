"""Real MCP HTTP protocol proves retry evidence survives SDK task boundaries."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx2
import pytest
from mcp import Client
from mcp.server import MCPServer
from tests.test_mcp_runtime import Allow, context, fixture_runtime, profiles

from foliqant.adapters.mcp.retry import _HTTP_ATTEMPT
from foliqant.adapters.mcp.runtime import McpRuntime
from foliqant.adapters.mcp.transport import McpClientSessionFactory
from foliqant.contracts.retry import RetryConfig
from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity


@asynccontextmanager
async def http_runtime(actions, *, attempts=3, delay=0, authorizer=None) -> AsyncIterator:
    server = MCPServer("retry-test")
    executed = []

    @server.tool()
    async def lookup(key: str) -> dict[str, str]:
        executed.append(key)
        return {"value": key.upper()}

    async with Client(server, cache=None) as client:
        tools = (await client.list_tools()).tools
    configured = profiles(tools)
    configured.servers["records"] = configured.servers["records"].model_copy(
        update={
            "retry": RetryConfig(
                max_attempts=attempts, initial_delay_seconds=delay, max_delay_seconds=delay
            ),
        }
    )
    app = server.streamable_http_app(stateless_http=True, host="tools.example.test")
    underlying = httpx2.ASGITransport(app=app)
    calls = []
    active = 0
    clients = []

    class Transport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal active
            document = json.loads(request.content) if request.method == "POST" else {}
            if document.get("method") != "tools/call":
                return await underlying.handle_async_request(request)
            active += 1
            assert active == 1  # No second call overlaps a prior pending request.
            try:
                observation = _HTTP_ATTEMPT.get()
                assert observation is not None and observation.active
                calls.append((document, observation))
                action = actions[len(calls) - 1] if len(calls) <= len(actions) else 200
                if isinstance(action, BaseException):
                    raise action
                if action == "hang":
                    await asyncio.Event().wait()
                if isinstance(action, tuple):
                    status, code = action
                    return httpx2.Response(
                        status,
                        request=request,
                        json={
                            "jsonrpc": "2.0",
                            "id": document["id"],
                            "error": {"code": code, "message": "PRIVATE peer diagnostics"},
                        },
                    )
                if action != 200:
                    return httpx2.Response(
                        action,
                        request=request,
                        headers={"Retry-After": "0"},
                        json={"error": "PRIVATE upstream detail"},
                    )
                return await underlying.handle_async_request(request)
            finally:
                active -= 1

    def create_client(*, auth, timeout, request_hooks):
        client = httpx2.AsyncClient(
            auth=auth,
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            event_hooks={"request": request_hooks},
            transport=Transport(),
        )
        clients.append(client)
        return client

    factory = McpClientSessionFactory(
        configured, credential_providers={}, http_client_factory=create_client
    )
    authorized = authorizer or Allow()
    runtime = McpRuntime(configured, factory, authorized)
    async with app.router.lifespan_context(app):
        yield runtime, calls, executed, authorized
    assert all(client.is_closed for client in clients)
    assert active == 0


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
async def test_real_sdk_transient_status_retries_with_new_ids_and_reauthorization(status):
    async with http_runtime([status, 200]) as (runtime, calls, executed, authorizer):
        ctx = context(Identity("tenant", "user"))
        async with runtime.open("records", ("lookup",), ctx) as tools:
            assert await tools.call("lookup", {"key": "ok"}) == {"value": "OK"}
        assert len(calls) == ctx.budget.snapshot().tool_calls == 2
        assert authorizer.identities == [ctx.caller.identity, ctx.caller.identity]
        assert executed == ["ok"]
        assert calls[0][0]["params"] == calls[1][0]["params"]
        assert calls[0][0]["id"] != calls[1][0]["id"]
        assert calls[0][1] is not calls[1][1]
        assert calls[0][1].status == status and calls[1][1].status == 200
        assert all(not observation.active for _, observation in calls)


@pytest.mark.parametrize(
    "action,code",
    [
        (400, ErrorCode.REQUEST_REJECTED),
        (401, ErrorCode.UNAUTHENTICATED),
        (403, ErrorCode.FORBIDDEN),
        # A JSON-RPC error never grants the HTTP status's retry permission.
        ((503, -32602), ErrorCode.DEPENDENCY_OVERLOADED),
        # A server that rejects a validated call, or fails it internally.
        ((200, -32602), ErrorCode.REQUEST_REJECTED),
        ((200, -32603), ErrorCode.DEPENDENCY_FAILURE),
    ],
)
async def test_terminal_http_or_protocol_errors_never_repeat(action, code):
    async with http_runtime([action]) as (runtime, calls, executed, _):
        ctx = context()
        async with runtime.open("records", ("lookup",), ctx) as tools:
            with pytest.raises(ServiceError) as raised:
                await tools.call("lookup", {"key": "ok"})
        assert raised.value.code == code
        assert len(calls) == ctx.budget.snapshot().tool_calls == 1
        assert executed == []
        assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize(
    "status,code",
    [
        (429, ErrorCode.RATE_LIMITED),
        (503, ErrorCode.DEPENDENCY_OVERLOADED),
        (500, ErrorCode.DEPENDENCY_FAILURE),
    ],
)
async def test_exhausted_transient_statuses_keep_their_code_and_retry_permission(status, code):
    async with http_runtime([status, status], attempts=2) as (runtime, calls, executed, _):
        ctx = context()
        async with runtime.open("records", ("lookup",), ctx) as tools:
            with pytest.raises(ServiceError) as raised:
                await tools.call("lookup", {"key": "ok"})
        assert raised.value.code is code and raised.value.retryable
        assert len(calls) == 2 and executed == []


async def test_budget_exhaustion_prevents_retry_wire_call():
    async with http_runtime([503]) as (runtime, calls, _, authorizer):
        ctx = replace(context(), budget=StepBudget(model_requests=0, tool_calls=1))
        async with runtime.open("records", ("lookup",), ctx) as tools:
            with pytest.raises(ServiceError) as raised:
                await tools.call("lookup", {"key": "ok"})
        # The limit refused the retry: the failure reports the overload it would have retried.
        assert raised.value.code == ErrorCode.DEPENDENCY_OVERLOADED and raised.value.retryable
        assert len(calls) == ctx.budget.snapshot().tool_calls == 1
        assert len(authorizer.identities) == 2  # Every retry reauthorizes before reserving.


async def test_authorization_can_revoke_retry_before_another_reservation():
    class Revoke(Allow):
        async def authorize(self, server, tool, arguments, ctx):
            await super().authorize(server, tool, arguments, ctx)
            if len(self.identities) > 1:
                raise ServiceError(ErrorCode.FORBIDDEN)

    async with http_runtime([429], authorizer=Revoke()) as (runtime, calls, _, authorizer):
        ctx = context()
        async with runtime.open("records", ("lookup",), ctx) as tools:
            with pytest.raises(ServiceError) as raised:
                await tools.call("lookup", {"key": "ok"})
        assert raised.value.code == ErrorCode.FORBIDDEN
        assert len(calls) == ctx.budget.snapshot().tool_calls == 1


@pytest.mark.parametrize("actions,expected", [(["hang"], 1), ([503, "hang"], 2)])
async def test_timeout_never_retries_or_overlaps_pending_remote_work(actions, expected):
    async with http_runtime(actions) as (runtime, calls, _, _):
        ctx = replace(context(), tool_timeout=0.04)
        async with runtime.open("records", ("lookup",), ctx) as tools:
            with pytest.raises(ServiceError) as raised:
                await tools.call("lookup", {"key": "ok"})
            assert raised.value.code == ErrorCode.REQUEST_TIMEOUT
            assert len(calls) == expected
        assert ctx.budget.snapshot().tool_calls == expected


async def test_cancellation_during_backoff_does_not_start_later_calls(monkeypatch):
    import foliqant.core.retry as retry_module

    monkeypatch.setattr(retry_module.random, "uniform", lambda low, high: high)
    async with http_runtime([429], delay=0.2) as (runtime, calls, _, _):
        ctx = context()
        async with runtime.open("records", ("lookup",), ctx) as tools:
            task = asyncio.create_task(tools.call("lookup", {"key": "ok"}))
            async with asyncio.timeout(1):
                while not calls or calls[0][1].active:
                    await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(calls) == ctx.budget.snapshot().tool_calls == 1


async def test_stdio_or_peer_internal_error_cannot_forge_http_retry_permission(monkeypatch):
    from mcp import MCPError, types
    from mcp.client.session import ClientSession

    runtime, _, _ = await fixture_runtime()
    runtime._profiles.servers["records"] = runtime._profiles.servers["records"].model_copy(
        update={
            "retry": RetryConfig(max_attempts=3, initial_delay_seconds=0, max_delay_seconds=0),
        }
    )
    ctx = context()
    async with runtime.open("records", ("lookup",), ctx) as tools:

        async def failure(*args, **kwargs):
            raise MCPError(types.INTERNAL_ERROR, "HTTP 503 retry now", data={"retry_after": 0})

        monkeypatch.setattr(ClientSession, "send_request", failure)
        with pytest.raises(ServiceError):
            await tools.call("lookup", {"key": "ok"})
    assert ctx.budget.snapshot().tool_calls == 1


async def test_response_observations_are_isolated_across_concurrent_callers():
    from foliqant.adapters.mcp.retry import observe_http_attempt, record_http_response

    ready = asyncio.Event()
    entered = 0

    async def one(status):
        nonlocal entered
        with observe_http_attempt() as observation:
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            request = httpx2.Request(
                "POST",
                "https://tools.example.test/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {},
                },
            )
            await record_http_response(httpx2.Response(status, request=request))
            await asyncio.sleep(0)
            assert observation.status == status
            return observation

    first, second = await asyncio.gather(one(503), one(401))
    assert first is not second
    assert first.failure is not None and first.failure.retryable
    assert second.failure is not None and second.failure.code is ErrorCode.UNAUTHENTICATED
    assert not second.failure.retryable
    assert not first.active and not second.active


async def test_mcp_late_response_after_ignored_cancellation_is_terminal(monkeypatch):
    from mcp import types
    from mcp.client.session import ClientSession

    runtime, _, _ = await fixture_runtime()
    ctx = replace(context(), tool_timeout=0.01)
    async with runtime.open("records", ("lookup",), ctx) as tools:

        async def late(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return types.CallToolResult(content=[], structuredContent={"value": "late"})

        monkeypatch.setattr(ClientSession, "send_request", late)
        with pytest.raises(ServiceError) as raised:
            await tools.call("lookup", {"key": "ok"})
    assert raised.value.code == ErrorCode.REQUEST_TIMEOUT
    assert ctx.budget.snapshot().tool_calls == 1
