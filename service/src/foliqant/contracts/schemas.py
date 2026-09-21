"""JSON Schemas generated from the canonical service boundary models."""

from typing import cast

from pydantic import TypeAdapter

from foliqant.core.json import JsonValue

from .envelope import Envelope
from .execution import AcceptanceReceipt, ExecutionResult
from .mcp import McpProfiles
from .models import ModelProfiles
from .workflow import DeclaredToolCatalog, StepAuthoring, WorkflowAuthoring


def service_schemas() -> dict[str, dict[str, JsonValue]]:
    """Return versioned public schemas; runtime mappings have separate tests."""
    schemas = cast(
        dict[str, dict[str, JsonValue]],
        {
            "envelope.schema.json": Envelope.model_json_schema(),
            "execution-result.schema.json": ExecutionResult.model_json_schema(),
            "acceptance-receipt.schema.json": AcceptanceReceipt.model_json_schema(),
            "model-profiles.schema.json": ModelProfiles.model_json_schema(),
            "mcp-profiles.schema.json": McpProfiles.model_json_schema(),
            "workflow.schema.json": WorkflowAuthoring.model_json_schema(),
            "step.schema.json": TypeAdapter(StepAuthoring).json_schema(),
            "tool-catalog.schema.json": DeclaredToolCatalog.model_json_schema(),
        },
    )
    for schema in schemas.values():
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schemas
