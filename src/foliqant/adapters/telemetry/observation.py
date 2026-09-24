"""W3C workflow/step tracing without inspecting payloads or caller identity."""

import logging
from collections.abc import Mapping
from contextvars import Token
from time import perf_counter
from types import MappingProxyType

from opentelemetry import baggage
from opentelemetry import context as context_api
from opentelemetry import trace as trace_api
from opentelemetry.metrics import Histogram, MeterProvider, NoOpMeterProvider
from opentelemetry.trace import Span, Status, StatusCode, TracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import AttributeValue

from foliqant.core.errors import ErrorCode
from foliqant.core.execution import StepStatus
from foliqant.ports.observation import Observation, ObservationValue, TraceContext

from .logging import LogEvent, emit_event
from .privacy import TelemetryLabels, safe_event_attributes

_PROPAGATOR = TraceContextTextMapPropagator()
_MAX_SECONDS = 365 * 24 * 60 * 60
_DEFAULT_LABELS = TelemetryLabels()
_LOGGER = logging.getLogger(__name__)
_METRIC_KEYS = frozenset(
    {
        "foliqant.workflow.name",
        "foliqant.flow.name",
        "foliqant.step.name",
        "foliqant.outcome",
        "error.type",
    }
)


def _label(attributes: Mapping[str, AttributeValue], key: str) -> str | None:
    value = attributes.get(key)
    return value if type(value) is str else None


def _count(attributes: Mapping[str, AttributeValue], key: str) -> int | None:
    value = attributes.get(key)
    return value if type(value) is int else None


def _log_scope(
    attributes: dict[str, AttributeValue],
    *,
    kind: str,
    outcome: StepStatus | None = None,
    error: ErrorCode | None = None,
    duration: float | None = None,
) -> None:
    events = {
        "workflow": (LogEvent.RUN_STARTED, LogEvent.RUN_COMPLETED, LogEvent.RUN_FAILED),
        "flow": (LogEvent.FLOW_STARTED, LogEvent.FLOW_COMPLETED, LogEvent.FLOW_FAILED),
        "step": (LogEvent.STEP_STARTED, LogEvent.STEP_COMPLETED, LogEvent.STEP_FAILED),
    }
    event = events[kind][0 if outcome is None else 2 if outcome in {"failed", "cancelled"} else 1]
    try:
        emit_event(
            _LOGGER,
            event,
            level=logging.WARNING if error is not None else logging.INFO,
            workflow=_label(attributes, "foliqant.workflow.name"),
            flow=_label(attributes, "foliqant.flow.name"),
            step=_label(attributes, "foliqant.step.name"),
            outcome=outcome,
            error_code=error,
            duration_seconds=duration,
            attempt=_count(attributes, "foliqant.flow.attempt"),
            execution_id=_label(attributes, "foliqant.execution.id"),
        )
    except Exception:
        pass


_EVENT_LOGS = {
    "route.selected": (LogEvent.ROUTE_SELECTED, logging.INFO),
    "step.skipped": (LogEvent.STEP_SKIPPED, logging.INFO),
    "repeat.stopped": (LogEvent.REPEAT_STOPPED, logging.INFO),
    "handler.review": (LogEvent.HANDLER_REVIEW, logging.INFO),
    "condition.type_mismatch": (LogEvent.CONDITION_TYPE_MISMATCH, logging.WARNING),
    "condition.evaluated": (LogEvent.CONDITION_EVALUATED, logging.DEBUG),
}


def _log_event(
    scope: Mapping[str, AttributeValue], name: str, attributes: Mapping[str, ObservationValue]
) -> None:
    selected = _EVENT_LOGS.get(name)
    if selected is None:
        return
    event, level = selected

    def text(key: str) -> str | None:
        value = attributes.get(key)
        return value if type(value) is str else None

    issues = attributes.get("issues")
    try:
        emit_event(
            _LOGGER,
            event,
            level=level,
            workflow=_label(scope, "foliqant.workflow.name"),
            flow=_label(scope, "foliqant.flow.name"),
            step=_label(scope, "foliqant.step.name"),
            attempt=_count(scope, "foliqant.flow.attempt"),
            execution_id=_label(scope, "foliqant.execution.id"),
            route_kind=text("kind"),
            target=text("target"),
            stopped_by=text("stopped_by"),
            operator=text("operator"),
            reason=text("reason"),
            location=text("location"),
            issues=issues if isinstance(issues, tuple) else None,
        )
    except Exception:
        pass


def trace_carrier() -> dict[str, str]:
    """Inject only current W3C trace fields, never ambient baggage or business data."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return {key: value for key, value in carrier.items() if len(value) <= 512}


def _extract(trace: TraceContext) -> context_api.Context:
    carrier = {
        key: value
        for key, value in (
            ("traceparent", trace.traceparent),
            ("tracestate", trace.tracestate),
        )
        if isinstance(value, str) and len(value) <= 512
    }
    return _PROPAGATOR.extract(carrier, context=context_api.Context())


def _parent(
    trace: TraceContext | None, transport_trace: TraceContext | None
) -> context_api.Context:
    # Select one complete valid carrier; never merge transport and body fields.
    for candidate in (transport_trace, trace):
        if candidate is not None:
            parent = _extract(candidate)
            if trace_api.get_current_span(parent).get_span_context().is_valid:
                return parent
    if trace is not None or transport_trace is not None:
        return context_api.Context()
    # Nested embedded work can inherit the current scope without forwarding baggage.
    return baggage.clear(context=context_api.get_current())


class _Observation:
    def __init__(
        self,
        span: Span,
        token: Token[context_api.Context],
        histogram: Histogram,
        attributes: dict[str, AttributeValue],
        kind: str,
        *,
        labels: TelemetryLabels = _DEFAULT_LABELS,
        conditions: bool = False,
    ) -> None:
        self._labels = labels
        self._span = span
        self._token = token
        self._histogram = histogram
        self._attributes = attributes
        self._started = perf_counter()
        self._finished = False
        self._closed = False
        self._kind = kind
        self._conditions = conditions
        _log_scope(attributes, kind=kind)

    def event(self, name: str, attributes: Mapping[str, ObservationValue]) -> None:
        """Add a fixed-name span event with allowlisted attributes only."""
        if self._closed or (name == "condition.evaluated" and not self._conditions):
            return
        try:
            safe = safe_event_attributes(name, attributes, self._labels)
            if safe is not None:
                self._span.add_event(name, safe)
        finally:
            _log_event(self._attributes, name, attributes)

    def carrier(self) -> Mapping[str, str]:
        carrier: dict[str, str] = {}
        _PROPAGATOR.inject(carrier, context=trace_api.set_span_in_context(self._span))
        return MappingProxyType({key: value for key, value in carrier.items() if len(value) <= 512})

    def finish(self, outcome: StepStatus, error: ErrorCode | None) -> None:
        if self._finished or self._closed:
            return
        self._finished = True
        attributes = dict(self._attributes)
        attributes["foliqant.outcome"] = outcome
        if error is not None:
            attributes["error.type"] = error.value
        duration = min(_MAX_SECONDS, max(0.0, perf_counter() - self._started))
        try:
            self._span.set_attributes(attributes)
            if outcome in {"failed", "cancelled"}:
                self._span.set_status(Status(StatusCode.ERROR))
            # Metrics keep low-cardinality labels only, never execution IDs.
            self._histogram.record(
                duration, {key: value for key, value in attributes.items() if key in _METRIC_KEYS}
            )
        finally:
            _log_scope(attributes, kind=self._kind, outcome=outcome, error=error, duration=duration)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            context_api.detach(self._token)
        except Exception:
            pass
        finally:
            try:
                self._span.end()
            except Exception:
                pass


class WorkflowTelemetry:
    """An optional ExecutionObserver backed by explicitly owned OTel providers.

    Pass a safe provider configured by TelemetryRuntime. SDK-dependent code stays
    outside the engine; the runner isolates observation failures from execution.
    Labels must come from reviewed nonsecret startup configuration.
    """

    def __init__(
        self,
        tracer_provider: TracerProvider,
        *,
        labels: TelemetryLabels = _DEFAULT_LABELS,
        meter_provider: MeterProvider | None = None,
        conditions: bool = False,
    ) -> None:
        self._labels = labels
        self._conditions = conditions
        self._workflow_tracer = tracer_provider.get_tracer("foliqant.workflow")
        self._flow_tracer = tracer_provider.get_tracer("foliqant.flow")
        self._step_tracer = tracer_provider.get_tracer("foliqant.step")
        meter = (meter_provider or NoOpMeterProvider()).get_meter("foliqant.metrics")
        self._workflow_duration = meter.create_histogram("foliqant.workflow.duration", unit="s")
        self._flow_duration = meter.create_histogram("foliqant.flow.duration", unit="s")
        self._step_duration = meter.create_histogram("foliqant.step.duration", unit="s")

    def start(
        self,
        workflow: str,
        *,
        flow: str | None = None,
        step: str | None = None,
        trace: TraceContext | None = None,
        transport_trace: TraceContext | None = None,
        attributes: Mapping[str, ObservationValue] | None = None,
    ) -> Observation:
        """Attach a content-free scope; close it in the same async task.

        Scope ``attributes`` (attempt, role, execution ID) are sanitized on export.
        """
        extra = dict(attributes or {})
        scope: dict[str, AttributeValue] = {}
        if workflow in self._labels.workflows:
            scope["foliqant.workflow.name"] = workflow
        if step in self._labels.steps:
            scope["foliqant.step.name"] = step
        if flow in self._labels.flows:
            scope["foliqant.flow.name"] = flow
        for key, value in extra.items():
            if key.startswith("foliqant.") and type(value) in (str, int, bool):
                scope[key] = value
        if step is not None:
            tracer, duration, name = self._step_tracer, self._step_duration, "step"
        elif flow is not None:
            tracer, duration, name = self._flow_tracer, self._flow_duration, "flow"
        else:
            tracer, duration, name = self._workflow_tracer, self._workflow_duration, "workflow"
        parent = _parent(trace, transport_trace)
        # The exported name already; spans on a host provider look the same.
        span = tracer.start_span(f"foliqant.{name}", context=parent, attributes=scope)
        try:
            token = context_api.attach(trace_api.set_span_in_context(span, parent))
        except BaseException:
            span.end()
            raise
        return _Observation(
            span,
            token,
            duration,
            scope,
            name,
            labels=self._labels,
            conditions=self._conditions,
        )
