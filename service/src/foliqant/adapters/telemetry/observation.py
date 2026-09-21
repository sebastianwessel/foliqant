"""W3C workflow/step tracing without inspecting payloads or caller identity."""

from contextvars import Token
from time import perf_counter

from opentelemetry import baggage
from opentelemetry import context as context_api
from opentelemetry import trace as trace_api
from opentelemetry.metrics import Histogram, MeterProvider, NoOpMeterProvider
from opentelemetry.trace import Span, Status, StatusCode, TracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import AttributeValue

from foliqant.core.errors import ErrorCode
from foliqant.core.execution import RunStatus
from foliqant.ports.observation import Observation, TraceContext

from .privacy import TelemetryLabels

_PROPAGATOR = TraceContextTextMapPropagator()
_MAX_SECONDS = 365 * 24 * 60 * 60
_DEFAULT_LABELS = TelemetryLabels()


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
    ) -> None:
        self._span = span
        self._token = token
        self._histogram = histogram
        self._attributes = attributes
        self._started = perf_counter()
        self._finished = False
        self._closed = False

    def finish(self, outcome: RunStatus, error: ErrorCode | None) -> None:
        if self._finished or self._closed:
            return
        self._finished = True
        attributes = dict(self._attributes)
        attributes["foliqant.outcome"] = outcome
        if error is not None:
            attributes["error.type"] = error.value
        self._span.set_attributes(attributes)
        if outcome in {"failed", "cancelled"}:
            self._span.set_status(Status(StatusCode.ERROR))
        self._histogram.record(
            min(_MAX_SECONDS, max(0.0, perf_counter() - self._started)), attributes
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            context_api.detach(self._token)
        finally:
            self._span.end()


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
    ) -> None:
        self._labels = labels
        self._workflow_tracer = tracer_provider.get_tracer("foliqant.workflow")
        self._step_tracer = tracer_provider.get_tracer("foliqant.step")
        meter = (meter_provider or NoOpMeterProvider()).get_meter("foliqant.metrics")
        self._workflow_duration = meter.create_histogram("foliqant.workflow.duration", unit="s")
        self._step_duration = meter.create_histogram("foliqant.step.duration", unit="s")

    def start(
        self,
        workflow: str,
        *,
        step: str | None = None,
        trace: TraceContext | None = None,
        transport_trace: TraceContext | None = None,
    ) -> Observation:
        """Attach a content-free scope; close it in the same async task."""
        attributes: dict[str, AttributeValue] = {}
        if workflow in self._labels.workflows:
            attributes["foliqant.workflow.name"] = workflow
        if step in self._labels.steps:
            attributes["foliqant.step.name"] = step
        tracer = self._workflow_tracer if step is None else self._step_tracer
        parent = _parent(trace, transport_trace)
        span = tracer.start_span(
            "workflow" if step is None else "step", context=parent, attributes=attributes
        )
        try:
            token = context_api.attach(trace_api.set_span_in_context(span, parent))
        except BaseException:
            span.end()
            raise
        return _Observation(
            span,
            token,
            self._workflow_duration if step is None else self._step_duration,
            attributes,
        )
