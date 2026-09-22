"""Scripted model turns; MCP execution and tool-result delivery stay real."""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from foliqant.adapters.models import ModelBinding
from foliqant.contracts.models import ModelProfiles
from foliqant.core.admission import CapacityLimiter

MODEL_ENVIRONMENT = {
    "MODEL_BASE_URL": "http://127.0.0.1:1/v1",
    "MODEL_ID": "offline-scripted-fixture",
}


def scripted_response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Require the actual tool result before returning the structured response."""
    returns = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    if returns:
        content = returns[-1].content
        value = json.loads(content) if isinstance(content, str) else content
        return ModelResponse(
            parts=[TextPart(json.dumps({"value": value}))],
            usage=RequestUsage(input_tokens=20, output_tokens=10),
        )
    assert [tool.name for tool in info.function_tools] == ["lookup_request"]
    text = repr(messages)
    if "FOI-2026-0142" in text and "English" in text:
        reference, language = "FOI-2026-0142", "en"
    elif "FOI-2026-0310" in text and "Deutsch" in text:
        reference, language = "FOI-2026-0310", "de"
    else:
        raise ValueError("No scripted tool call for this synthetic message")
    return ModelResponse(
        parts=[
            ToolCallPart(
                "lookup_request",
                {"reference": reference, "language": language},
                tool_call_id="lookup",
            )
        ],
        usage=RequestUsage(input_tokens=10, output_tokens=5),
    )


@asynccontextmanager
async def model_factory(
    profiles: ModelProfiles, *, environment: Mapping[str, str]
) -> AsyncIterator[Mapping[str, ModelBinding]]:
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
