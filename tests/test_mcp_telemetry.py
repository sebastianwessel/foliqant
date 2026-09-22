"""Real MCP HTTP tracing stays continuous and private across concurrent callers."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import httpx2
from mcp import Client, types
from mcp.server import MCPServer
from opentelemetry import baggage
from opentelemetry import context as context_api
from opentelemetry import trace as trace_api
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from foliqant.adapters.mcp.runtime import McpExecutor, McpRuntime
from foliqant.adapters.mcp.transport import McpClientSessionFactory
from foliqant.adapters.telemetry.logging import configure_logging
from foliqant.adapters.telemetry.observation import WorkflowTelemetry, trace_carrier
from foliqant.adapters.telemetry.privacy import SafeSpanProcessor, TelemetryLabels
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.compiler import compile_workflow
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.contracts.mcp import McpProfiles
from foliqant.core.admission import CapacityLimiter
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject
from foliqant.core.runner import ExecutionLimits, WorkflowRunner
from foliqant.ports.execution import StepContext

_ROOT = Path(__file__).resolve().parents[1]
_ARGUMENT = "PRIVATE_ARGUMENT"
_RESULT = "PRIVATE_RESULT"
_PROMPT = "PRIVATE_PROMPT"
_EXCEPTION = "PRIVATE_EXCEPTION"
_ARBITRARY_METADATA = "PRIVATE_ARBITRARY_METADATA"
_IDENTITY_KEY = "example.test/identity"
_MCP_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
_MCP_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
_MCP_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"


class _Allow:
    def __init__(self) -> None:
        self.identities: list[Identity] = []

    async def authorize(
        self,
        server: str,
        tool: str,
        arguments: FrozenObject,
        context: StepContext,
    ) -> None:
        self.identities.append(context.caller.identity)


def _profiles(tool: types.Tool) -> McpProfiles:
    input_schema = tool.input_schema
    output_schema = tool.output_schema
    return McpProfiles.model_validate(
        {
            "servers": {
                "records": {
                    "transport": {
                        "type": "streamable_http",
                        "endpoint": "https://tools.example.test/mcp",
                    },
                    "identity_meta_key": _IDENTITY_KEY,
                    "catalog": {
                        "tools": {
                            "lookup": {
                                "input_schema": input_schema,
                                "output_schema": output_schema,
                                "effect": "read",
                            }
                        }
                    },
                    "concurrency": 2,
                    "queue_limit": 0,
                    "request_timeout": 2.0,
                }
            }
        },
        strict=True,
    )


def _write_workflow(directory: Path) -> None:
    (directory / "workflow.yaml").write_text(
        json.dumps(
            {
                "name": "mcp_privacy",
                "start": "main",
                "output": {"pointer": "/flows/main/result"},
                "flows": {
                    "main": {
                        "input": {"key": {"pointer": "/payload/key"}},
                        "definition": {
                            "output": {"pointer": "/steps/call/result"},
                            "steps": [
                                {
                                    "id": "call",
                                    "definition": {
                                        "type": "mcp",
                                        "server": "records",
                                        "tool": "lookup",
                                        "arguments": {"key": {"pointer": "/payload/key"}},
                                    },
                                }
                            ],
                        },
                        "transition": {"outcome": "completed"},
                    }
                },
            }
        )
    )


async def _child() -> None:
    logging_runtime = configure_logging()
    server = MCPServer("private-server-name")
    both_tools_entered = asyncio.Event()
    entered = 0

    @server.tool()
    async def lookup(key: str) -> dict[str, str]:
        """PRIVATE_TOOL_DESCRIPTION must never enter exported telemetry."""
        nonlocal entered
        entered += 1
        if entered == 2:
            both_tools_entered.set()
        await asyncio.wait_for(both_tools_entered.wait(), timeout=1)
        if key.endswith("fail"):
            raise RuntimeError(_EXCEPTION)
        return {"value": f"{_RESULT}:{key}"}

    # Catalog discovery precedes global installation, so only the two real
    # workflow calls are considered by the isolated exporter.
    async with Client(server, cache=None) as discovery:
        tools = (await discovery.list_tools()).tools
    assert [tool.name for tool in tools] == ["lookup"]
    profiles = _profiles(tools[0])

    labels = TelemetryLabels(
        services=frozenset({"foliqant"}),
        tools=frozenset({"lookup"}),
        workflows=frozenset({"mcp_privacy"}),
        steps=frozenset({"call", "done"}),
    )
    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource({"service.name": "foliqant"}),
        shutdown_on_exit=False,
    )
    provider.add_span_processor(SafeSpanProcessor(SimpleSpanProcessor(exporter), labels))
    trace_api.set_tracer_provider(provider)
    set_global_textmap(TraceContextTextMapPropagator())

    app = server.streamable_http_app(stateless_http=True, host="tools.example.test")
    captured_calls: list[dict[str, object]] = []

    async def capture(scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await app(scope, receive, send)
            return
        messages: list[dict[str, Any]] = []
        while True:
            message = await receive()
            messages.append(message)
            if not message.get("more_body", False):
                break
        body = b"".join(message.get("body", b"") for message in messages)
        request = json.loads(body)
        if request.get("method") == "tools/call":
            captured_calls.append(cast(dict[str, object], request))

        async def replay() -> dict[str, Any]:
            if messages:
                return messages.pop(0)
            return await receive()

        await app(scope, replay, send)

    clients: list[httpx2.AsyncClient] = []

    def create_client(
        *,
        auth: httpx2.Auth | None,
        timeout: httpx2.Timeout,
        request_hooks: list[Callable[[httpx2.Request], Awaitable[None]]],
    ) -> httpx2.AsyncClient:
        client = httpx2.AsyncClient(
            auth=auth,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            event_hooks={"request": request_hooks},
            transport=httpx2.ASGITransport(app=capture),
        )
        clients.append(client)
        return client

    factory = McpClientSessionFactory(
        profiles,
        credential_providers={},
        http_client_factory=create_client,
    )
    authorizer = _Allow()
    mcp = McpRuntime(profiles, factory, authorizer, trace_carrier=trace_carrier)

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        _write_workflow(directory)
        plan = compile_workflow(
            directory,
            model_aliases={},
            tool_catalogs={"records": profiles.servers["records"].catalog},
            handler_names=set(),
        )
        runner = WorkflowRunner(
            plan,
            executor=McpExecutor(mcp),
            validator=WorkflowSchemas(plan),
            admission=CapacityLimiter(concurrency=2, queue_limit=0),
            limits=ExecutionLimits(run_timeout=3, tool_timeout=2),
            observer=WorkflowTelemetry(provider, labels=labels),
        )

        parents = (
            "00-11111111111111111111111111111111-aaaaaaaaaaaaaaaa-01",
            "00-22222222222222222222222222222222-bbbbbbbbbbbbbbbb-01",
        )
        identities = (
            Identity("tenant-one", "principal-one"),
            Identity("tenant-two", "principal-two"),
        )

        async def run_one(index: int) -> object:
            identity = identities[index]
            key = f"{_ARGUMENT}-{'ok' if index == 0 else 'fail'}"
            envelope = accept_envelope(
                Envelope.model_validate(
                    {
                        "payload": {"key": key, "prompt": _PROMPT},
                        "metadata": {
                            "telemetry": {
                                "traceparent": parents[index],
                                "tracestate": f"vendor=state-{index}",
                            },
                            "arbitrary": _ARBITRARY_METADATA,
                        },
                    }
                ),
                identity,
            )
            token = context_api.attach(
                baggage.set_baggage("private-caller", f"PRIVATE_BAGGAGE_{index}")
            )
            try:
                return await runner.run(envelope, identity=identity)
            finally:
                context_api.detach(token)

        async with app.router.lifespan_context(app):
            results = await asyncio.gather(run_one(0), run_one(1))

    assert results[0].status == "completed"
    assert results[0].payload == {"value": f"{_RESULT}:{_ARGUMENT}-ok"}
    assert results[1].status == "failed"
    assert len(captured_calls) == 2
    assert set(authorizer.identities) == set(identities)
    assert len(clients) == 2 and all(client.is_closed for client in clients)

    spans = exporter.get_finished_spans()
    assert len(spans) >= 8
    trace_ids = {span.context.trace_id for span in spans}
    assert trace_ids == {int(parent.split("-")[1], 16) for parent in parents}
    assert sum(span.name == "foliqant.workflow" for span in spans) == 2
    assert sum(span.name == "foliqant.step" for span in spans) == 2
    assert sum(span.name == "foliqant.flow" for span in spans) == 2

    captured_by_trace: dict[int, dict[str, object]] = {}
    for request in captured_calls:
        params = cast(dict[str, object], request["params"])
        meta = cast(dict[str, object], params["_meta"])
        assert set(meta) <= {
            "traceparent",
            "tracestate",
            "progressToken",
            _IDENTITY_KEY,
            _MCP_PROTOCOL_VERSION,
            _MCP_CLIENT_INFO,
            _MCP_CLIENT_CAPABILITIES,
        }
        assert "baggage" not in meta and "arbitrary" not in meta
        assert meta[_MCP_PROTOCOL_VERSION] == "2026-07-28"
        assert meta[_MCP_CLIENT_INFO] == {"name": "mcp", "version": "0.1.0"}
        assert meta[_MCP_CLIENT_CAPABILITIES] == {}
        traceparent = cast(str, meta["traceparent"])
        trace_id = int(traceparent.split("-")[1], 16)
        captured_by_trace[trace_id] = meta
        identity = cast(dict[str, str], meta[_IDENTITY_KEY])
        expected_index = 0 if trace_id == int(parents[0].split("-")[1], 16) else 1
        assert identity == {
            "tenant_id": identities[expected_index].tenant_id,
            "principal_id": identities[expected_index].principal_id,
        }
    assert set(captured_by_trace) == trace_ids

    for trace_id, meta in captured_by_trace.items():
        request_parent_id = int(cast(str, meta["traceparent"]).split("-")[2], 16)
        step = next(
            span
            for span in spans
            if span.context.trace_id == trace_id
            and span.name == "foliqant.step"
            and span.attributes.get("foliqant.step.name") == "call"
        )
        client = next(
            span
            for span in spans
            if span.context.trace_id == trace_id
            and span.context.span_id == request_parent_id
            and span.kind is SpanKind.CLIENT
        )
        server_span = next(
            span
            for span in spans
            if span.context.trace_id == trace_id
            and span.kind is SpanKind.SERVER
            and span.parent is not None
            and span.parent.span_id == client.context.span_id
            and span.name == "tools/call lookup"
        )
        assert client.name == "tools/call"
        assert client.parent is not None and client.parent.span_id == step.context.span_id
        assert request_parent_id != step.context.span_id
        assert server_span.name == "tools/call lookup"

    serialized = "\n".join(span.to_json() for span in spans)
    for private in (
        _ARGUMENT,
        _RESULT,
        _PROMPT,
        _EXCEPTION,
        _ARBITRARY_METADATA,
        "PRIVATE_TOOL_DESCRIPTION",
        "PRIVATE_BAGGAGE",
        "tenant-one",
        "tenant-two",
        "principal-one",
        "principal-two",
        "private-server-name",
    ):
        assert private not in serialized
    assert all(not span.events for span in spans)
    assert all(span.status.description is None for span in spans)
    provider.shutdown()
    assert await asyncio.to_thread(logging_runtime.close, timeout=1)
    print(json.dumps({"spans": len(spans), "traces": len(trace_ids)}))


def test_real_mcp_trace_continuity_and_privacy_in_isolated_process() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--child"],
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    for private in (
        _ARGUMENT,
        _RESULT,
        _PROMPT,
        _EXCEPTION,
        _ARBITRARY_METADATA,
        "PRIVATE_TOOL_DESCRIPTION",
    ):
        assert private not in completed.stderr
    assert all(
        json.loads(line)["event"] == "external_event" for line in completed.stderr.splitlines()
    )
    report = json.loads(completed.stdout)
    assert report["traces"] == 2
    assert report["spans"] >= 14


if __name__ == "__main__" and sys.argv[1:] == ["--child"]:
    asyncio.run(_child())
