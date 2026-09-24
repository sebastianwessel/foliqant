"""Privacy-safe OpenTelemetry instrumentation for model requests."""

from __future__ import annotations

import time
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from opentelemetry import trace as trace_api
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.util.types import AttributeValue
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.instrumented import InstrumentationSettings, InstrumentedModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from foliqant.adapters.models.accounting import request_token_usage
from foliqant.core.errors import ErrorCode
from foliqant.core.execution import TokenUsage
from foliqant.core.pricing import PricingPlan

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


_COST_QUANTUM = Decimal("0.000001")


def usage_span_attributes(
    tokens: TokenUsage, pricing: PricingPlan | None
) -> dict[str, AttributeValue]:
    """OTel GenAI token counts that were reported, and the configured cost estimate."""
    attributes: dict[str, AttributeValue] = {}
    for key, value in (
        ("gen_ai.usage.input_tokens", tokens.input_tokens),
        ("gen_ai.usage.output_tokens", tokens.output_tokens),
        ("gen_ai.usage.cache_read.input_tokens", tokens.cache_read_input_tokens),
        ("gen_ai.usage.cache_creation.input_tokens", tokens.cache_write_input_tokens),
        ("gen_ai.usage.reasoning.output_tokens", tokens.reasoning_output_tokens),
    ):
        if value is not None and value <= _MAX_COUNT:
            attributes[key] = value
    if pricing is not None:
        cost = pricing.request_cost(tokens)
        if cost.amount is not None:
            attributes["foliqant.usage.cost"] = float(
                cost.amount.quantize(_COST_QUANTUM, rounding=ROUND_HALF_EVEN)
            )
    return attributes


class _UsageAnnotation(WrapperModel):
    """Annotate the active ``chat`` span with reported usage before the span closes."""

    def __init__(self, wrapped: Model, pricing: PricingPlan | None) -> None:
        super().__init__(wrapped)
        self._pricing = pricing

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await self.wrapped.request(messages, model_settings, model_request_parameters)
        try:
            span = trace_api.get_current_span()
            if span.is_recording():
                span.set_attributes(
                    usage_span_attributes(request_token_usage(response.usage), self._pricing)
                )
        except Exception:
            # Telemetry never changes the model response or its accounting.
            pass
        return response


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

    def instrument(self, model: Model, pricing: PricingPlan | None = None) -> InstrumentedModel:
        """Return the public PydanticAI model wrapper for one client span per request.

        The ``chat <model>`` span also carries the reported cached and reasoning
        token counts and, with ``pricing``, the request's ``foliqant.usage.cost``.
        """

        return InstrumentedModel(_UsageAnnotation(model, pricing), self._settings)

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
