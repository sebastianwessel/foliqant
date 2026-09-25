"""Every failure family is an ERROR span with its code on every level; review is not."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from foliqant import Envelope, RuntimePlugins, open_application, prepare_application
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.adapters.models import ModelBinding
from foliqant.core.admission import CapacityLimiter
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, freeze_json
from foliqant.core.retry import RetryPolicy
from foliqant.ports.execution import StepContext

_ANSWER = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _project(tmp_path: Path, step: dict[str, Any], *, handlers: str = "") -> Path:
    root = tmp_path / "workflows/demo"
    root.mkdir(parents=True)
    (root / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "start": "main",
                "flows": {
                    "main": {
                        "input": {"text": {"pointer": "/payload/text"}},
                        "transition": {"outcome": "completed"},
                        "on_unresolved": {"outcome": "needs_review"},
                        "definition": {"steps": [{"id": "first", "definition": step}]},
                    }
                },
            }
        )
    )
    config = tmp_path / "foliqant.yaml"
    config.write_text(
        "workflows: {demo: workflows/demo}\n"
        "execution: {model_timeout: 0.2, run_timeout: 5}\n"
        "models:\n  local:\n    provider: openai_compatible\n    model: test-model\n"
        "    base_url: http://127.0.0.1:1234/v1\n    allow_insecure_http: true\n"
        "    output_mode: native\n    output_retries: 1\n" + handlers
    )
    return config


def _llm() -> dict[str, Any]:
    return {
        "type": "llm",
        "model": "local",
        "instructions": "Answer.",
        "input": {"text": {"pointer": "/payload/text"}},
        "output": {"schema": _ANSWER},
    }


def _factory(respond: Callable[..., Any]) -> Any:
    @asynccontextmanager
    async def factory(profiles: Any, *, environment: Any) -> AsyncIterator[dict[str, Any]]:
        yield {
            "local": ModelBinding(
                FunctionModel(respond, model_name="test-model"),
                {},
                CapacityLimiter(concurrency=1, queue_limit=0),
                "native",
                retry=RetryPolicy(2, 0, 0),
            )
        }

    return factory


async def _spans(
    config: Path,
    respond: Callable[..., Any] | None = None,
    *,
    handlers: dict[str, HandlerRegistration] | None = None,
    cancel: bool = False,
) -> tuple[Any, dict[str, list[ReadableSpan]]]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    registered = handlers or {}
    if registered:
        from handler_contracts import declare

        declare(config, registered)
    prepared = prepare_application(config, handlers=registered)
    plugins = RuntimePlugins(
        model_factory=_factory(respond or (lambda *_: None)), tracer_provider=provider
    )
    result = None
    async with open_application(prepared, environment={}, plugins=plugins) as app:
        run = app.run("demo", Envelope(payload={"text": "PRIVATE"}))
        if cancel:
            task = asyncio.create_task(run)
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await run
    grouped: dict[str, list[ReadableSpan]] = {}
    for span in exporter.get_finished_spans():
        kind = span.name.split(" ", 1)[0]
        grouped.setdefault(kind, []).append(span)
    provider.shutdown()
    assert "PRIVATE" not in "".join(
        json.dumps(dict(span.attributes or {})) for spans in grouped.values() for span in spans
    )
    return result, grouped


def _error(span: ReadableSpan) -> str | None:
    if span.status.status_code is not StatusCode.ERROR:
        return None
    value = (span.attributes or {}).get("error.type")
    assert isinstance(value, str), span.name
    return value


def _levels(spans: dict[str, list[ReadableSpan]]) -> tuple[str | None, str | None, str | None]:
    (workflow,) = spans["workflow"]
    (flow,) = spans["flow"]
    (step,) = spans["step"]
    return _error(step), _error(flow), _error(workflow)


def _ok(value: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(json.dumps({"value": value}))],
        usage=RequestUsage(input_tokens=5, output_tokens=5),
    )


@pytest.mark.parametrize(
    "status,code",
    [(503, "dependency_overloaded"), (429, "rate_limited"), (500, "dependency_failure")],
)
async def test_retried_then_successful_request_is_an_error_attempt_and_an_ok_step(
    tmp_path, status, code
):
    calls = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelHTTPError(status, "test-model", "PRIVATE", headers={"Retry-After": "0"})
        return _ok({"answer": 1})

    result, spans = await _spans(_project(tmp_path, _llm()), respond)
    assert result.execution.status == "completed"
    assert [_error(span) for span in spans["chat"]] == [code, None]
    assert [span.attributes["foliqant.request.attempt"] for span in spans["chat"]] == [1, 2]
    assert _levels(spans) == (None, None, None)


@pytest.mark.parametrize(
    "respond,code",
    [
        (
            lambda *_: (_ for _ in ()).throw(ModelHTTPError(429, "test-model", "PRIVATE")),
            "rate_limited",
        ),
        (
            lambda *_: (_ for _ in ()).throw(
                ModelHTTPError(400, "test-model", {"code": "context_length_exceeded"})
            ),
            "context_limit_exceeded",
        ),
        (
            lambda *_: ModelResponse(
                parts=[TextPart("{")],
                finish_reason="length",
                usage=RequestUsage(
                    input_tokens=5, output_tokens=8, details={"reasoning_tokens": 8}
                ),
            ),
            "output_limit_reached",
        ),
        (lambda *_: _ok({"answer": "PRIVATE"}), "invalid_output"),
    ],
    ids=["exhausted_429", "context_window", "output_limit", "invalid_after_output_retry"],
)
async def test_model_failures_are_errors_on_request_step_flow_and_run(tmp_path, respond, code):
    result, spans = await _spans(_project(tmp_path, _llm()), respond)
    assert result.execution.status == "failed" and result.execution.error.code.value == code
    assert _levels(spans) == (code, code, code)
    chat = [_error(span) for span in spans["chat"]]
    if code == "invalid_output":
        # The requests succeeded; the output failed validation, then its correction did too.
        assert chat == [None, None]
        assert spans["chat"][1].attributes["foliqant.request.output_retry"] is True
    else:
        assert chat and all(item == code for item in chat)


async def test_request_timeout_is_an_error_on_every_level(tmp_path):
    async def respond(messages: Any, info: Any) -> ModelResponse:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    result, spans = await _spans(_project(tmp_path, _llm()), respond)
    assert result.execution.error.code.value == "request_timeout"
    assert [_error(span) for span in spans["chat"]] == ["request_timeout"]
    assert _levels(spans) == ("request_timeout",) * 3


async def test_handler_exception_is_an_error_and_review_is_not(tmp_path):
    async def broken(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        raise RuntimeError("PRIVATE")

    async def review(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome(freeze_json({}), needs_review=True)

    step = {"type": "handler", "handler": "work", "input": {}}
    schema = freeze_json({"type": "object"})
    for handler, expected, status in (
        (broken, ("handler_failed",) * 3, "failed"),
        (review, (None, None, None), "needs_review"),
    ):
        folder = tmp_path / status
        registration = HandlerRegistration(handler, schema, schema)
        result, spans = await _spans(_project(folder, step), handlers={"work": registration})
        assert result.execution.status == status
        assert _levels(spans) == expected


async def test_cancellation_is_an_error_on_every_level(tmp_path):
    async def respond(messages: Any, info: Any) -> ModelResponse:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    config = _project(tmp_path, _llm())
    config.write_text(config.read_text().replace("model_timeout: 0.2", "model_timeout: 5"))
    _, spans = await _spans(config, respond, cancel=True)
    assert _levels(spans) == ("cancelled",) * 3
    assert [_error(span) for span in spans["chat"]] == ["cancelled"]


async def test_mcp_tool_error_is_an_error_tool_span_and_step() -> None:
    from mcp import Client
    from mcp.server.mcpserver import MCPServer
    from test_mcp_runtime import Allow, InProcessFactory, context, profiles

    from foliqant.adapters.mcp.runtime import McpRuntime
    from foliqant.adapters.telemetry.observation import WorkflowTelemetry
    from foliqant.adapters.telemetry.privacy import SafeSpanProcessor, TelemetryLabels
    from foliqant.adapters.telemetry.tools import ToolTelemetry
    from foliqant.core.errors import ErrorCode, ServiceError
    from foliqant.core.observation import observe

    server = MCPServer("Test")

    @server.tool()
    async def lookup(key: str) -> dict[str, str]:
        raise RuntimeError("PRIVATE server diagnostic")

    async with Client(server, cache=None) as client:
        catalog = (await client.list_tools()).tools
    labels = TelemetryLabels(tools=frozenset({"lookup"}))
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SafeSpanProcessor(SimpleSpanProcessor(exporter), labels))
    runtime = McpRuntime(
        profiles(catalog),
        InProcessFactory(server),
        Allow(),
        telemetry=ToolTelemetry(provider, labels=labels),
    )
    observer = WorkflowTelemetry(provider, labels=labels)
    with pytest.raises(ServiceError) as raised:
        with observe(observer, "workflow"):
            with observe(observer, "workflow", flow="main"):
                with observe(observer, "workflow", flow="main", step="call"):
                    async with runtime.open("records", ("lookup",), context()) as tools:
                        await tools.call("lookup", freeze_json({"key": "x"}))  # type: ignore[arg-type]
    assert raised.value.code is ErrorCode.TOOL_ERROR
    spans = exporter.get_finished_spans()
    (tool,) = [span for span in spans if span.name == "execute_tool lookup"]
    assert _error(tool) == "tool_error"
    assert {_error(span) for span in spans if span.name != "execute_tool lookup"} == {"tool_error"}
    assert "PRIVATE" not in "".join(span.to_json() for span in spans)
    provider.shutdown()
