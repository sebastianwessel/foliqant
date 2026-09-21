"""JSON Schemas generated from the canonical public boundary models."""

from typing import cast

from pydantic import TypeAdapter

from foliqant.core.json import JsonValue
from foliqant.decisions import CategoryCatalog, DecisionInput, DecisionOutput

from .deployment import DeploymentConfig
from .envelope import Envelope
from .execution import ExecutionResult
from .mcp import McpProfiles
from .models import ModelProfiles
from .telemetry import TelemetryConfig
from .workflow import DeclaredToolCatalog, StepAuthoring, WorkflowAuthoring


def runtime_schemas() -> dict[str, dict[str, JsonValue]]:
    """Return versioned runtime schemas generated from the library boundary types."""
    schemas = cast(
        dict[str, dict[str, JsonValue]],
        {
            "deployment.schema.json": DeploymentConfig.model_json_schema(),
            "envelope.schema.json": Envelope.model_json_schema(),
            "execution-result.schema.json": ExecutionResult.model_json_schema(),
            "model-profiles.schema.json": ModelProfiles.model_json_schema(),
            "mcp-profiles.schema.json": McpProfiles.model_json_schema(),
            "telemetry.schema.json": TelemetryConfig.model_json_schema(),
            "workflow.schema.json": WorkflowAuthoring.model_json_schema(),
            "step.schema.json": TypeAdapter(StepAuthoring).json_schema(),
            "tool-catalog.schema.json": DeclaredToolCatalog.model_json_schema(),
        },
    )
    for schema in schemas.values():
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schemas


def decision_schemas() -> dict[str, dict[str, JsonValue]]:
    """Return versioned decision schemas shared by runtime and model tooling."""
    schemas = cast(
        dict[str, dict[str, JsonValue]],
        {
            "category-catalog.schema.json": CategoryCatalog.model_json_schema(),
            "decision-input.schema.json": DecisionInput.model_json_schema(),
            "decision-output.schema.json": DecisionOutput.model_json_schema(),
        },
    )
    for schema in schemas.values():
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schemas
