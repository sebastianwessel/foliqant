"""Scripted extraction for offline wiring checks; it is not a quality claim."""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from foliqant.adapters.models import ModelBinding
from foliqant.contracts.models import ModelProfiles
from foliqant.core.admission import CapacityLimiter

MODEL_ENVIRONMENT = {
    "FOLIQANT_CURATION_ENDPOINT_URL": "http://127.0.0.1:1/v1",
    "FOLIQANT_CURATION_MODEL": "offline-scripted-fixture",
}


def scripted_response(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    """Return authored fixtures for the two published synthetic messages."""

    text = repr(messages)
    if "FOI-2026-0142" in text and "English" in text:
        extracted = {
            "reference": "FOI-2026-0142",
            "language": "en",
            "internal_summary": "English request-status lookup.",
        }
    elif "FOI-2026-0310" in text and "Deutsch" in text:
        extracted = {
            "reference": "FOI-2026-0310",
            "language": "de",
            "internal_summary": "Deutsche Statusabfrage.",
        }
    else:
        raise ValueError("No scripted extraction for this synthetic input")
    return ModelResponse(
        parts=[TextPart(json.dumps({"value": extracted}))],
        usage=RequestUsage(input_tokens=10, output_tokens=5),
    )


@asynccontextmanager
async def model_factory(
    profiles: ModelProfiles, *, environment: Mapping[str, str]
) -> AsyncIterator[Mapping[str, ModelBinding]]:
    """Inject a local FunctionModel while retaining the real stdio MCP client."""

    del environment
    assert set(profiles.models) == {"local_qwen"}
    yield {
        "local_qwen": ModelBinding(
            model=FunctionModel(scripted_response),
            settings=ModelSettings(),
            admission=CapacityLimiter(concurrency=1, queue_limit=0),
            output_mode="native",
            supports_text=True,
            supports_json_schema=True,
        )
    }
