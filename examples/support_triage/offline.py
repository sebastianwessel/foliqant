"""Scripted responses for synthetic wiring checks, never a substitute classifier."""

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
    "FOLIQANT_CURATION_ENDPOINT_URL": "http://127.0.0.1:1/v1",
    "FOLIQANT_CURATION_MODEL": "offline-scripted-fixture",
}


def scripted_response(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    """Return fixed outputs only for the published synthetic input phrases."""
    text = repr(messages)
    extracted: dict[str, str | None] | None
    if "Cancel renewal for account C-1049 by 30 September 2026." in text:
        option, quote = "cancellation", "Cancel renewal"
        extracted = {
            "requested_action": "cancel renewal",
            "deadline": "30 September 2026",
            "account_reference": "C-1049",
        }
    elif "I dispute invoice INV-882." in text:
        option, quote = "billing_dispute", "I dispute invoice INV-882."
        extracted = {
            "requested_action": "review duplicate charge",
            "deadline": None,
            "account_reference": None,
        }
    elif "Please help." in text:
        option, quote, extracted = None, None, None
    else:
        raise ValueError("No scripted response for this synthetic input")
    if '"id":"classify"' in text:
        value = {
            "schemaVersion": 1,
            "results": [
                {
                    "questionId": "classify",
                    "type": "choice",
                    "answerability": {
                        "status": "answerable" if option else "undetermined",
                        "issues": [] if option else ["missing_information"],
                    },
                    "answer": {"optionId": option} if option else None,
                    "explanation": {
                        "summary": "The sender explicitly states the request."
                        if option
                        else "No actionable request is stated.",
                        "evidence": [{"sourceId": "message", "quote": quote}] if quote else [],
                        "contraryEvidence": [],
                        "missingFacts": [] if option else ["The requested action is missing."],
                    },
                }
            ],
        }
    else:
        if extracted is None:
            raise ValueError("Extraction must not run for the review case")
        value = {"value": extracted}
    return ModelResponse(
        parts=[TextPart(json.dumps(value))], usage=RequestUsage(input_tokens=10, output_tokens=5)
    )


@asynccontextmanager
async def model_factory(
    profiles: ModelProfiles, *, environment: Mapping[str, str]
) -> AsyncIterator[Mapping[str, ModelBinding]]:
    """Inject a PydanticAI FunctionModel; no SDK clients or sockets are opened."""
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
