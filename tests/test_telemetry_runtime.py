"""Explicit OTel setup must stay optional, bounded, and isolated from ambient config."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    MetricsData,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from pydantic import ValidationError

import foliqant.adapters.telemetry.runtime as runtime_module
from foliqant.adapters.telemetry.privacy import TelemetryLabels
from foliqant.adapters.telemetry.runtime import TelemetryRuntime
from foliqant.contracts.telemetry import TelemetryConfig


class CapturingSpanExporter(SpanExporter):
    def __init__(self) -> None:
        self.batches: list[Sequence[ReadableSpan]] = []
        self.shutdown_calls = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.batches.append(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class CapturingMetricExporter(MetricExporter):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.batches: list[MetricsData] = []
        self.fail = fail
        self.shutdown_calls = 0

    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10_000,
        **kwargs: object,
    ) -> MetricExportResult:
        del timeout_millis, kwargs
        if self.fail:
            raise RuntimeError("SECRET_EXPORT_FAILURE")
        self.batches.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        del timeout_millis
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: object) -> None:
        del timeout_millis, kwargs
        self.shutdown_calls += 1


def config(**overrides: object) -> TelemetryConfig:
    return TelemetryConfig.model_validate(
        {"service_name": "foliqant", **overrides},
        strict=True,
    )


def labels() -> TelemetryLabels:
    return TelemetryLabels(
        services=frozenset({"foliqant"}),
        models=frozenset({"reviewed-model"}),
        providers=frozenset({"reviewed-provider"}),
        workflows=frozenset({"reviewed_workflow"}),
        steps=frozenset({"reviewed_step"}),
    )


def test_empty_endpoints_create_no_exporter() -> None:
    calls: list[str] = []

    def unexpected_span(**kwargs: object) -> SpanExporter:
        calls.append(str(kwargs))
        raise AssertionError("span exporter constructed")

    def unexpected_metric(**kwargs: object) -> MetricExporter:
        calls.append(str(kwargs))
        raise AssertionError("metric exporter constructed")

    settings = config(
        traces_endpoint="",
        metrics_endpoint="",
    )
    runtime = TelemetryRuntime.build(
        settings,
        labels=labels(),
        environment={},
        _span_exporter_factory=unexpected_span,
        _metric_exporter_factory=unexpected_metric,
    )
    assert settings.traces_endpoint is None
    assert settings.metrics_endpoint is None
    assert calls == []
    assert not runtime.traces_exporting
    assert not runtime.metrics_exporting
    assert runtime.startup_failures == frozenset()


@pytest.mark.parametrize(
    "overrides",
    [
        {"traces_endpoint": "   "},
        {"metrics_endpoint": "http://collector.example/v1/metrics"},
        {"traces_endpoint": "https://user:secret@collector.example/v1/traces"},
        {"span_queue_capacity": 2, "span_batch_size": 3},
        {"traces_headers": {"bad header": "$TOKEN"}},
        {"export_timeout": float("inf")},
    ],
)
def test_invalid_configuration_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        config(**overrides)


async def test_explicit_signal_settings_ignore_common_ambient_exporter_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://ambient.invalid",
        "OTEL_EXPORTER_OTLP_HEADERS": "authorization=SECRET_AMBIENT",
        "OTEL_EXPORTER_OTLP_TIMEOUT": "999",
        "OTEL_EXPORTER_OTLP_COMPRESSION": "gzip",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=ambient-secret",
        "OTEL_METRICS_EXEMPLAR_FILTER": "always_on",
    }.items():
        monkeypatch.setenv(name, value)

    span_exporter = CapturingSpanExporter()
    metric_exporter = CapturingMetricExporter()
    span_arguments: dict[str, Any] = {}
    metric_arguments: dict[str, Any] = {}

    def make_span(**kwargs: object) -> SpanExporter:
        span_arguments.update(kwargs)
        return span_exporter

    def make_metric(**kwargs: object) -> MetricExporter:
        metric_arguments.update(kwargs)
        return metric_exporter

    monkeypatch.setattr(runtime_module, "OTLPSpanExporter", make_span)
    monkeypatch.setattr(runtime_module, "OTLPMetricExporter", make_metric)
    runtime = TelemetryRuntime.build(
        config(
            traces_endpoint="https://configured.example/v1/traces",
            metrics_endpoint="https://configured.example/v1/metrics",
            traces_headers={"authorization": "$TRACE_TOKEN"},
            metrics_headers={"x-api-key": "$METRIC_TOKEN"},
            export_timeout=2.5,
            metric_export_interval=3600.0,
        ),
        labels=labels(),
        environment={"TRACE_TOKEN": "trace-value", "METRIC_TOKEN": "metric-value"},
    )
    assert span_arguments["endpoint"] == "https://configured.example/v1/traces"
    assert metric_arguments["endpoint"] == "https://configured.example/v1/metrics"
    assert span_arguments["headers"] == {
        "User-Agent": "foliqant",
        "authorization": "trace-value",
    }
    assert metric_arguments["headers"] == {
        "User-Agent": "foliqant",
        "x-api-key": "metric-value",
    }
    assert span_arguments["timeout"] == metric_arguments["timeout"] == 2.5
    assert span_arguments["compression"] is runtime_module.Compression.NoCompression
    assert metric_arguments["compression"] is runtime_module.Compression.NoCompression
    assert span_arguments["session"].trust_env is False
    assert metric_arguments["session"].trust_env is False
    assert runtime.tracer_provider.resource.attributes == {"service.name": "foliqant"}
    assert await runtime.aclose(timeout=1)


def test_unoverridable_ambient_credentials_are_rejected_only_when_exporting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE", "/secret/client.pem")
    TelemetryRuntime.build(config(), labels=labels(), environment={})
    with pytest.raises(ValueError, match="conflicting ambient OpenTelemetry configuration"):
        TelemetryRuntime.build(
            config(traces_endpoint="https://collector.example/v1/traces"),
            labels=labels(),
            environment={},
        )


def test_missing_header_fails_before_exporter_construction_and_export_failure_is_nonfatal() -> None:
    from foliqant.core.errors import ServiceError

    calls = 0

    def broken_factory(**kwargs: object) -> SpanExporter:
        nonlocal calls
        del kwargs
        calls += 1
        raise RuntimeError("SECRET_CONSTRUCTOR_FAILURE")

    with pytest.raises(ServiceError):
        TelemetryRuntime.build(
            config(
                traces_endpoint="https://collector.example/v1/traces",
                traces_headers={"authorization": "$MISSING"},
            ),
            labels=labels(),
            environment={},
            _span_exporter_factory=broken_factory,
        )
    assert calls == 0

    failed = TelemetryRuntime.build(
        config(traces_endpoint="https://collector.example/v1/traces"),
        labels=labels(),
        environment={},
        _span_exporter_factory=broken_factory,
    )
    assert calls == 1
    assert failed.startup_failures == frozenset({"traces"})
    assert not failed.traces_exporting


async def test_export_failure_does_not_escape_business_span_or_metric_calls() -> None:
    class BrokenSpanExporter(CapturingSpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            del spans
            raise RuntimeError("SECRET_SPAN_EXPORT_FAILURE")

    metric_exporter = CapturingMetricExporter(fail=True)
    runtime = TelemetryRuntime.build(
        config(
            traces_endpoint="https://collector.example/v1/traces",
            metrics_endpoint="https://collector.example/v1/metrics",
            metric_export_interval=3600.0,
        ),
        labels=labels(),
        environment={},
        _span_exporter_factory=lambda **kwargs: BrokenSpanExporter(),
        _metric_exporter_factory=lambda **kwargs: metric_exporter,
    )
    tracer = runtime.tracer_provider.get_tracer("foliqant.workflow")
    with tracer.start_as_current_span("workflow"):
        pass
    meter = runtime.meter_provider.get_meter("foliqant.metrics")
    meter.create_histogram("foliqant.workflow.duration", unit="s").record(0.1)
    assert runtime.tracer_provider.force_flush(timeout_millis=500)
    runtime.meter_provider.force_flush(timeout_millis=500)
    assert await runtime.aclose(timeout=1)


async def test_partial_processor_and_reader_construction_are_owned_until_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    span_exporter = CapturingSpanExporter()
    metric_exporter = CapturingMetricExporter()

    def broken_processor(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("SECRET_PROCESSOR_FAILURE")

    def broken_reader(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("SECRET_READER_FAILURE")

    monkeypatch.setattr(runtime_module, "BatchSpanProcessor", broken_processor)
    monkeypatch.setattr(runtime_module, "PeriodicExportingMetricReader", broken_reader)
    runtime = TelemetryRuntime.build(
        config(
            traces_endpoint="https://collector.example/v1/traces",
            metrics_endpoint="https://collector.example/v1/metrics",
        ),
        labels=labels(),
        environment={},
        _span_exporter_factory=lambda **kwargs: span_exporter,
        _metric_exporter_factory=lambda **kwargs: metric_exporter,
    )
    assert runtime.startup_failures == frozenset({"traces", "metrics"})
    assert span_exporter.shutdown_calls == 0
    assert metric_exporter.shutdown_calls == 0
    assert await runtime.aclose(timeout=1)
    assert span_exporter.shutdown_calls == 1
    assert metric_exporter.shutdown_calls == 1


async def test_metric_views_export_only_reviewed_host_instruments_and_keys() -> None:
    exporter = CapturingMetricExporter()
    runtime = TelemetryRuntime.build(
        config(
            metrics_endpoint="https://collector.example/v1/metrics",
            metric_export_interval=3600.0,
        ),
        labels=labels(),
        environment={},
        _metric_exporter_factory=lambda **kwargs: exporter,
    )
    host = runtime.meter_provider.get_meter("foliqant.metrics")
    host.create_histogram("foliqant.workflow.duration", unit="s").record(
        0.25,
        {
            "foliqant.workflow.name": "reviewed_workflow",
            "foliqant.outcome": "completed",
            "payload": "SECRET_PAYLOAD",
        },
    )
    host.create_histogram("invented.host.metric", unit="s").record(1)
    runtime.meter_provider.get_meter("pydantic-ai").create_histogram(
        "gen_ai.client.operation.duration", unit="s"
    ).record(1, {"prompt": "SECRET_PROMPT"})
    runtime.meter_provider.force_flush(timeout_millis=500)

    metrics = [
        metric
        for batch in exporter.batches
        for resource in batch.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    ]
    assert [metric.name for metric in metrics] == ["foliqant.workflow.duration"]
    points = list(metrics[0].data.data_points)
    assert len(points) == 1
    assert dict(points[0].attributes) == {
        "foliqant.workflow.name": "reviewed_workflow",
        "foliqant.outcome": "completed",
    }
    assert "SECRET" not in repr(metrics)
    assert await runtime.aclose(timeout=1)


async def test_shutdown_is_off_loop_reuses_one_owned_worker_and_reports_timeout() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingExporter(CapturingSpanExporter):
        def shutdown(self) -> None:
            self.shutdown_calls += 1
            entered.set()
            assert release.wait(timeout=2)

    exporter = BlockingExporter()
    runtime = TelemetryRuntime.build(
        config(traces_endpoint="https://collector.example/v1/traces", shutdown_timeout=1.0),
        labels=labels(),
        environment={},
        _span_exporter_factory=lambda **kwargs: exporter,
    )
    before = asyncio.all_tasks()
    try:
        assert not await runtime.aclose(timeout=0.01)
        assert entered.wait(timeout=1)
        assert asyncio.all_tasks() == before
    finally:
        release.set()
    assert await runtime.aclose(timeout=1)
    assert exporter.shutdown_calls == 1


async def test_exporter_shutdown_failure_is_reported_without_raw_error() -> None:
    class BrokenClose(CapturingSpanExporter):
        def shutdown(self) -> None:
            raise RuntimeError("PRIVATE_CLEANUP_FAILURE")

    runtime = TelemetryRuntime.build(
        config(traces_endpoint="https://collector.example/v1/traces"),
        environment={},
        _span_exporter_factory=lambda **kwargs: BrokenClose(),
    )
    assert not await runtime.aclose(timeout=1)


async def test_flow_and_step_metric_views_preserve_flow_scope() -> None:
    from foliqant.adapters.telemetry.observation import WorkflowTelemetry
    from foliqant.core.observation import observe

    exporter = CapturingMetricExporter()
    allowed = TelemetryLabels(
        workflows=frozenset({"inbox"}),
        flows=frozenset({"triage", "followup"}),
        steps=frozenset({"classify"}),
    )
    runtime = TelemetryRuntime.build(
        config(
            metrics_endpoint="https://collector.example/v1/metrics", metric_export_interval=3600.0
        ),
        labels=allowed,
        environment={},
        _metric_exporter_factory=lambda **kwargs: exporter,
    )
    observer = WorkflowTelemetry(
        runtime.tracer_provider, labels=allowed, meter_provider=runtime.meter_provider
    )
    try:
        with observe(observer, "inbox"):
            for flow in ("triage", "followup"):
                with observe(observer, "inbox", flow=flow):
                    with observe(observer, "inbox", flow=flow, step="classify"):
                        pass
        runtime.meter_provider.force_flush(timeout_millis=500)
        metrics = {
            metric.name: metric
            for batch in exporter.batches
            for resource in batch.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }
        for name in ("foliqant.flow.duration", "foliqant.step.duration"):
            points = metrics[name].data.data_points
            assert {point.attributes["foliqant.flow.name"] for point in points} == {
                "triage",
                "followup",
            }
    finally:
        assert await runtime.aclose(timeout=1)


def test_global_install_rejects_ambient_provider_without_loading_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = TelemetryRuntime.build(config(), labels=labels(), environment={})
    monkeypatch.setenv("OTEL_PYTHON_TRACER_PROVIDER", "untrusted.provider")
    with pytest.raises(RuntimeError, match="ambient host tracer provider"):
        runtime.install_global()


def test_global_install_updates_preexisting_proxy_and_rejects_host_provider() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    installed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio
from opentelemetry import propagate, trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from foliqant.adapters.telemetry.privacy import TelemetryLabels
from foliqant.adapters.telemetry.runtime import TelemetryRuntime
from foliqant.contracts.telemetry import TelemetryConfig
proxy_tracer = trace.get_tracer('mcp-python-sdk')
runtime = TelemetryRuntime.build(
    TelemetryConfig(service_name='foliqant'),
    labels=TelemetryLabels(services=frozenset({'foliqant'})),
    environment={},
)
runtime.install_global()
assert trace.get_tracer_provider() is runtime.tracer_provider
assert isinstance(propagate.get_global_textmap(), TraceContextTextMapPropagator)
assert proxy_tracer.start_span('ping').get_span_context().is_valid
assert asyncio.run(runtime.aclose())
""",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert installed.returncode == 0, installed.stderr

    rejected = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from foliqant.adapters.telemetry.runtime import TelemetryRuntime
from foliqant.contracts.telemetry import TelemetryConfig
host = TracerProvider(shutdown_on_exit=False)
trace.set_tracer_provider(host)
runtime = TelemetryRuntime.build(TelemetryConfig(service_name='foliqant'), environment={})
try:
    runtime.install_global()
except RuntimeError:
    pass
else:
    raise AssertionError('host provider was replaced')
assert trace.get_tracer_provider() is host
""",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert rejected.returncode == 0, rejected.stderr
