"""Optional, process-owned telemetry setup for the deterministic demo."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import ValidationError

from foliqant.adapters.telemetry.logging import LogEvent, configure_logging, emit_event
from foliqant.adapters.telemetry.observation import WorkflowTelemetry
from foliqant.adapters.telemetry.privacy import TelemetryLabels
from foliqant.adapters.telemetry.runtime import TelemetryRuntime
from foliqant.contracts.telemetry import TelemetryConfig
from foliqant.core.errors import ErrorCode, ServiceError


@asynccontextmanager
async def telemetry_observer() -> AsyncIterator[WorkflowTelemetry]:
    """Create no exporters unless a signal endpoint is explicitly supplied."""
    log_runtime = configure_logging()
    logger = logging.getLogger("foliqant.example")
    runtime: TelemetryRuntime | None = None
    try:
        environment = dict(os.environ)
        insecure = environment.get("FOLIQANT_OTLP_ALLOW_INSECURE_HTTP", "false")
        if insecure not in {"true", "false"}:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        try:
            config = TelemetryConfig(
                service_name="embedded_demo",
                traces_endpoint=environment.get("FOLIQANT_OTLP_TRACES_ENDPOINT"),
                metrics_endpoint=environment.get("FOLIQANT_OTLP_METRICS_ENDPOINT"),
                allow_insecure_http=insecure == "true",
            )
            labels = TelemetryLabels(
                services=frozenset({"embedded_demo"}),
                workflows=frozenset({"embedded_triage"}),
                steps=frozenset({"route", "render", "done", "review"}),
            )
            runtime = TelemetryRuntime.build(config, labels=labels, environment=environment)
            runtime.install_global()
        except (ValidationError, ValueError, RuntimeError):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
        if runtime.startup_failures:
            emit_event(
                logger,
                LogEvent.DEPENDENCY_REJECTED,
                level=logging.WARNING,
                error_code=ErrorCode.DEPENDENCY_FAILURE,
            )
        yield WorkflowTelemetry(
            runtime.tracer_provider, labels=labels, meter_provider=runtime.meter_provider
        )
    finally:
        if runtime is not None and not await runtime.aclose():
            emit_event(
                logger,
                LogEvent.DEPENDENCY_REJECTED,
                level=logging.WARNING,
                error_code=ErrorCode.DEPENDENCY_FAILURE,
            )
        await asyncio.to_thread(log_runtime.close, timeout=1.0)
