"""Privacy and accounting tests for optional model telemetry."""

from collections.abc import Callable
from typing import Any, cast

import pytest
from flow_fixtures import operation_flow
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from foliqant.adapters.models import ModelBinding, ModelExecutor
from foliqant.adapters.telemetry.models import ModelTelemetry
from foliqant.adapters.telemetry.privacy import SafeSpanProcessor, TelemetryLabels
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.core.admission import CapacityLimiter
from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import CallerContext
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import LlmStepPlan, SchemaResourcePlan, SourceLocation, WorkflowPlan
from foliqant.ports.execution import StepContext

_PRIVATE = "PRIVATE customer 1234 tool schema and provider failure"
_MODEL = "reviewed-test-model"
_LOCATION = SourceLocation("steps/test.yaml", 1, 1)


def _frozen(value: object) -> FrozenObject:
    return cast(FrozenObject, freeze_json(value))


def _step() -> LlmStepPlan:
    schema = _frozen(
        {
            "type": "object",
            "description": _PRIVATE,
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
    )
    return LlmStepPlan(
        name="generate",
        type="llm",
        location=_LOCATION,
        model="configured-alias",
        instructions=f"Do not export {_PRIVATE}",
        output_kind="schema",
        output_schema_path="schemas/result.json",
        output_schema=schema,
    )


def _plan(step: LlmStepPlan) -> WorkflowPlan:
    assert step.output_schema is not None
    return WorkflowPlan(
        name="test-workflow",
        revision="a" * 64,
        start="main",
        default_model=None,
        input_schema_path=None,
        input_schema=None,
        schema_resources=(SchemaResourcePlan("schemas/result.json", step.output_schema),),
        output=None,
        flows=(operation_flow(step),),
        location=SourceLocation("workflow.yaml", 1, 1),
    )


def _context(step: LlmStepPlan) -> StepContext:
    import asyncio

    return StepContext(
        execution_id="execution-id",
        workflow="test-workflow",
        revision="a" * 64,
        step_id=step.name,
        flow_id="main",
        caller=CallerContext(Identity("tenant", "principal"), _frozen({})),
        deadline=asyncio.get_running_loop().time() + 2,
        model_timeout=1,
        tool_timeout=1,
        budget=StepBudget(model_requests=1, tool_calls=0),
    )


def _binding(function: Callable[..., Any]) -> ModelBinding:
    return ModelBinding(
        model=FunctionModel(function, model_name=_MODEL),
        settings={},
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        output_mode="tool",
    )


def _telemetry() -> tuple[
    ModelTelemetry,
    TracerProvider,
    InMemorySpanExporter,
    InMemoryMetricReader,
]:
    tracer_provider = TracerProvider()
    span_exporter = InMemorySpanExporter()
    labels = TelemetryLabels(models=frozenset({_MODEL}), providers=frozenset({"function"}))
    tracer_provider.add_span_processor(
        SafeSpanProcessor(SimpleSpanProcessor(span_exporter), labels)
    )
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    return (
        ModelTelemetry(tracer_provider, meter_provider, labels),
        tracer_provider,
        span_exporter,
        metric_reader,
    )


def _metrics(reader: InMemoryMetricReader) -> dict[str, Any]:
    data = reader.get_metrics_data()
    assert data is not None
    return {
        metric.name: metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


async def test_one_safe_client_span_preserves_parent_and_uses_private_settings() -> None:
    telemetry, tracer_provider, spans, metrics = _telemetry()
    step = _step()

    async def model(_messages: Any, info: Any) -> ModelResponse:
        assert info.output_tools
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"value": {"answer": 42}})],
            usage=RequestUsage(input_tokens=7, output_tokens=3),
        )

    binding = _binding(model)
    instrumented = telemetry.instrument(binding.model)
    settings = instrumented.instrumentation_settings
    assert settings.version == 6
    assert settings.include_content is False
    assert settings.include_binary_content is False
    assert settings.include_model_request_parameters is False

    executor = ModelExecutor(
        {"configured-alias": binding},
        WorkflowSchemas(_plan(step)),
        telemetry=telemetry,
    )
    tracer = tracer_provider.get_tracer("foliqant.step")
    with tracer.start_as_current_span("parent") as parent:
        parent_id = parent.get_span_context().span_id
        outcome = await executor.execute(
            step,
            _frozen({"correspondence": _PRIVATE}),
            _context(step),
        )

    assert thaw_json(outcome.result) == {"answer": 42}
    exported = spans.get_finished_spans()
    client_spans = [span for span in exported if span.kind is SpanKind.CLIENT]
    assert len(client_spans) == 1
    assert client_spans[0].name == f"chat {_MODEL}"
    assert client_spans[0].parent is not None
    assert client_spans[0].parent.span_id == parent_id
    serialized = repr(exported)
    assert _PRIVATE not in serialized
    assert "gen_ai.tool.definitions" not in serialized
    assert "model_request_parameters" not in serialized
    assert all(not span.events for span in exported)

    observed = _metrics(metrics)
    assert set(observed) == {
        "gen_ai.client.operation.duration",
        "gen_ai.client.token.usage",
    }
    token_points = observed["gen_ai.client.token.usage"].data.data_points
    assert {point.attributes["gen_ai.token.type"]: point.sum for point in token_points} == {
        "input": 7,
        "output": 3,
    }
    assert all(point.attributes["gen_ai.operation.name"] == "chat" for point in token_points)


async def test_embedded_bootstrap_parent_chain_for_each_model_attempt(
    tmp_path, monkeypatch
) -> None:
    from contextlib import asynccontextmanager

    from opentelemetry import trace
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.messages import TextPart
    from test_bootstrap import settings

    from foliqant.adapters.telemetry.observation import trace_carrier
    from foliqant.bootstrap import RuntimePlugins, open_application, prepare_application
    from foliqant.contracts.envelope import Envelope
    from foliqant.core.retry import RetryPolicy
    from foliqant.ports.observation import TraceContext

    path = settings(
        tmp_path,
        "type: llm\nmodel: local\ninstructions: 'PRIVATE_PROMPT'\ninput: {}\noutput: text\n",
        "models:\n  local:\n    provider: openai_compatible\n    model: reviewed-test-model\n"
        "    base_url: https://provider.example/v1\n    output_mode: tool\n"
        "telemetry:\n  service_name: test\n"
        "  traces_endpoint: https://collector.example/v1/traces\n"
        "  span_schedule_delay: 3600.0\n",
    )
    captured = InMemorySpanExporter()
    monkeypatch.setattr(
        "foliqant.adapters.telemetry.runtime.OTLPSpanExporter", lambda **kwargs: captured
    )
    seen = []
    closed = False

    async def request(messages, info):
        seen.append(trace_carrier())
        if len(seen) == 1:
            raise ModelHTTPError(503, _MODEL, body="PRIVATE_PROVIDER_ERROR")
        return ModelResponse(parts=[TextPart("PRIVATE_RESPONSE")])

    @asynccontextmanager
    async def factory(profiles, *, environment):
        nonlocal closed
        try:
            yield {
                "local": ModelBinding(
                    model=FunctionModel(request, model_name=_MODEL),
                    settings={},
                    admission=CapacityLimiter(concurrency=1, queue_limit=0),
                    output_mode="tool",
                    retry=RetryPolicy(max_attempts=2, initial_delay_seconds=0, max_delay_seconds=0),
                )
            }
        finally:
            closed = True

    previous_global = trace.get_tracer_provider()
    async with open_application(
        prepare_application(path), environment={}, plugins=RuntimePlugins(model_factory=factory)
    ) as application:
        result = await application.run(
            "demo",
            Envelope(payload={}),
            transport_trace=TraceContext("00-" + "a" * 32 + "-" + "b" * 16 + "-01"),
        )
        assert result.execution.status == "completed"
        assert result.execution.usage.model_requests == 2
    assert closed
    assert trace.get_tracer_provider() is previous_global
    spans = captured.get_finished_spans()
    workflow = next(
        span for span in spans if span.instrumentation_scope.name == "foliqant.workflow"
    )
    flow = next(span for span in spans if span.instrumentation_scope.name == "foliqant.flow")
    step = next(span for span in spans if span.instrumentation_scope.name == "foliqant.step")
    calls = [span for span in spans if span.kind is SpanKind.CLIENT]
    assert len(calls) == len(seen) == 2
    assert workflow.parent.span_id == int("b" * 16, 16)
    assert flow.parent.span_id == workflow.context.span_id
    assert step.parent.span_id == flow.context.span_id
    assert all(call.parent.span_id == step.context.span_id for call in calls)
    assert {call.context.span_id for call in calls} == {
        int(carrier["traceparent"].split("-")[2], 16) for carrier in seen
    }
    assert all(span.context.trace_id == int("a" * 32, 16) for span in spans)
    assert "PRIVATE" not in "".join(span.to_json() for span in spans)


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        # FunctionModel estimates usage when has_values() is false. A benign
        # detail marks these fixtures as provider-returned while the production
        # accounting path deliberately ignores arbitrary detail keys.
        (RequestUsage(details={"provider_reported": 1}), {}),
        (
            RequestUsage(details={"provider_reported": 1}, input_tokens=0, output_tokens=0),
            {"input": 0, "output": 0},
        ),
        (
            RequestUsage(
                input_tokens=9,
                output_tokens=5,
                cache_read_tokens=4,
                output_reasoning_tokens=2,
            ),
            {"input": 9, "output": 5},
        ),
        (
            RequestUsage(input_tokens=2**53, output_tokens=1),
            {"output": 1},
        ),
    ],
)
async def test_token_metrics_preserve_unknown_zero_and_do_not_duplicate_subsets(
    usage: RequestUsage,
    expected: dict[str, int],
) -> None:
    telemetry, _tracer_provider, _spans, reader = _telemetry()
    step = _step()

    async def model(_messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"value": {"answer": 42}})],
            usage=usage,
        )

    await ModelExecutor(
        {"configured-alias": _binding(model)},
        WorkflowSchemas(_plan(step)),
        telemetry=telemetry,
    ).execute(step, _frozen({}), _context(step))

    observed = _metrics(reader)
    token_metric = observed.get("gen_ai.client.token.usage")
    if not expected:
        assert token_metric is None or not token_metric.data.data_points
        return
    assert token_metric is not None
    assert {
        point.attributes["gen_ai.token.type"]: point.sum for point in token_metric.data.data_points
    } == expected


async def test_telemetry_recording_failure_does_not_change_model_outcome() -> None:
    telemetry, _tracer_provider, _spans, _reader = _telemetry()
    step = _step()

    class BrokenHistogram:
        def record(self, _amount: object, _attributes: object) -> None:
            raise RuntimeError(_PRIVATE)

    telemetry._duration = BrokenHistogram()  # type: ignore[assignment]

    async def model(_messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"value": {"answer": 42}})],
            usage=RequestUsage(input_tokens=1, output_tokens=1),
        )

    outcome = await ModelExecutor(
        {"configured-alias": _binding(model)},
        WorkflowSchemas(_plan(step)),
        telemetry=telemetry,
    ).execute(step, _frozen({}), _context(step))
    assert thaw_json(outcome.result) == {"answer": 42}


async def test_provider_exception_is_removed_from_exported_model_span() -> None:
    telemetry, _tracer_provider, spans, reader = _telemetry()
    step = _step()

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        raise RuntimeError(_PRIVATE)

    with pytest.raises(ServiceError) as error:
        await ModelExecutor(
            {"configured-alias": _binding(model)},
            WorkflowSchemas(_plan(step)),
            telemetry=telemetry,
        ).execute(step, _frozen({}), _context(step))
    assert error.value.code == ErrorCode.DEPENDENCY_FAILURE
    assert _PRIVATE not in repr(spans.get_finished_spans())
    assert all(not span.events for span in spans.get_finished_spans())
    duration_metric = _metrics(reader)["gen_ai.client.operation.duration"]
    assert duration_metric.data.data_points[0].attributes["error.type"] == (
        ErrorCode.DEPENDENCY_FAILURE.value
    )
    assert "gen_ai.client.token.usage" not in _metrics(reader)


@pytest.mark.parametrize(
    ("model_id", "labelled"), [("reviewed-test-model", True), ("model@2026-09", False)]
)
async def test_environment_model_references_resolve_to_telemetry_labels(
    tmp_path, monkeypatch, caplog, model_id, labelled
) -> None:
    """Regression: `$MODEL_ID` profiles made activation reject the telemetry labels."""
    import logging
    from contextlib import asynccontextmanager

    from pydantic_ai.messages import TextPart
    from test_bootstrap import settings

    from foliqant.bootstrap import RuntimePlugins, open_application, prepare_application
    from foliqant.contracts.envelope import Envelope

    path = settings(
        tmp_path,
        "type: llm\nmodel: local\ninstructions: 'Summarize.'\ninput: {}\noutput: text\n",
        "models:\n  local:\n    provider: openai_compatible\n    model: $MODEL_ID\n"
        "    base_url: https://provider.example/v1\n    output_mode: tool\n"
        "telemetry:\n  service_name: test\n"
        "  traces_endpoint: https://collector.example/v1/traces\n"
        "  span_schedule_delay: 3600.0\n",
    )
    captured = InMemorySpanExporter()
    monkeypatch.setattr(
        "foliqant.adapters.telemetry.runtime.OTLPSpanExporter", lambda **kwargs: captured
    )

    async def request(messages, info):
        return ModelResponse(parts=[TextPart("summary")])

    @asynccontextmanager
    async def factory(profiles, *, environment):
        assert profiles.models["local"].model == model_id
        yield {
            "local": ModelBinding(
                model=FunctionModel(request, model_name=model_id),
                settings={},
                admission=CapacityLimiter(concurrency=1, queue_limit=0),
                output_mode="tool",
            )
        }

    caplog.set_level(logging.WARNING)
    async with open_application(
        prepare_application(path),
        environment={"MODEL_ID": model_id},
        plugins=RuntimePlugins(model_factory=factory),
    ) as application:
        result = await application.run("demo", Envelope(payload={}))
    assert result.execution.status == "completed"
    assert result.execution.trace is not None
    spans = captured.get_finished_spans()
    assert {span.name for span in spans} >= {"workflow demo", "flow main", "step first (llm)"}
    client = [span for span in spans if span.kind is SpanKind.CLIENT]
    assert {span.name for span in client} == ({f"chat {model_id}"} if labelled else {"chat"})
    assert client, "model request spans must still be exported"
    models = {span.attributes.get("gen_ai.request.model") for span in client}
    assert models == ({model_id} if labelled else {None})
    assert "$MODEL_ID" not in "".join(span.to_json() for span in spans)
    dropped = [record for record in caplog.records if record.msg == "telemetry_labels_dropped"]
    assert bool(dropped) is not labelled
