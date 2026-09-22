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
    "MODEL_BASE_URL": "http://127.0.0.1:1/v1",
    "MODEL_ID": "offline-scripted-fixture",
}


def scripted_response(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    """Return authored fixtures only for the published synthetic inputs.

    These values prove example wiring and validation, never model quality.
    """
    text = repr(messages)
    extracted: dict[str, str | None] | None = None
    option: str | None = None
    status = "answerable"
    issues: list[str] = []
    reason = "No current action in the category catalog is established."
    if "Your help page lists cancellation" in text or "Ihre Hilfeseite nennt" in text:
        reason = "The writer mentions categories but explicitly requests no action."
    elif "I withdraw that request." in text:
        reason = "The only request was withdrawn; there is no current action."
    elif "I have not specified what" in text:
        reason = "The object of the stop request is unspecified."
    elif "Cancel renewal for account C-1049 by 30 September 2026." in text:
        option = "cancellation"
        reason = "The writer explicitly asks to cancel renewal."
        extracted = {
            "requested_action": "Cancel renewal",
            "deadline": "by 30 September 2026",
            "account_reference": "C-1049",
        }
    elif "I dispute invoice INV-882." in text:
        option = "billing_dispute"
        reason = "The writer disputes an invoice and requests a duplicate-charge review."
        extracted = {
            "requested_action": "review the duplicate charge",
            "deadline": None,
            "account_reference": None,
        }
    elif "Add priority support to account A-2205" in text:
        option = "service_change"
        reason = "The writer requests an addition to their subscribed support service."
        extracted = {
            "requested_action": "Add priority support",
            "deadline": "by 15 October 2026",
            "account_reference": "A-2205",
        }
    elif "K-771" in text:
        option = "cancellation"
        reason = "Die automatische Verlängerung soll ausdrücklich gestoppt werden."
        extracted = {
            "requested_action": "stoppen Sie die automatische Verlängerung",
            "deadline": "bis 31. Dezember 2026",
            "account_reference": "K-771",
        }
    elif "RE-550" in text:
        option = "billing_dispute"
        reason = "Die doppelte Belastung wird beanstandet und soll geprüft werden."
        extracted = {
            "requested_action": "prüfen Sie die doppelte Belastung",
            "deadline": "bis 15. Oktober 2026",
            "account_reference": None,
        }
    elif "Both requests are current." in text:
        issues = ["multiple_valid_options"]
        reason = "Two current requests belong to different queues; one queue cannot represent both."
    elif "Neither instruction supersedes the other." in text:
        issues = ["conflicting_information"]
        reason = "Cancel and keep-renewal instructions conflict without a precedence rule."
    elif "Correction: do not cancel it." in text:
        option = "service_change"
        reason = "The correction withdraws cancellation and asks for priority support."
        extracted = {
            "requested_action": "add priority support",
            "deadline": "by 1 November 2026",
            "account_reference": "A-3100",
        }
    elif "advertised accountant position" in text or "Stelle als Buchhalter" in text:
        reason = "A job application does not match any configured support category."
    elif "Please help." in text or "Bitte helfen Sie mir." in text:
        reason = "The message does not establish the requested action."
    else:
        raise ValueError("No scripted response for this synthetic input")
    if option is None:
        status = "not_answerable"
        issues = issues or ["no_supported_answer"]
    if '"id":"classify"' in text:
        value: object = {
            "results": [
                {
                    "questionId": "classify",
                    "type": "choice",
                    "answerability": {
                        "status": status,
                        "issues": issues,
                    },
                    "answer": {"optionId": option} if option else None,
                    "reason": reason,
                    "evidence_strength": "strong",
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
