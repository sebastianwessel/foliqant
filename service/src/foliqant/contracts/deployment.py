"""Closed deployment settings composed from the existing adapter contracts."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from foliqant.core.runner import ExecutionLimits

from .auth import BearerAuthConfig, JwtAuthConfig
from .base import BoundaryModel
from .http import HttpConfig, validate_http_mode
from .mcp import McpServerProfile
from .models import Duration, ModelConfig
from .telemetry import TelemetryConfig
from .workflow import Id, NonBlank


class ExecutionConfig(BoundaryModel):
    """Per-replica admission and per-run attempt/time bounds."""

    concurrency: Annotated[int, Field(ge=1, le=1024)] = 4
    queue_limit: Annotated[int, Field(ge=0, le=65536)] = 16
    run_timeout: Duration = 300.0
    model_timeout: Duration = 60.0
    tool_timeout: Duration = 30.0
    max_steps: Annotated[int, Field(ge=1, le=1024)] = 32
    model_requests_per_step: Annotated[int, Field(ge=1, le=1024)] = 4
    tool_calls_per_step: Annotated[int, Field(ge=1, le=1024)] = 3

    def limits(self) -> ExecutionLimits:
        """Map validated settings to the standard-library runtime value."""
        return ExecutionLimits(
            run_timeout=self.run_timeout,
            model_timeout=self.model_timeout,
            tool_timeout=self.tool_timeout,
            max_steps=self.max_steps,
            model_requests_per_step=self.model_requests_per_step,
            tool_calls_per_step=self.tool_calls_per_step,
        )


class DeploymentConfig(BoundaryModel):
    """Configuration contains references and policy, never resolved credentials."""

    version: Literal[1]
    mode: Literal["production", "development"] = "production"
    workflows: Annotated[dict[Id, NonBlank], Field(min_length=1, max_length=64)]
    models: Annotated[dict[Id, ModelConfig], Field(max_length=128)] = Field(default_factory=dict)
    mcp: Annotated[dict[Id, McpServerProfile], Field(max_length=128)] = Field(default_factory=dict)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    http: HttpConfig | None = None
    telemetry: TelemetryConfig | None = None

    @model_validator(mode="after")
    def validate_modes_and_grants(self) -> Self:
        if self.http is not None:
            validate_http_mode(self.http, self.mode)
            auth = self.http.auth
            grants = (
                [grant for binding in auth.bindings.values() for grant in binding.workflows]
                if isinstance(auth, BearerAuthConfig)
                else auth.workflows
                if isinstance(auth, JwtAuthConfig)
                else []
            )
            if not set(grants).issubset(self.workflows):
                raise ValueError("workflow grants must refer to configured workflows")
        return self
