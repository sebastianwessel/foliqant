"""Fixture lookup for wiring tests only; this double is not a security classifier."""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import cast

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from examples.security_evaluation.fixtures import CASES
from foliqant.adapters.models import ModelBinding
from foliqant.contracts.models import ModelProfiles
from foliqant.core.admission import CapacityLimiter
from foliqant.core.json import JsonValue

ENVIRONMENT = {"MODEL_BASE_URL": "http://127.0.0.1:1/v1", "MODEL_ID": "offline-scripted-security"}


def user_document(messages: list[ModelMessage]) -> dict[str, JsonValue]:
    """Decode the actual JSON user message, preserving source strings exactly."""
    prompts = [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    if len(prompts) != 1 or not isinstance(prompts[0], str):
        raise ValueError("Expected one fresh JSON user message")
    value = json.loads(prompts[0])
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return cast(dict[str, JsonValue], value)


def scripted_response(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    """Return authored answers by exact source lookup; measure wiring, not quality."""
    document = user_document(messages)
    decision = "questions" in document
    if decision:
        state = document["state"]
        assert isinstance(state, dict)
        sources = state["sources"]
        assert isinstance(sources, list) and len(sources) == 1
        source = sources[0]
        assert isinstance(source, dict) and source["id"] == "original_message"
        text = source["text"]
    else:
        text = document["original_message"]
    case = next((item for item in CASES if item.original_message == text), None)
    if case is None:
        raise ValueError("No offline fixture matches the exact supplied source")
    if decision:
        value: JsonValue = {"results": [case.assessment()]}
    else:
        if case.action is None:
            raise ValueError("Review cases must never reach confirmation")
        value = {
            "value": {
                "action": case.action,
                "reference": case.reference,
                "evidence_origin": "original_message",
            }
        }
    return ModelResponse(
        parts=[TextPart(json.dumps(value, ensure_ascii=False))],
        usage=RequestUsage(input_tokens=10, output_tokens=5),
    )


@asynccontextmanager
async def model_factory(
    profiles: ModelProfiles, *, environment: Mapping[str, str]
) -> AsyncIterator[Mapping[str, ModelBinding]]:
    """Install deterministic PydanticAI doubles without opening provider clients."""
    del environment
    yield {
        name: ModelBinding(
            FunctionModel(scripted_response),
            ModelSettings(),
            CapacityLimiter(concurrency=1, queue_limit=0),
            "native",
        )
        for name in profiles.models
    }
