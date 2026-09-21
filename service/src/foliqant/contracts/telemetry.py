"""Strict, reference-only configuration for optional OTLP telemetry."""

from typing import Annotated, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from .base import BoundaryModel
from .endpoints import validate_http_endpoint
from .models import Duration, EnvironmentName

HeaderName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$",
    ),
]
TelemetryLabel = Annotated[str, Field(max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")]
EndpointText = Annotated[str, Field(max_length=2048)]


class TelemetryConfig(BoundaryModel):
    """Explicit OTLP/HTTP settings; header values remain outside configuration."""

    model_config = ConfigDict(frozen=True)

    service_name: TelemetryLabel
    traces_endpoint: EndpointText | None = None
    metrics_endpoint: EndpointText | None = None
    traces_headers_env: Annotated[dict[HeaderName, EnvironmentName], Field(max_length=64)] = Field(
        default_factory=dict
    )
    metrics_headers_env: Annotated[dict[HeaderName, EnvironmentName], Field(max_length=64)] = Field(
        default_factory=dict
    )
    allow_insecure_http: bool = False
    span_queue_capacity: Annotated[int, Field(strict=True, ge=1, le=65_536)] = 2048
    span_batch_size: Annotated[int, Field(strict=True, ge=1, le=4096)] = 512
    span_schedule_delay: Duration = 5.0
    metric_export_interval: Duration = 60.0
    metric_export_batch_size: Annotated[int, Field(strict=True, ge=1, le=65_536)] = 512
    export_timeout: Annotated[float, Field(gt=0, le=30)] = 10.0
    shutdown_timeout: Annotated[float, Field(gt=0, le=30)] = 10.0

    @field_validator("traces_endpoint", "metrics_endpoint", mode="before")
    @classmethod
    def empty_endpoint_disables_signal(cls, value: object) -> object:
        """Treat only the exact empty deployment value as disabled."""
        return None if type(value) is str and value == "" else value

    @model_validator(mode="after")
    def valid_runtime_bounds(self) -> Self:
        for endpoint in (self.traces_endpoint, self.metrics_endpoint):
            if endpoint is not None:
                validate_http_endpoint(endpoint, allow_insecure_http=self.allow_insecure_http)
        if self.span_batch_size > self.span_queue_capacity:
            raise ValueError("span batch size cannot exceed queue capacity")
        return self
