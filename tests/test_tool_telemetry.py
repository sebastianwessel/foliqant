"""Tool attempts use owned providers and preserve application exceptions/context."""

import asyncio

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from foliqant.adapters.telemetry.observation import WorkflowTelemetry, trace_carrier
from foliqant.adapters.telemetry.privacy import SafeSpanProcessor, TelemetryLabels
from foliqant.adapters.telemetry.tools import ToolTelemetry
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.observation import observe
from foliqant.ports.observation import TraceContext


@pytest.mark.parametrize(
    "error",
    [
        None,
        ServiceError(ErrorCode.TIMEOUT),
        asyncio.CancelledError(),
        RuntimeError("PRIVATE_ERROR"),
    ],
)
def test_attempt_spans_without_global_installation(error: BaseException | None) -> None:
    labels = TelemetryLabels(tools=frozenset({"lookup"}))
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SafeSpanProcessor(SimpleSpanProcessor(exporter), labels))
    observer = WorkflowTelemetry(provider, labels=labels)
    tools = ToolTelemetry(provider, labels=labels)
    global_provider = trace.get_tracer_provider()
    try:
        with observe(
            observer, "workflow", trace=TraceContext("00-" + "a" * 32 + "-" + "b" * 16 + "-01")
        ):
            with observe(observer, "workflow", flow="main"):
                with observe(observer, "workflow", flow="main", step="call"):
                    step_id = trace.get_current_span().get_span_context().span_id
                    try:
                        with tools.observe("lookup", attempt=2):
                            carrier = trace_carrier()
                            if error is not None:
                                raise error
                    except BaseException as actual:
                        assert actual is error
                    assert trace.get_current_span().get_span_context().span_id == step_id
        spans = exporter.get_finished_spans()
        tool = next(span for span in spans if span.name == "execute_tool lookup")
        assert tool.parent.span_id == step_id
        assert tool.context.span_id == int(carrier["traceparent"].split("-")[2], 16)
        assert tool.attributes["foliqant.request.attempt"] == 2
        assert tool.status.status_code is (StatusCode.ERROR if error else StatusCode.UNSET)
        assert "PRIVATE" not in "".join(span.to_json() for span in spans)
        assert trace.get_tracer_provider() is global_provider
        assert not trace.get_current_span().get_span_context().is_valid
    finally:
        provider.shutdown()


def test_tool_cleanup_failure_does_not_replace_business_exception(monkeypatch) -> None:
    from opentelemetry import context

    provider = TracerProvider(shutdown_on_exit=False)
    tools = ToolTelemetry(provider, labels=TelemetryLabels())
    detach = context.detach

    def broken_detach(token):
        detach(token)
        raise RuntimeError("PRIVATE_OBSERVATION_ERROR")

    monkeypatch.setattr(context, "detach", broken_detach)
    business_error = ServiceError(ErrorCode.INVALID_OUTPUT)
    try:
        with pytest.raises(ServiceError) as raised:
            with tools.observe("unknown", attempt=1):
                raise business_error
        assert raised.value is business_error
        assert not trace.get_current_span().get_span_context().is_valid
    finally:
        provider.shutdown()
