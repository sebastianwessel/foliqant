"""Privacy and accounting tests for optional model telemetry."""

from collections.abc import Callable
from typing import Any, cast

import pytest
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
        start=step.name,
        default_model=None,
        input_schema_path=None,
        input_schema=None,
        schema_resources=(SchemaResourcePlan("schemas/result.json", step.output_schema),),
        output=None,
        steps=(step,),
        location=SourceLocation("workflow.yaml", 1, 1),
    )


def _context(step: LlmStepPlan) -> StepContext:
    import asyncio

    return StepContext(
        execution_id="execution-id",
        workflow="test-workflow",
        revision="a" * 64,
        step_id=step.name,
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
