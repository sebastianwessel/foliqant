"""Synchronous privacy filtering for OpenTelemetry spans before export queues."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast

from opentelemetry import context as context_api
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, Span, SpanProcessor
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
_FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_call", "error"})
_CONDITION = re.compile(r"[^\x00-\x1f\x7f]{1,512}\Z")
_EXECUTION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_LOCATION = re.compile(r"[a-z0-9_.]{1,200}\Z")
_ROLES = frozenset({"routed", "callable", "retry"})
_ROUTE_KINDS = frozenset({"direct", "cases", "route", "review"})
_OUTCOME_TARGETS = frozenset({"completed", "needs_review"})
_STOPS = frozenset({"until", "exhausted", "continue_when", "review", "failure"})
_ISSUES = frozenset({"no_supported_answer", "conflicting_information", "multiple_valid_options"})
_OPERATORS = frozenset(
    {
        "present",
        "empty",
        "equals",
        "not_equals",
        "in",
        "not_in",
        "gt",
        "gte",
        "lt",
        "lte",
        "matches",
        "length",
    }
)
_MISMATCHES = frozenset({"incompatible_type", "value_too_long"})
_STEP_KINDS = frozenset({"decision", "llm", "mcp", "handler", "flow_collection"})
_MAX_EVENTS = 128
_MAX_COST = 1_000_000_000.0

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
        "foliqant.usage.output_retries",
        "foliqant.request.attempt",
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
    cases: frozenset[str] = frozenset()
    """Configured `cases` keys, reported by `route.selected` events."""
    conditions: frozenset[str] = frozenset()
    """Condensed step conditions (pointers and operators only) for `step.skipped`."""

    def __post_init__(self) -> None:
        for values in (
            self.services,
            self.models,
            self.providers,
            self.tools,
            self.workflows,
            self.steps,
            self.flows,
            self.cases,
        ):
            if type(values) is not frozenset or len(values) > 1024:
                raise ValueError("invalid telemetry label allowlist")
            for value in values:
                if type(value) is not str or _LABEL.fullmatch(value) is None:
                    raise ValueError("invalid telemetry label")
        if type(self.conditions) is not frozenset or len(self.conditions) > 4096:
            raise ValueError("invalid telemetry condition allowlist")
        for value in self.conditions:
            if type(value) is not str or _CONDITION.fullmatch(value) is None:
                raise ValueError("invalid telemetry condition label")

    @classmethod
    def accepted(cls, **groups: Iterable[str]) -> tuple["TelemetryLabels", int]:
        """Keep representable labels only and return how many values were dropped.

        Configuration values that cannot be exported safely (for example a model
        ID with characters outside the label alphabet) are omitted from telemetry;
        they never fail activation or disable the remaining observations.
        """
        kept: dict[str, frozenset[str]] = {}
        dropped = 0
        for name, values in groups.items():
            pattern, limit = (_CONDITION, 4096) if name == "conditions" else (_LABEL, 1024)
            unique = sorted(set(values))
            valid = [value for value in unique if type(value) is str and pattern.fullmatch(value)]
            dropped += len(unique) - min(len(valid), limit)
            kept[name] = frozenset(valid[:limit])
        return cls(**kept), dropped


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
        "foliqant.tool",
        "mcp-python-sdk",
        "pydantic-ai",
    }:
        return scope.name
    return "external"


def scope_span_name(scope: str, attributes: Mapping[str, AttributeValue]) -> str:
    """Name a workflow, flow or step span from its sanitized configuration attributes.

    Names use configuration IDs and fixed words only: ``workflow <id>``,
    ``flow <id>`` with `` [item <index>]`` inside a collection and `` #<n>`` for
    attempt 2 onwards, and ``step <id> (<kind>)``. A name that is not an
    exportable label is left out rather than replaced by a runtime value.
    """
    kind = scope.removeprefix("foliqant.")
    label = attributes.get(f"foliqant.{kind}.name")
    name = f"{kind} {label}" if type(label) is str and _LABEL.fullmatch(label) else kind
    if kind == "flow":
        index = attributes.get("foliqant.collection.index")
        if type(index) is int and 0 <= index <= 1023:
            name = f"{name} [item {index}]"
        attempt = attributes.get("foliqant.flow.attempt")
        if type(attempt) is int and 2 <= attempt <= 64:
            name = f"{name} #{attempt}"
    elif kind == "step":
        step_kind = attributes.get("foliqant.step.kind")
        if type(step_kind) is str and step_kind in _STEP_KINDS:
            name = f"{name} ({step_kind})"
    return name


def _safe_span_name(span: ReadableSpan, attributes: Mapping[str, AttributeValue]) -> str:
    scope = _safe_scope_name(span)
    if scope in {"foliqant.workflow", "foliqant.flow", "foliqant.step"}:
        return scope_span_name(scope, attributes)
    if scope == "foliqant.tool":
        tool = attributes.get("gen_ai.tool.name")
        return f"execute_tool {tool}" if type(tool) is str else "execute_tool"
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

    response_model = attributes.get("gen_ai.response.model")
    if type(response_model) is str and _LABEL.fullmatch(response_model):
        # The configured model or a provider snapshot of it, such as `<model>-2026-05-01`.
        if response_model in labels.models or any(
            response_model.startswith(f"{model}-") for model in labels.models
        ):
            output["gen_ai.response.model"] = response_model
    step_kind = attributes.get("foliqant.step.kind")
    if type(step_kind) is str and step_kind in _STEP_KINDS:
        output["foliqant.step.kind"] = step_kind
    cost = attributes.get("foliqant.usage.cost")
    if type(cost) is float and 0 <= cost <= _MAX_COST:
        output["foliqant.usage.cost"] = cost
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
    finish_reasons = attributes.get("gen_ai.response.finish_reasons")
    if (
        isinstance(finish_reasons, tuple)
        and 0 < len(finish_reasons) <= 8
        and all(type(reason) is str and reason in _FINISH_REASONS for reason in finish_reasons)
    ):
        output["gen_ai.response.finish_reasons"] = tuple(cast(tuple[str, ...], finish_reasons))
    consumed = attributes.get("foliqant.response.reasoning_consumed_budget")
    if type(consumed) is bool:
        output["foliqant.response.reasoning_consumed_budget"] = consumed
    if attributes.get("foliqant.request.output_retry") is True:
        output["foliqant.request.output_retry"] = True

    for key in _USAGE_ATTRIBUTES:
        value = attributes.get(key)
        if type(value) is int and 0 <= value <= _MAX_COUNT:
            output[key] = value
    role = attributes.get("foliqant.flow.role")
    if type(role) is str and role in _ROLES:
        output["foliqant.flow.role"] = role
    for key, low, high in (
        ("foliqant.flow.attempt", 1, 64),
        ("foliqant.flow.max_attempts", 2, 64),
        ("foliqant.collection.index", 0, 1023),
    ):
        value = attributes.get(key)
        if type(value) is int and low <= value <= high:
            output[key] = value
    if attributes.get("foliqant.step.skipped") is True:
        output["foliqant.step.skipped"] = True
    execution = attributes.get("foliqant.execution.id")
    if type(execution) is str and _EXECUTION_ID.fullmatch(execution):
        output["foliqant.execution.id"] = execution
    return output


def safe_event_attributes(
    name: str, attributes: Mapping[str, object], labels: TelemetryLabels
) -> dict[str, AttributeValue] | None:
    """Keep only fixed event names and their allowlisted, content-free attributes.

    Applied when an event is recorded and again on export, so spans on a
    host-owned provider carry the same event content as exported runtime spans.
    """
    output: dict[str, AttributeValue] = {}
    if name == "route.selected":
        kind, target = attributes.get("kind"), attributes.get("target")
        if type(kind) is not str or kind not in _ROUTE_KINDS:
            return None
        output["kind"] = kind
        if type(target) is str and (target in labels.flows or target in _OUTCOME_TARGETS):
            output["target"] = target
        index = attributes.get("index")
        if type(index) is int and 0 <= index <= 31:
            output["index"] = index
        case = attributes.get("case")
        if type(case) is str and (case in labels.cases or case in _ISSUES):
            output["case"] = case
        return output
    if name == "repeat.stopped":
        stopped = attributes.get("stopped_by")
        return {"stopped_by": stopped} if type(stopped) is str and stopped in _STOPS else None
    if name == "step.skipped":
        condition = attributes.get("condition")
        if type(condition) is str and condition in labels.conditions:
            output["condition"] = condition
        return output
    if name in {"condition.evaluated", "condition.type_mismatch"}:
        location = attributes.get("location")
        if type(location) is not str or _LOCATION.fullmatch(location) is None:
            return None
        output["location"] = location
        result = attributes.get("result")
        if name == "condition.evaluated" and type(result) is bool:
            output["result"] = result
        operator = attributes.get("operator")
        if name == "condition.type_mismatch" and type(operator) is str and operator in _OPERATORS:
            output["operator"] = operator
        reason = attributes.get("reason")
        if name == "condition.type_mismatch" and type(reason) is str and reason in _MISMATCHES:
            output["reason"] = reason
        return output
    if name == "handler.review":
        issues = attributes.get("issues")
        if isinstance(issues, tuple) and all(
            type(issue) is str and issue in _ISSUES for issue in issues
        ):
            output["issues"] = tuple(cast(tuple[str, ...], issues))
        return output
    return None


def _safe_events(span: ReadableSpan, labels: TelemetryLabels) -> tuple[Event, ...]:
    events: list[Event] = []
    for event in span.events[:_MAX_EVENTS]:
        safe = safe_event_attributes(
            event.name, cast(Mapping[str, object], event.attributes or {}), labels
        )
        if safe is not None:
            events.append(Event(event.name, safe, event.timestamp))
    return tuple(events)


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
        events=_safe_events(span, labels),
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
        """Let the runtime report incomplete cleanup without exposing raw errors."""
        try:
            self._delegate.shutdown()
        except Exception:
            raise RuntimeError("telemetry shutdown failed") from None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush the delegate; failures are reported as False, never raised."""
        try:
            return self._delegate.force_flush(timeout_millis)
        except Exception:
            return False
