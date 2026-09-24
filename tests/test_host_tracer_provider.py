"""A host-owned OpenTelemetry tracer provider receives the runtime's spans."""

import pytest
from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_handler_contracts import _project, _route

from foliqant import Envelope, RuntimePlugins, open_application, prepare_application
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.errors import ErrorCode, ServiceError


def _host() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


async def test_runtime_spans_join_the_host_provider_without_owning_it(tmp_path):
    prepared = prepare_application(
        _project(tmp_path), handlers={"route_message": HandlerRegistration(_route)}
    )
    provider, exporter = _host()
    global_before = trace_api.get_tracer_provider()
    host_tracer = provider.get_tracer("host")
    with host_tracer.start_as_current_span("host.request") as parent:
        async with open_application(
            prepared, environment={}, plugins=RuntimePlugins(tracer_provider=provider)
        ) as app:
            result = await app.run("demo", Envelope(payload={"message": "invoice 42"}))
    assert result.execution.status == "completed"
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert {"workflow demo", "flow main"} <= set(spans)
    assert any(name.startswith("step ") and name.endswith(" (handler)") for name in spans)
    workflow = spans["workflow demo"]
    # Runtime spans are children of the host's current span in the host's trace.
    assert workflow.context.trace_id == parent.get_span_context().trace_id
    assert workflow.parent is not None
    assert workflow.parent.span_id == parent.get_span_context().span_id
    assert result.execution.trace is not None
    assert result.execution.trace.trace_id == f"{workflow.context.trace_id:032x}"
    # Runtime spans are built from configured labels only, also without an export filter.
    assert "invoice" not in "".join(span.to_json() for span in exporter.get_finished_spans())
    assert spans["flow main"].attributes["foliqant.flow.name"] == "main"
    # The runtime neither installed globals nor shut the host provider down.
    assert trace_api.get_tracer_provider() is global_before
    with host_tracer.start_as_current_span("host.after"):
        pass
    assert "host.after" in {span.name for span in exporter.get_finished_spans()}
    provider.shutdown()


async def test_a_host_provider_excludes_global_installation(tmp_path):
    prepared = prepare_application(
        _project(tmp_path), handlers={"route_message": HandlerRegistration(_route)}
    )
    provider, _ = _host()
    with pytest.raises(ServiceError) as error:
        async with open_application(
            prepared,
            environment={},
            plugins=RuntimePlugins(tracer_provider=provider),
            install_global_telemetry=True,
        ):
            pass
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION
    provider.shutdown()
