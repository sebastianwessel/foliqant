"""Scripted model outputs prove wiring without calling a provider."""

import json
import re
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
    "MODEL_ID": "offline-scripted-fixture",
}
_BILLING = ("invoice", "charge", "billing")
_CANCELLATION = ("cancel", "renewal")


def _message(text: str) -> str:
    """Return the synthetic message embedded in the request text."""
    for sentence in _MESSAGES:
        if sentence in text:
            return sentence
    raise ValueError("No scripted response for this synthetic message")


_MESSAGES = (
    "Invoice INV-7 was charged twice.",
    "Please cancel renewal for account a 200.",
    "Please review invoice INV-9 for account A-120; my previous account was A-100.",
    "Please cancel renewal for account A-999.",
    "Please help with my account.",
)


def _classify(message: str) -> dict[str, object]:
    lowered = message.lower()
    billing = any(word in lowered for word in _BILLING)
    cancellation = any(word in lowered for word in _CANCELLATION)
    option = (
        "billing"
        if billing and not cancellation
        else "cancellation"
        if cancellation and not billing
        else None
    )
    return {
        "results": [
            {
                "questionId": "classify",
                "type": "choice",
                "answerability": {
                    "status": "answerable" if option else "not_answerable",
                    "issues": [] if option else ["no_supported_answer"],
                },
                "answer": {"optionId": option} if option else None,
                "reason": "The message states the requested action."
                if option
                else "The message names no supported action.",
                "evidence_strength": "strong",
            }
        ]
    }


def scripted_response(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    text = repr(messages)
    message = _message(text)
    value: object
    if '"id":"classify"' in text:
        value = _classify(message)
    elif "Extract the customer account reference" in text:
        found = re.search(r"account (A-[0-9]+|a [0-9]+)", message)
        value = {"value": {"account_reference": found.group(1) if found else None}}
    elif "Repair the account reference format" in text:
        found = re.search(r"a ([0-9]{3})", message)
        if found is None:
            raise ValueError("No scripted repair for this synthetic message")
        value = {"value": {"account_reference": f"A-{found.group(1)}"}}
    elif "The account lookup found no record" in text:
        previous = re.search(r"previous account was (A-[0-9]{3})", message)
        value = {
            "value": {
                "status": "corrected" if previous else "no_correction",
                "account_reference": previous.group(1) if previous else None,
            }
        }
    else:
        raise ValueError("No scripted response for this synthetic step")
    return ModelResponse(
        parts=[TextPart(json.dumps(value))],
        usage=RequestUsage(input_tokens=10, output_tokens=5),
    )


@asynccontextmanager
async def model_factory(
    profiles: ModelProfiles, *, environment: Mapping[str, str]
) -> AsyncIterator[Mapping[str, ModelBinding]]:
    """Inject a local FunctionModel while keeping the real stdio MCP server."""
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
