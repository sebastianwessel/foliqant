"""Independent scripted decisions for the deterministic routing example."""

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

ENVIRONMENT = {
    "MODEL_BASE_URL": "http://127.0.0.1:1/v1",
    "MODEL_ID": "offline-routed-intake",
}


def scripted_response(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    """Return a reviewed category for the four published tutorial messages."""
    text = repr(messages)
    if "invoice INV-42" in text or "Rechnung R-17" in text:
        option = "billing"
        reason = "The message requests an invoice copy."
    elif "Cancel my subscription" in text or "kündigen Sie mein Abonnement" in text:
        option = "cancellation"
        reason = "The message explicitly requests cancellation."
    else:
        raise ValueError("No scripted response for this synthetic input")
    value = {
        "results": [
            {
                "questionId": "classify",
                "type": "choice",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"optionId": option},
                "reason": reason,
                "evidence_strength": "strong",
            }
        ]
    }
    return ModelResponse(
        parts=[TextPart(json.dumps(value))],
        usage=RequestUsage(input_tokens=8, output_tokens=4),
    )


@asynccontextmanager
async def model_factory(
    profiles: ModelProfiles, *, environment: Mapping[str, str]
) -> AsyncIterator[Mapping[str, ModelBinding]]:
    """Open only local FunctionModel bindings; no socket is created."""
    del environment
    yield {
        alias: ModelBinding(
            model=FunctionModel(scripted_response),
            settings=ModelSettings(),
            admission=CapacityLimiter(concurrency=1, queue_limit=0),
            output_mode="native",
            supports_text=True,
            supports_json_schema=True,
        )
        for alias in profiles.models
    }
