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
    """Return authored fixtures only for the published synthetic inputs.

    These values prove example wiring and validation, never model quality.
    """
    text = repr(messages)
    extracted: dict[str, str | None] | None
    status = "answerable"
    issues: list[str] = []
    contrary: list[dict[str, str]] = []
    missing: list[str] = []
    if any(
        marker in text
        for marker in (
            "Your help page lists cancellation and priority support.",
            "Ihre Hilfeseite nennt Kündigung und Premium-Support.",
            "I withdraw that request.",
            "I have not specified what",
        )
    ):
        option, quote, extracted = None, None, None
        status = "not_answerable"
        issues = ["no_supported_answer"]
        missing = ["No current action in the category catalog is established."]
    elif "Cancel renewal for account C-1049 by 30 September 2026." in text:
        option, quote = "cancellation", "Cancel renewal"
        extracted = {
            "requested_action": "Cancel renewal",
            "deadline": "by 30 September 2026",
            "account_reference": "C-1049",
        }
    elif "I dispute invoice INV-882." in text:
        option, quote = "billing_dispute", "I dispute invoice INV-882."
        extracted = {
            "requested_action": "review the duplicate charge",
            "deadline": None,
            "account_reference": None,
        }
    elif "Add priority support to account A-2205" in text:
        option, quote = "service_change", "Add priority support"
        extracted = {
            "requested_action": "Add priority support",
            "deadline": "by 15 October 2026",
            "account_reference": "A-2205",
        }
    elif "K-771" in text:
        option, quote = "cancellation", "automatische Verlängerung"
        extracted = {
            "requested_action": "stoppen Sie die automatische Verlängerung",
            "deadline": "bis 31. Dezember 2026",
            "account_reference": "K-771",
        }
    elif "RE-550" in text:
        option, quote = "billing_dispute", "doppelten Belastung"
        extracted = {
            "requested_action": "prüfen Sie die doppelte Belastung",
            "deadline": "bis 15. Oktober 2026",
            "account_reference": None,
        }
    elif "Both requests are current." in text:
        option, quote, extracted = None, None, None
        status = "not_answerable"
        issues = ["multiple_valid_options"]
        contrary = [
            {"sourceId": "message", "quote": "cancel renewal"},
            {"sourceId": "message", "quote": "add priority support"},
        ]
    elif "Neither instruction supersedes the other." in text:
        option, quote, extracted = None, None, None
        status = "not_answerable"
        issues = ["conflicting_information"]
        contrary = [
            {"sourceId": "message", "quote": "cancel renewal"},
            {"sourceId": "message", "quote": "keep the renewal active"},
        ]
    elif "Correction: do not cancel it." in text:
        option, quote = "service_change", "Please add priority support"
        extracted = {
            "requested_action": "add priority support",
            "deadline": "by 1 November 2026",
            "account_reference": "A-3100",
        }
    elif "advertised accountant position" in text or "Stelle als Buchhalter" in text:
        option, extracted = None, None
        quote = (
            "Ich möchte mich auf die ausgeschriebene Stelle als Buchhalter bewerben."
            if "Stelle als Buchhalter" in text
            else "I would like to apply for the advertised accountant position."
        )
        status = "not_answerable"
        issues = ["no_supported_answer"]
    elif "Please help." in text or "Bitte helfen Sie mir." in text:
        option, quote, extracted = None, None, None
        status = "not_answerable"
        issues = ["no_supported_answer"]
        missing = ["The requested action is missing."]
    else:
        raise ValueError("No scripted response for this synthetic input")
    if '"id":"classify"' in text:
        value = {
            "schemaVersion": 2,
            "results": [
                {
                    "questionId": "classify",
                    "type": "choice",
                    "answerability": {
                        "status": status,
                        "issues": issues,
                    },
                    "answer": {"optionId": option} if option else None,
                    "explanation": {
                        "summary": (
                            "The supplied message supports one current queue."
                            if option
                            else "The supplied message does not support one queue."
                        ),
                        "evidence": [{"sourceId": "message", "quote": quote}] if quote else [],
                        "contraryEvidence": contrary,
                        "missingFacts": missing,
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
