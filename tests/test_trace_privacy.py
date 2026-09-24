"""Raw spans must be sanitized before an exporter processor can retain them."""

from collections.abc import Sequence

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import (
    Link,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
    TraceState,
)

from foliqant.adapters.telemetry.privacy import SafeSpanProcessor, TelemetryLabels
from foliqant.core.errors import ErrorCode

_SECRET = "SECRET_CUSTOMER_TOKEN"


def _context(trace_id: int, span_id: int, *, remote: bool = False) -> SpanContext:
    return SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        is_remote=remote,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState((("private", "customer-secret"),)),
    )


class RecordingProcessor(SpanProcessor):
    def __init__(self) -> None:
        self.ended: list[ReadableSpan] = []

    def on_end(self, span: ReadableSpan) -> None:
        self.ended.append(span)


def _labels() -> TelemetryLabels:
    return TelemetryLabels(
        services=frozenset({"foliqant"}),
        models=frozenset({"reviewed/model-v1"}),
        providers=frozenset({"openai"}),
        tools=frozenset({"lookup_invoice"}),
        workflows=frozenset({"invoice_review"}),
        steps=frozenset({"classify"}),
        flows=frozenset({"triage"}),
    )


def test_actual_readable_span_is_cloned_and_sanitized_before_delegate() -> None:
    context = _context(1, 2)
    parent = _context(1, 3, remote=True)
    link_context = _context(4, 5, remote=True)
    raw = ReadableSpan(
        name=f"chat {_SECRET}",
        context=context,
        parent=parent,
        resource=Resource(
            {"service.name": "foliqant", "host.name": _SECRET},
            schema_url=f"https://{_SECRET}.invalid/schema",
        ),
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "reviewed/model-v1",
            "gen_ai.response.model": _SECRET,
            "gen_ai.provider.name": "openai",
            "gen_ai.system": "openai",
            "gen_ai.tool.name": "lookup_invoice",
            "gen_ai.tool.definitions": _SECRET,
            "gen_ai.input.messages": _SECRET,
            "gen_ai.output.messages": _SECRET,
            "gen_ai.usage.input_tokens": 12,
            "gen_ai.usage.output_tokens": None,
            "gen_ai.usage.cache_read.input_tokens": True,
            "gen_ai.usage.cache_creation.input_tokens": -1,
            "gen_ai.usage.reasoning.output_tokens": 2**53,
            "foliqant.workflow.name": "invoice_review",
            "foliqant.step.name": "classify",
            "foliqant.outcome": "completed",
            "error.type": ErrorCode.TIMEOUT.value,
            "tenant_id": "tenant-private-123",
            "principal_id": "private.person@example.invalid",
            "http.url": f"https://example.invalid/?token={_SECRET}",
        },
        events=(
            Event(
                "exception",
                {"exception.message": _SECRET, "exception.stacktrace": f"trace: {_SECRET}"},
            ),
        ),
        links=(Link(link_context, {"private.link": _SECRET}),),
        kind=SpanKind.CLIENT,
        status=Status(StatusCode.ERROR, _SECRET),
        start_time=10,
        end_time=20,
        instrumentation_scope=InstrumentationScope(
            "pydantic-ai",
            version=_SECRET,
            schema_url=f"https://{_SECRET}.invalid/scope",
            attributes={"private.scope": _SECRET},
        ),
    )
    delegate = RecordingProcessor()

    SafeSpanProcessor(delegate, _labels()).on_end(raw)

    assert len(delegate.ended) == 1
    safe = delegate.ended[0]
    assert safe is not raw
    assert safe.name == "chat reviewed/model-v1"
    assert dict(safe.attributes) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "reviewed/model-v1",
        "gen_ai.provider.name": "openai",
        "gen_ai.tool.name": "lookup_invoice",
        "gen_ai.usage.input_tokens": 12,
        "foliqant.workflow.name": "invoice_review",
        "foliqant.step.name": "classify",
        "foliqant.outcome": "completed",
        "error.type": ErrorCode.TIMEOUT.value,
    }
    assert dict(safe.resource.attributes) == {"service.name": "foliqant"}
    assert safe.resource.schema_url == ""
    assert safe.events == ()
    assert len(safe.links) == 1
    assert safe.links[0].context.trace_id == link_context.trace_id
    assert safe.links[0].context.trace_flags == link_context.trace_flags
    assert not safe.links[0].context.trace_state
    assert safe.links[0].attributes is None
    assert safe.context is not None
    assert safe.context.trace_id == context.trace_id
    assert safe.context.span_id == context.span_id
    assert safe.context.trace_flags == context.trace_flags
    assert not safe.context.trace_state
    assert safe.parent is not None
    assert safe.parent.is_remote
    assert not safe.parent.trace_state
    assert safe.status.status_code is StatusCode.ERROR
    assert safe.status.description is None
    assert safe.kind is SpanKind.CLIENT
    assert safe.start_time == 10
    assert safe.end_time == 20
    assert safe.instrumentation_scope == InstrumentationScope("pydantic-ai")
    assert _SECRET not in safe.to_json()


@pytest.mark.parametrize(
    ("scope", "attributes", "expected_name"),
    [
        (
            "pydantic-ai",
            {"gen_ai.operation.name": "chat", "gen_ai.request.model": "reviewed/model-v1"},
            "chat reviewed/model-v1",
        ),
        (
            "pydantic-ai",
            {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup_invoice"},
            "execute_tool lookup_invoice",
        ),
        ("pydantic-ai", {"gen_ai.operation.name": _SECRET}, "gen_ai.operation"),
        (
            "mcp-python-sdk",
            {"mcp.method.name": "tools/call", "gen_ai.tool.name": "lookup_invoice"},
            "tools/call lookup_invoice",
        ),
        ("mcp-python-sdk", {"mcp.method.name": _SECRET}, "mcp.operation"),
        (
            "foliqant.workflow",
            {"foliqant.workflow.name": "invoice_review"},
            "workflow invoice_review",
        ),
        ("foliqant.workflow", {"foliqant.workflow.name": _SECRET}, "workflow"),
        ("foliqant.step", {"foliqant.step.name": "classify"}, "step classify"),
        (
            "foliqant.step",
            {"foliqant.step.name": "classify", "foliqant.step.kind": "decision"},
            "step classify (decision)",
        ),
        ("foliqant.step", {"foliqant.step.name": _SECRET, "foliqant.step.kind": _SECRET}, "step"),
        ("foliqant.flow", {"foliqant.flow.name": "triage"}, "flow triage"),
        (
            "foliqant.flow",
            {"foliqant.flow.name": "triage", "foliqant.flow.attempt": 1},
            "flow triage",
        ),
        (
            "foliqant.flow",
            {"foliqant.flow.name": "triage", "foliqant.flow.attempt": 3},
            "flow triage #3",
        ),
        (
            "foliqant.flow",
            {
                "foliqant.flow.name": "triage",
                "foliqant.collection.index": 4,
                "foliqant.flow.attempt": 2,
            },
            "flow triage [item 4] #2",
        ),
        (
            "foliqant.flow",
            {"foliqant.flow.name": "triage", "foliqant.flow.attempt": 999},
            "flow triage",
        ),
        (_SECRET, {"private": _SECRET}, "external.operation"),
    ],
)
def test_span_names_are_fixed_by_reviewed_scope_and_operation(
    scope: str,
    attributes: dict[str, str | int],
    expected_name: str,
) -> None:
    delegate = RecordingProcessor()
    processor = SafeSpanProcessor(delegate, _labels())
    processor.on_end(
        ReadableSpan(
            name=_SECRET,
            attributes=attributes,
            instrumentation_scope=InstrumentationScope(scope),
        )
    )

    assert delegate.ended[0].name == expected_name
    assert _SECRET not in delegate.ended[0].to_json()


def test_unknown_span_keeps_ids_but_no_arbitrary_content() -> None:
    context = _context(9, 10)
    delegate = RecordingProcessor()
    SafeSpanProcessor(delegate, _labels()).on_end(
        ReadableSpan(
            name=_SECRET,
            context=context,
            attributes={"payload": _SECRET, "error.type": _SECRET},
            resource=Resource({"service.name": _SECRET, "secret": _SECRET}),
            status=Status(StatusCode.ERROR, _SECRET),
            instrumentation_scope=InstrumentationScope(_SECRET),
        )
    )

    safe = delegate.ended[0]
    assert safe.name == "external.operation"
    assert dict(safe.attributes) == {"error.type": ErrorCode.DEPENDENCY_FAILURE.value}
    assert dict(safe.resource.attributes) == {}
    assert safe.context is not None
    assert safe.context.trace_id == context.trace_id
    assert safe.context.span_id == context.span_id
    assert _SECRET not in safe.to_json()


class BrokenReadableSpan(ReadableSpan):
    @property
    def attributes(self) -> Sequence[object]:  # type: ignore[override]
        raise RuntimeError(_SECRET)


class FailingProcessor(SpanProcessor):
    def on_end(self, span: ReadableSpan) -> None:
        raise RuntimeError(_SECRET)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        raise RuntimeError(_SECRET)

    def shutdown(self) -> None:
        raise RuntimeError(_SECRET)


def test_sanitizer_and_delegate_failures_are_nonfatal() -> None:
    recording = RecordingProcessor()
    SafeSpanProcessor(recording).on_end(BrokenReadableSpan(name=_SECRET))
    assert recording.ended == []

    processor = SafeSpanProcessor(FailingProcessor())
    processor.on_end(ReadableSpan(name=_SECRET))
    assert processor.force_flush(timeout_millis=1) is False
    with pytest.raises(RuntimeError, match="^telemetry shutdown failed$"):
        processor.shutdown()


def test_on_start_never_exposes_mutable_raw_span() -> None:
    class StartFailingProcessor(RecordingProcessor):
        def on_start(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("raw span reached delegate")

    SafeSpanProcessor(StartFailingProcessor()).on_start(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("label", ["", "value\nsecret", "value with spaces", "x" * 129])
def test_invalid_startup_labels_are_rejected(label: str) -> None:
    with pytest.raises(ValueError, match="invalid telemetry label"):
        TelemetryLabels(models=frozenset({label}))
