"""Privacy-safe OpenTelemetry instrumentation for model requests."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from opentelemetry.metrics import NoOpMeterProvider
from pydantic_ai.models import Model
from pydantic_ai.models.instrumented import InstrumentationSettings, InstrumentedModel

from foliqant.core.errors import ErrorCode
from foliqant.core.execution import TokenUsage

if TYPE_CHECKING:
    from opentelemetry.metrics import MeterProvider
    from opentelemetry.trace import TracerProvider

    from foliqant.adapters.telemetry.privacy import TelemetryLabels

_SCOPE = "foliqant.metrics"
_DURATION = "gen_ai.client.operation.duration"
_TOKEN_USAGE = "gen_ai.client.token.usage"
_MAX_DURATION_SECONDS = 365 * 24 * 60 * 60
_MAX_COUNT = 2**53 - 1
_TOKEN_HISTOGRAM_BOUNDARIES = (
    1,
    4,
    16,
    64,
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
)


class ModelTelemetry:
    """Instrument model calls without exporting request content or SDK metrics."""

    __slots__ = (
        "_duration",
        "_labels",
        "_settings",
        "_token_usage",
    )

    def __init__(
        self,
        tracer_provider: TracerProvider,
        meter_provider: MeterProvider,
        labels: TelemetryLabels,
    ) -> None:
        self._labels = labels
        self._settings = InstrumentationSettings(
            tracer_provider=tracer_provider,
            meter_provider=NoOpMeterProvider(),
            include_binary_content=False,
            include_content=False,
            include_model_request_parameters=False,
            version=6,
        )
        meter = meter_provider.get_meter(_SCOPE)
        self._duration = meter.create_histogram(
            _DURATION,
            unit="s",
            description="Model client operation duration",
        )
        self._token_usage = meter.create_histogram(
            _TOKEN_USAGE,
            unit="{token}",
            description="Model client token usage",
            explicit_bucket_boundaries_advisory=_TOKEN_HISTOGRAM_BOUNDARIES,
        )

    def instrument(self, model: Model) -> InstrumentedModel:
        """Return the public PydanticAI model wrapper for one client span per request."""

        return InstrumentedModel(model, self._settings)

    @staticmethod
    def start_request() -> float:
        """Capture a monotonic request start instant."""

        return time.perf_counter()

    def record_request(
        self,
        model: Model,
        started_at: float,
        usage: TokenUsage | None,
        error: ErrorCode | None = None,
    ) -> None:
        """Record reviewed metrics; telemetry failures never alter model behavior."""

        try:
            attributes = self._attributes(model)
            if error is not None:
                attributes["error.type"] = error.value
            duration = min(
                max(0.0, time.perf_counter() - started_at),
                _MAX_DURATION_SECONDS,
            )
            self._duration.record(duration, attributes)
            if usage is None:
                return
            for token_type, value in (
                ("input", usage.input_tokens),
                ("output", usage.output_tokens),
            ):
                if value is not None and value <= _MAX_COUNT:
                    self._token_usage.record(
                        value,
                        {**attributes, "gen_ai.token.type": token_type},
                    )
        except Exception:
            return

    def _attributes(self, model: Model) -> dict[str, str]:
        attributes = {"gen_ai.operation.name": "chat"}
        try:
            model_name = model.model_name
            if type(model_name) is str and model_name in self._labels.models:
                attributes["gen_ai.request.model"] = model_name
        except Exception:
            pass
        try:
            provider_name = model.system
            if type(provider_name) is str and provider_name in self._labels.providers:
                attributes["gen_ai.provider.name"] = provider_name
        except Exception:
            pass
        return attributes
