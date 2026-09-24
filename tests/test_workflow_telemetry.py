"""Real OTel scope/propagation checks with deterministic engine operations."""

import asyncio
from pathlib import Path

import pytest
from opentelemetry import baggage
from opentelemetry import context as context_api
from opentelemetry import trace as trace_api
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from test_runner import NoSchema, Scripted, make_plan

from foliqant.adapters.telemetry.observation import WorkflowTelemetry, trace_carrier
from foliqant.adapters.telemetry.privacy import SafeSpanProcessor, TelemetryLabels
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import RunStatus, StepOutcome
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject
from foliqant.core.observation import observe
from foliqant.core.runner import WorkflowRunner
from foliqant.ports.execution import OperationStep, StepContext
from foliqant.ports.observation import Observation, TraceContext

_LABELS = TelemetryLabels(
    workflows=frozenset({"inbox"}), steps=frozenset({"first"}), flows=frozenset({"main"})
)
_PARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"


@pytest.fixture
def telemetry():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SafeSpanProcessor(SimpleSpanProcessor(exporter), _LABELS))
    reader = InMemoryMetricReader()
    meters = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    observer = WorkflowTelemetry(provider, labels=_LABELS, meter_provider=meters)
    yield observer, exporter, reader, provider
    provider.shutdown()
    meters.shutdown()


async def test_real_runner_spans_and_metrics_do_not_capture_business_values(tmp_path, telemetry):
    observer, exporter, reader, _ = telemetry
    plan = make_plan(
        tmp_path,
        {
            "first": "type: handler\nhandler: echo\ninput: {}\n",
        },
    )
    seen = []

    async def execute(step, inputs, context):
        seen.append(trace_carrier())
        assert baggage.get_all() == {}
        return StepOutcome("PRIVATE RESPONSE")

    runner = WorkflowRunner(
        plan,
        executor=Scripted(execute),
        validator=NoSchema(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        observer=observer,
    )
    identity = Identity(tenant_id="PRIVATE-TENANT", principal_id="PRIVATE-USER")
    envelope = accept_envelope(
        Envelope.model_validate(
            {
                "payload": "PRIVATE INPUT",
                "metadata": {
                    "telemetry": {"traceparent": _PARENT, "tracestate": "vendor=PRIVATE-STATE"}
                },
            }
        ),
        identity,
    )
    token = context_api.attach(baggage.set_baggage("secret", "PRIVATE BAGGAGE"))
    try:
        result = await runner.run(envelope, identity=identity)
        assert baggage.get_baggage("secret") == "PRIVATE BAGGAGE"
    finally:
        context_api.detach(token)
    assert result.status == "completed"
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["foliqant.step", "foliqant.flow", "foliqant.workflow"]
    root = spans[-1]
    assert root.parent.span_id == int("0123456789abcdef", 16)
    assert all(s.context.trace_id == int(_PARENT.split("-")[1], 16) for s in spans)
    assert spans[0].parent.span_id == spans[1].context.span_id
    assert spans[1].parent.span_id == root.context.span_id
    assert seen[0]["traceparent"].split("-")[2] == f"{spans[0].context.span_id:016x}"
    assert set(seen[0]) == {"traceparent", "tracestate"}
    assert "PRIVATE" not in " ".join(s.to_json() for s in spans)
    metrics = reader.get_metrics_data()
    assert metrics is not None
    assert "PRIVATE" not in metrics.to_json()
    names = {m.name for r in metrics.resource_metrics for s in r.scope_metrics for m in s.metrics}
    assert names == {
        "foliqant.workflow.duration",
        "foliqant.flow.duration",
        "foliqant.step.duration",
    }
    assert not trace_api.get_current_span().get_span_context().is_valid


@pytest.mark.parametrize("mode", ["failure", "review", "cancel"])
async def test_outcome_status_and_context_restored(tmp_path: Path, telemetry, mode: str):
    observer, exporter, _, _ = telemetry
    plan = make_plan(
        tmp_path,
        {
            "first": "type: handler\nhandler: echo\ninput: {}\n",
        },
    )

    async def execute(
        step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        if mode == "cancel":
            raise asyncio.CancelledError()
        if mode == "failure":
            raise RuntimeError("PRIVATE ERROR")
        return StepOutcome(None, needs_review=True)

    runner = WorkflowRunner(
        plan,
        executor=Scripted(execute),
        validator=NoSchema(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        observer=observer,
    )
    envelope = accept_envelope(Envelope(payload={}), Identity())
    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await runner.run(envelope, identity=Identity())
    else:
        result = await runner.run(envelope, identity=Identity())
        assert result.status == ("failed" if mode == "failure" else "needs_review")
    for span in exporter.get_finished_spans():
        assert span.status.status_code == (
            StatusCode.UNSET if mode == "review" else StatusCode.ERROR
        )
        assert "PRIVATE" not in span.to_json()
        # Exception events are dropped; only fixed runtime events remain.
        assert {event.name for event in span.events} <= {"route.selected", "handler.review"}
    assert not trace_api.get_current_span().get_span_context().is_valid


async def test_concurrent_callers_do_not_share_trace_context(telemetry):
    observer, exporter, _, _ = telemetry
    ready = asyncio.Event()

    async def one(parent: str):
        with observe(observer, "inbox", trace=TraceContext(parent)):
            ready.set()
            await asyncio.sleep(0)
            with observe(observer, "inbox", step="first"):
                await ready.wait()
                return trace_carrier()["traceparent"].split("-")[1]

    parents = [_PARENT, _PARENT.replace("0123456789abcdef0123456789abcdef", "1" * 32)]
    result = await asyncio.gather(*(one(p) for p in parents))
    assert result == [p.split("-")[1] for p in parents]
    assert len(exporter.get_finished_spans()) == 4


@pytest.mark.parametrize("stage", ["start", "finish", "close"])
async def test_broken_observer_does_not_change_business_result_or_error(stage):
    class Broken:
        def start(
            self,
            workflow: str,
            *,
            flow: str | None = None,
            step: str | None = None,
            trace: TraceContext | None = None,
            transport_trace: TraceContext | None = None,
        ) -> Observation:
            if stage == "start":
                raise RuntimeError("PRIVATE")
            return self

        def finish(self, outcome: RunStatus, error: ErrorCode | None) -> None:
            if stage == "finish":
                raise RuntimeError("PRIVATE")

        def close(self) -> None:
            if stage == "close":
                raise RuntimeError("PRIVATE")

    with observe(Broken(), "inbox") as outcome:
        outcome.status = "needs_review"
    with pytest.raises(ServiceError) as caught, observe(Broken(), "inbox"):
        raise ServiceError(ErrorCode.CONFLICT)
    assert caught.value.code == ErrorCode.CONFLICT


def test_invalid_explicit_carrier_does_not_inherit_unrelated_parent(telemetry):
    observer, exporter, _, provider = telemetry
    with provider.get_tracer("test").start_as_current_span("ambient") as ambient:
        with observe(observer, "inbox", trace=TraceContext("bad-carrier")):
            assert (
                trace_api.get_current_span().get_span_context().trace_id
                != ambient.get_span_context().trace_id
            )
    root = next(s for s in exporter.get_finished_spans() if s.name == "foliqant.workflow")
    assert root.parent is None


@pytest.mark.parametrize("metadata_parent", [_PARENT, "invalid"])
async def test_transport_carrier_precedes_metadata_as_complete_carrier(
    tmp_path, telemetry, metadata_parent
):
    observer, exporter, _, _ = telemetry
    plan = make_plan(tmp_path, {"first": "type: handler\nhandler: echo\ninput: {}\n"})

    async def execute(step, inputs, context):
        return StepOutcome(None)

    carrier = _PARENT.replace("0123456789abcdef0123456789abcdef", "3" * 32)
    runner = WorkflowRunner(
        plan,
        executor=Scripted(execute),
        validator=NoSchema(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        observer=observer,
    )
    envelope = accept_envelope(
        Envelope.model_validate(
            {
                "payload": {},
                "metadata": {
                    "telemetry": {
                        "traceparent": metadata_parent,
                        "tracestate": "vendor=untrusted",
                    }
                },
            }
        ),
        Identity(),
    )
    result = await runner.run(envelope, identity=Identity(), transport_trace=TraceContext(carrier))
    assert result.metadata == envelope.metadata
    assert all(s.context.trace_id == int("3" * 32, 16) for s in exporter.get_finished_spans())
    with observe(
        observer,
        "inbox",
        trace=TraceContext(_PARENT, "vendor=body"),
        transport_trace=TraceContext(carrier),
    ):
        assert "tracestate" not in trace_carrier()


def test_invalid_transport_falls_back_to_valid_metadata_without_merging(telemetry):
    observer, exporter, _, _ = telemetry
    with observe(
        observer,
        "inbox",
        trace=TraceContext(_PARENT, "vendor=body"),
        transport_trace=TraceContext("invalid", "vendor=transport"),
    ):
        assert trace_carrier()["tracestate"] == "vendor=body"
    assert exporter.get_finished_spans()[0].context.trace_id == int(_PARENT.split("-")[1], 16)
