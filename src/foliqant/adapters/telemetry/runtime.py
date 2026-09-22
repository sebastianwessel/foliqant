"""Explicit, bounded ownership of optional OpenTelemetry SDK providers."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any, Protocol

import requests
from opentelemetry import propagate
from opentelemetry import trace as trace_api
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Histogram, NoOpMeterProvider
from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter, MeterProvider
from opentelemetry.sdk.metrics.export import MetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import DropAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import ProxyTracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from foliqant.contracts.telemetry import TelemetryConfig
from foliqant.environment import EnvironmentResolver

from .privacy import SafeSpanProcessor, TelemetryLabels

_DEFAULT_LABELS = TelemetryLabels()
_METRIC_SCOPE = "foliqant.metrics"
_FIXED_HEADERS = {"User-Agent": "foliqant"}

# The HTTP exporter has no public constructor sentinel that disables ambient
# client certificate and credential-provider settings. Reject those settings
# instead of mutating the process environment or accepting hidden credentials.
_COMMON_UNSAFE_ENV = frozenset(
    {
        "OTEL_SDK_DISABLED",
        "OTEL_EXPORTER_OTLP_CLIENT_KEY",
        "OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE",
        "OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER",
    }
)
_TRACE_UNSAFE_ENV = frozenset(
    {
        "OTEL_EXPORTER_OTLP_TRACES_CLIENT_KEY",
        "OTEL_EXPORTER_OTLP_TRACES_CLIENT_CERTIFICATE",
        "OTEL_PYTHON_EXPORTER_OTLP_HTTP_TRACES_CREDENTIAL_PROVIDER",
    }
)
_METRIC_UNSAFE_ENV = frozenset(
    {
        "OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY",
        "OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE",
        "OTEL_EXPORTER_OTLP_METRICS_DEFAULT_HISTOGRAM_AGGREGATION",
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
        "OTEL_PYTHON_EXPORTER_OTLP_HTTP_METRICS_CREDENTIAL_PROVIDER",
    }
)


class _SpanExporterFactory(Protocol):
    def __call__(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        timeout: float,
    ) -> SpanExporter: ...


class _MetricExporterFactory(Protocol):
    def __call__(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        timeout: float,
        max_export_batch_size: int,
    ) -> MetricExporter: ...


class _ExporterSession(requests.Session):
    """Send only to the configured collector, without consuming response bodies.

    OTLP exporters use only status codes. Do not retain collector-controlled body,
    headers or reason phrases, or pass transport exceptions containing URLs to
    SDK loggers. Redirects are failures, never authorization to forward headers.
    """

    def get_redirect_target(self, resp: requests.Response) -> None:
        # requests prepares redirects even with allow_redirects=False; that path
        # eagerly consumes response.content. Disable discovery as well as following.
        return None

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        kwargs["allow_redirects"] = False
        kwargs["stream"] = True
        try:
            response = super().send(request, **kwargs)
        except requests.RequestException:
            # Fail this best-effort export without SDK immediate connection retries
            # or exception strings containing endpoint/credential details.
            raise requests.RequestException("telemetry transport failed") from None
        try:
            safe = requests.Response()
            safe.status_code = 400 if 300 <= response.status_code < 400 else response.status_code
            safe.reason = "telemetry export response"
            safe._content = b""
            return safe
        finally:
            response.close()


def _session() -> requests.Session:
    session = _ExporterSession()
    session.trust_env = False
    return session


def _span_exporter(
    *,
    endpoint: str,
    headers: dict[str, str],
    timeout: float,
) -> SpanExporter:
    session = _session()
    try:
        return OTLPSpanExporter(
            endpoint=endpoint,
            certificate_file=requests.certs.where(),  # type: ignore[attr-defined]
            headers={**_FIXED_HEADERS, **headers},
            timeout=timeout,
            compression=Compression.NoCompression,
            session=session,
            meter_provider=NoOpMeterProvider(),
        )
    except BaseException:
        session.close()
        raise


def _metric_exporter(
    *,
    endpoint: str,
    headers: dict[str, str],
    timeout: float,
    max_export_batch_size: int,
) -> MetricExporter:
    session = _session()
    try:
        return OTLPMetricExporter(
            endpoint=endpoint,
            certificate_file=requests.certs.where(),  # type: ignore[attr-defined]
            headers={**_FIXED_HEADERS, **headers},
            timeout=timeout,
            compression=Compression.NoCompression,
            session=session,
            max_export_batch_size=max_export_batch_size,
            meter_provider=NoOpMeterProvider(),
        )
    except BaseException:
        session.close()
        raise


def _reject_unsafe_ambient(config: TelemetryConfig) -> None:
    if config.traces_endpoint is None and config.metrics_endpoint is None:
        return
    unsafe = set(_COMMON_UNSAFE_ENV)
    if config.traces_endpoint is not None:
        unsafe.update(_TRACE_UNSAFE_ENV)
    if config.metrics_endpoint is not None:
        unsafe.update(_METRIC_UNSAFE_ENV)
    if any(name in os.environ for name in unsafe):
        raise ValueError("conflicting ambient OpenTelemetry configuration")


def _metric_views() -> tuple[View, ...]:
    return (
        View(
            instrument_type=Histogram,
            instrument_name="foliqant.workflow.duration",
            instrument_unit="s",
            meter_name=_METRIC_SCOPE,
            attribute_keys={"foliqant.workflow.name", "foliqant.outcome", "error.type"},
        ),
        View(
            instrument_type=Histogram,
            instrument_name="foliqant.flow.duration",
            instrument_unit="s",
            meter_name=_METRIC_SCOPE,
            attribute_keys={
                "foliqant.workflow.name",
                "foliqant.flow.name",
                "foliqant.outcome",
                "error.type",
            },
        ),
        View(
            instrument_type=Histogram,
            instrument_name="foliqant.step.duration",
            instrument_unit="s",
            meter_name=_METRIC_SCOPE,
            attribute_keys={
                "foliqant.workflow.name",
                "foliqant.flow.name",
                "foliqant.step.name",
                "foliqant.outcome",
                "error.type",
            },
        ),
        View(
            instrument_type=Histogram,
            instrument_name="gen_ai.client.operation.duration",
            instrument_unit="s",
            meter_name=_METRIC_SCOPE,
            attribute_keys={
                "gen_ai.operation.name",
                "gen_ai.provider.name",
                "gen_ai.request.model",
                "error.type",
            },
        ),
        View(
            instrument_type=Histogram,
            instrument_name="gen_ai.client.token.usage",
            instrument_unit="{token}",
            meter_name=_METRIC_SCOPE,
            attribute_keys={
                "gen_ai.operation.name",
                "gen_ai.provider.name",
                "gen_ai.request.model",
                "gen_ai.token.type",
            },
        ),
        View(instrument_name="*", aggregation=DropAggregation()),
    )


class TelemetryRuntime:
    """Own explicit providers, exporters, worker threads, and bounded shutdown."""

    def __init__(
        self,
        *,
        config: TelemetryConfig,
        tracer_provider: TracerProvider,
        meter_provider: MeterProvider,
        startup_failures: frozenset[str],
        traces_exporting: bool,
        metrics_exporting: bool,
        cleanup: tuple[Callable[[], None], ...],
    ) -> None:
        self.config = config
        self.tracer_provider = tracer_provider
        self.meter_provider = meter_provider
        self.startup_failures = startup_failures
        self.traces_exporting = traces_exporting
        self.metrics_exporting = metrics_exporting
        self._cleanup = cleanup
        self._global_lock = threading.Lock()
        self._shutdown_lock = threading.Lock()
        self._shutdown_thread: threading.Thread | None = None
        self._shutdown_ok = False

    @classmethod
    def build(
        cls,
        config: TelemetryConfig,
        *,
        labels: TelemetryLabels = _DEFAULT_LABELS,
        environment: Mapping[str, str],
        _span_exporter_factory: _SpanExporterFactory = _span_exporter,
        _metric_exporter_factory: _MetricExporterFactory = _metric_exporter,
    ) -> TelemetryRuntime:
        """Create providers without installing globals or discovering endpoints."""
        if (
            type(config) is not TelemetryConfig
            or type(labels) is not TelemetryLabels
            or not isinstance(environment, Mapping)
        ):
            raise ValueError("invalid telemetry runtime configuration")
        config = EnvironmentResolver(environment).resolve(config)
        _reject_unsafe_ambient(config)

        resource = Resource({"service.name": config.service_name})
        meter_readers: list[PeriodicExportingMetricReader] = []
        cleanup: list[Callable[[], None]] = []
        failures: set[str] = set()
        metrics_exporting = False
        if config.metrics_endpoint is not None:
            try:
                metric_headers = {
                    key: value.get_secret_value() for key, value in config.metrics_headers.items()
                }
                metric_exporter = _metric_exporter_factory(
                    endpoint=config.metrics_endpoint,
                    headers=metric_headers,
                    timeout=config.export_timeout,
                    max_export_batch_size=config.metric_export_batch_size,
                )
                cleanup.append(partial(metric_exporter.shutdown, timeout_millis=0))
                meter_readers.append(
                    PeriodicExportingMetricReader(
                        metric_exporter,
                        export_interval_millis=config.metric_export_interval * 1000,
                        export_timeout_millis=config.export_timeout * 1000,
                    )
                )
                cleanup.clear()
                metrics_exporting = True
            except Exception:
                failures.add("metrics")

        try:
            meter_provider = MeterProvider(
                metric_readers=meter_readers,
                resource=resource,
                exemplar_filter=AlwaysOffExemplarFilter(),
                shutdown_on_exit=False,
                views=_metric_views(),
            )
        except Exception:
            failures.add("metrics")
            metrics_exporting = False
            if meter_readers:
                reader = meter_readers[0]
                cleanup.append(partial(reader.shutdown, timeout_millis=0))
            meter_provider = MeterProvider(
                resource=resource,
                exemplar_filter=AlwaysOffExemplarFilter(),
                shutdown_on_exit=False,
                views=_metric_views(),
            )
        tracer_provider = TracerProvider(
            sampler=ALWAYS_ON,
            resource=resource,
            shutdown_on_exit=False,
            span_limits=SpanLimits(
                max_attributes=64,
                max_events=0,
                max_links=32,
                max_span_attributes=64,
                max_event_attributes=16,
                max_link_attributes=8,
                max_attribute_length=1024,
                max_span_attribute_length=1024,
            ),
            meter_provider=NoOpMeterProvider(),
        )
        traces_exporting = False
        if config.traces_endpoint is not None:
            try:
                trace_headers = {
                    key: value.get_secret_value() for key, value in config.traces_headers.items()
                }
                span_exporter = _span_exporter_factory(
                    endpoint=config.traces_endpoint,
                    headers=trace_headers,
                    timeout=config.export_timeout,
                )
                cleanup.append(span_exporter.shutdown)
                batch_processor = BatchSpanProcessor(
                    span_exporter,
                    max_queue_size=config.span_queue_capacity,
                    schedule_delay_millis=config.span_schedule_delay * 1000,
                    max_export_batch_size=config.span_batch_size,
                    export_timeout_millis=config.export_timeout * 1000,
                    meter_provider=NoOpMeterProvider(),
                )
                cleanup[-1] = batch_processor.shutdown
                tracer_provider.add_span_processor(SafeSpanProcessor(batch_processor, labels))
                cleanup.pop()
                traces_exporting = True
            except Exception:
                failures.add("traces")

        return cls(
            config=config,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            startup_failures=frozenset(failures),
            traces_exporting=traces_exporting,
            metrics_exporting=metrics_exporting,
            cleanup=tuple(cleanup),
        )

    def install_global(self) -> None:
        """Install tracing and trace-context propagation once during process startup.

        This deliberately does not install the meter provider. Foliqant passes it
        directly to reviewed instruments, while third-party SDK metrics stay off.
        """
        with self._global_lock:
            if "OTEL_PYTHON_TRACER_PROVIDER" in os.environ:
                raise RuntimeError("an ambient host tracer provider is configured")
            current = trace_api.get_tracer_provider()
            if current is self.tracer_provider:
                return
            if not isinstance(current, ProxyTracerProvider):
                raise RuntimeError("a host tracer provider is already configured")
            trace_api.set_tracer_provider(self.tracer_provider)
            if trace_api.get_tracer_provider() is not self.tracer_provider:
                raise RuntimeError("a host tracer provider won global installation")
            propagate.set_global_textmap(TraceContextTextMapPropagator())

    async def aclose(self, *, timeout: float | None = None) -> bool:
        """Shut down owned SDK workers off-loop; False reports an incomplete drain."""
        wait_timeout = self.config.shutdown_timeout if timeout is None else timeout
        if type(wait_timeout) not in (int, float) or not 0 <= wait_timeout <= 30:
            raise ValueError("invalid telemetry shutdown timeout")
        with self._shutdown_lock:
            if self._shutdown_thread is None:
                self._shutdown_thread = threading.Thread(
                    target=self._shutdown,
                    name="foliqant-telemetry-shutdown",
                    daemon=True,
                )
                self._shutdown_thread.start()
            shutdown_thread = self._shutdown_thread

        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(wait_timeout)
        while shutdown_thread.is_alive():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.01, remaining))
        return self._shutdown_ok

    def _shutdown(self) -> None:
        ok = True
        try:
            self.meter_provider.shutdown(timeout_millis=self.config.shutdown_timeout * 1000)
        except Exception:
            ok = False
        try:
            self.tracer_provider.shutdown()
        except Exception:
            ok = False
        for close in self._cleanup:
            try:
                close()
            except Exception:
                ok = False
        self._shutdown_ok = ok
