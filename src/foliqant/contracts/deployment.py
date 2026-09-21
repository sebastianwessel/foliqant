"""Closed deployment settings composed from the existing adapter contracts."""

from typing import Annotated

from pydantic import Field

from foliqant.core.runner import ExecutionLimits

from .base import BoundaryModel, Version1
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

    version: Version1
    workflows: Annotated[dict[Id, NonBlank], Field(min_length=1, max_length=64)]
    models: Annotated[dict[Id, ModelConfig], Field(max_length=128)] = Field(default_factory=dict)
    mcp: Annotated[dict[Id, McpServerProfile], Field(max_length=128)] = Field(default_factory=dict)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    telemetry: TelemetryConfig | None = None
