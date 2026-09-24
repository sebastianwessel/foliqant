"""Strict deployment references and protected headers for optional OTLP telemetry."""

from typing import Annotated, Self

from pydantic import ConfigDict, Field, SecretStr, ValidationInfo, field_validator, model_validator

from .base import BoundaryModel
from .endpoints import validate_http_endpoint
from .environment import (
    ENVIRONMENT_FIELD,
    EnvironmentSecret,
    EnvironmentText,
    is_environment_reference,
)
from .models import Duration

HeaderName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$",
    ),
]
TelemetryLabel = Annotated[str, Field(max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")]
EndpointText = Annotated[EnvironmentText, Field(max_length=2048)]


class TelemetryConfig(BoundaryModel):
    """Explicit OTLP/HTTP settings; secret header contents are redacted on export."""

    model_config = ConfigDict(frozen=True)

    service_name: TelemetryLabel
    traces_endpoint: EndpointText | None = Field(default=None, json_schema_extra=ENVIRONMENT_FIELD)
    metrics_endpoint: EndpointText | None = Field(default=None, json_schema_extra=ENVIRONMENT_FIELD)
    traces_headers: Annotated[dict[HeaderName, EnvironmentSecret], Field(max_length=64)] = Field(
        default_factory=dict, json_schema_extra=ENVIRONMENT_FIELD
    )
    metrics_headers: Annotated[dict[HeaderName, EnvironmentSecret], Field(max_length=64)] = Field(
        default_factory=dict, json_schema_extra=ENVIRONMENT_FIELD
    )
    allow_insecure_http: bool = False
    span_queue_capacity: Annotated[int, Field(strict=True, ge=1, le=65_536)] = 2048
    span_batch_size: Annotated[int, Field(strict=True, ge=1, le=4096)] = 512
    span_schedule_delay: Duration = 5.0
    metric_export_interval: Duration = 60.0
    metric_export_batch_size: Annotated[int, Field(strict=True, ge=1, le=65_536)] = 512
    export_timeout: Annotated[float, Field(gt=0, le=30)] = 10.0
    shutdown_timeout: Annotated[float, Field(gt=0, le=30)] = 10.0
    conditions: bool = False
    """Also record a debug `condition.evaluated` event for every evaluated condition."""

    @field_validator("traces_headers", "metrics_headers")
    @classmethod
    def valid_headers(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        if any(
            not item.get_secret_value().strip()
            or len(item.get_secret_value()) > 8192
            or any(ord(char) < 32 or ord(char) == 127 for char in item.get_secret_value())
            for item in value.values()
        ):
            raise ValueError("invalid telemetry header value")
        return value

    @field_validator("traces_endpoint", "metrics_endpoint", mode="before")
    @classmethod
    def empty_endpoint_disables_signal(cls, value: object) -> object:
        """Treat only the exact empty deployment value as disabled."""
        return None if type(value) is str and value == "" else value

    @model_validator(mode="after")
    def valid_runtime_bounds(self, info: ValidationInfo) -> Self:
        for endpoint in (self.traces_endpoint, self.metrics_endpoint):
            if endpoint is not None and not (
                is_environment_reference(endpoint)
                and not (info.context and info.context.get("resolved_environment"))
            ):
                validate_http_endpoint(endpoint, allow_insecure_http=self.allow_insecure_http)
        if self.span_batch_size > self.span_queue_capacity:
            raise ValueError("span batch size cannot exceed queue capacity")
        return self
