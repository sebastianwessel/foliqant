"""Synchronous privacy filtering for OpenTelemetry spans before export queues."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from opentelemetry import context as context_api
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceState
from opentelemetry.util.types import AttributeValue

from foliqant.core.errors import ErrorCode

_MAX_COUNT = 2**53 - 1
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")

_GEN_AI_OPERATIONS = frozenset({"chat", "execute_tool", "inference", "invoke_agent"})
_MCP_METHODS = frozenset({"initialize", "server/discover", "ping", "tools/call", "tools/list"})
_OUTCOMES = frozenset({"cancelled", "completed", "failed", "needs_review", "skipped"})
_ERROR_TYPES = frozenset(item.value for item in ErrorCode)

_STRING_ATTRIBUTES = {
    "service.name": "services",
    "foliqant.model.id": "models",
    "foliqant.provider.name": "providers",
    "foliqant.step.name": "steps",
    "foliqant.flow.name": "flows",
    "foliqant.tool.name": "tools",
    "foliqant.workflow.name": "workflows",
    "gen_ai.provider.name": "providers",
    "gen_ai.request.model": "models",
    "gen_ai.response.model": "models",
    "gen_ai.tool.name": "tools",
}
_USAGE_ATTRIBUTES = frozenset(
    {
        "foliqant.usage.cache_read_input_tokens",
        "foliqant.usage.cache_write_input_tokens",
        "foliqant.usage.input_tokens",
        "foliqant.usage.model_requests",
        "foliqant.usage.output_tokens",
        "foliqant.usage.reasoning_output_tokens",
        "foliqant.usage.tool_calls",
        "gen_ai.aggregated_usage.cache_creation.input_tokens",
        "gen_ai.aggregated_usage.cache_read.input_tokens",
        "gen_ai.aggregated_usage.input_tokens",
        "gen_ai.aggregated_usage.output_tokens",
        "gen_ai.aggregated_usage.reasoning.output_tokens",
        "gen_ai.usage.cache_creation.input_tokens",
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.reasoning.output_tokens",
    }
)


@dataclass(frozen=True, slots=True)
class TelemetryLabels:
    """Reviewed nonsecret configuration values permitted in exported telemetry."""

    services: frozenset[str] = frozenset()
    models: frozenset[str] = frozenset()
    providers: frozenset[str] = frozenset()
    tools: frozenset[str] = frozenset()
    workflows: frozenset[str] = frozenset()
    steps: frozenset[str] = frozenset()
    flows: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for values in (
            self.services,
            self.models,
            self.providers,
            self.tools,
            self.workflows,
            self.steps,
            self.flows,
        ):
            if type(values) is not frozenset or len(values) > 1024:
                raise ValueError("invalid telemetry label allowlist")
            for value in values:
                if type(value) is not str or _LABEL.fullmatch(value) is None:
                    raise ValueError("invalid telemetry label")


_DEFAULT_LABELS = TelemetryLabels()


def _clean_context(context: SpanContext | None) -> SpanContext | None:
    if context is None:
        return None
    return SpanContext(
        trace_id=context.trace_id,
        span_id=context.span_id,
        is_remote=context.is_remote,
        trace_flags=context.trace_flags,
        trace_state=TraceState(),
    )


def _safe_scope_name(span: ReadableSpan) -> str:
    scope = span.instrumentation_scope
    if scope is None:
        return "external"
    if scope.name in {
        "foliqant.workflow",
        "foliqant.flow",
        "foliqant.step",
        "mcp-python-sdk",
        "pydantic-ai",
    }:
        return scope.name
    return "external"


def _safe_span_name(span: ReadableSpan, attributes: Mapping[str, AttributeValue]) -> str:
    scope = _safe_scope_name(span)
    if scope == "foliqant.workflow":
        return "foliqant.workflow"
    if scope == "foliqant.step":
        return "foliqant.step"
    if scope == "foliqant.flow":
        return "foliqant.flow"
    if scope == "pydantic-ai":
        operation = attributes.get("gen_ai.operation.name")
        if type(operation) is str and operation in _GEN_AI_OPERATIONS:
            target_key = (
                "gen_ai.tool.name"
                if operation == "execute_tool"
                else "gen_ai.request.model"
                if operation in {"chat", "inference"}
                else None
            )
            target = attributes.get(target_key) if target_key is not None else None
            return f"{operation} {target}" if type(target) is str else operation
        return "gen_ai.operation"
    if scope == "mcp-python-sdk":
        method = attributes.get("mcp.method.name")
        if type(method) is str and method in _MCP_METHODS:
            target = attributes.get("gen_ai.tool.name") if method == "tools/call" else None
            return f"{method} {target}" if type(target) is str else method
        return "mcp.operation"
    return "external.operation"


def _safe_attributes(
    attributes: Mapping[str, object], labels: TelemetryLabels
) -> dict[str, AttributeValue]:
    output: dict[str, AttributeValue] = {}
    for key, label_group in _STRING_ATTRIBUTES.items():
        value = attributes.get(key)
        allowed = getattr(labels, label_group)
        if type(value) is str and value in allowed:
            output[key] = value

    operation = attributes.get("gen_ai.operation.name")
    if type(operation) is str and operation in _GEN_AI_OPERATIONS:
        output["gen_ai.operation.name"] = operation
    method = attributes.get("mcp.method.name")
    if type(method) is str and method in _MCP_METHODS:
        output["mcp.method.name"] = method
    outcome = attributes.get("foliqant.outcome")
    if type(outcome) is str and outcome in _OUTCOMES:
        output["foliqant.outcome"] = outcome
    error_type = attributes.get("error.type")
    if type(error_type) is str and error_type in _ERROR_TYPES:
        output["error.type"] = error_type

    for key in _USAGE_ATTRIBUTES:
        value = attributes.get(key)
        if type(value) is int and 0 <= value <= _MAX_COUNT:
            output[key] = value
    return output


def _safe_resource(resource: Resource, labels: TelemetryLabels) -> Resource:
    service_name = resource.attributes.get("service.name")
    if type(service_name) is str and service_name in labels.services:
        return Resource({"service.name": service_name})
    return Resource.get_empty()


def _safe_span(span: ReadableSpan, labels: TelemetryLabels) -> ReadableSpan:
    raw_attributes = cast(Mapping[str, object], span.attributes or {})
    safe_attributes = _safe_attributes(raw_attributes, labels)
    if span.status.status_code is StatusCode.ERROR and "error.type" not in safe_attributes:
        safe_attributes["error.type"] = ErrorCode.DEPENDENCY_FAILURE.value
    links = tuple(
        Link(context, attributes=None)
        for link in span.links
        if (context := _clean_context(link.context)) is not None
    )
    return ReadableSpan(
        name=_safe_span_name(span, safe_attributes),
        context=_clean_context(span.context),
        parent=_clean_context(span.parent),
        resource=_safe_resource(span.resource, labels),
        attributes=safe_attributes,
        events=(),
        links=links,
        kind=span.kind,
        instrumentation_info=None,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=InstrumentationScope(_safe_scope_name(span)),
    )


class SafeSpanProcessor(SpanProcessor):
    """Clone and sanitize ended spans before delegating to an exporter processor.

    Wrap a batch processor with this class, rather than wrapping only its exporter,
    so the batch queue never retains raw span content.
    """

    def __init__(
        self,
        delegate: SpanProcessor,
        labels: TelemetryLabels = _DEFAULT_LABELS,
    ) -> None:
        if not isinstance(delegate, SpanProcessor) or not isinstance(labels, TelemetryLabels):
            raise ValueError("invalid safe span processor configuration")
        self._delegate = delegate
        self._labels = labels

    def on_start(
        self,
        span: Span,
        parent_context: context_api.Context | None = None,
    ) -> None:
        """Do not expose a mutable raw span to the delegate."""

    def on_end(self, span: ReadableSpan) -> None:
        """Synchronously sanitize before the delegate can queue the span."""
        try:
            safe_span = _safe_span(span, self._labels)
            self._delegate.on_end(safe_span)
        except Exception:
            # Telemetry must not change a business operation's outcome.
            return

    def shutdown(self) -> None:
        """Shut down the delegate without surfacing telemetry failures."""
        try:
            self._delegate.shutdown()
        except Exception:
            return

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush the delegate; failures are reported as False, never raised."""
        try:
            return self._delegate.force_flush(timeout_millis)
        except Exception:
            return False
