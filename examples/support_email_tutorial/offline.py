"""Scripted model outputs prove wiring without calling a provider."""

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

ENVIRONMENT = {
    "MODEL_BASE_URL": "http://127.0.0.1:1/v1",
    "MODEL_ID": "offline-scripted-fixture",
}


def reply_for(reference: str) -> str:
    return f"We received your request for {reference}. A support agent will review it."


def scripted_response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    text = repr(messages)
    if info.function_tools:
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            tool_content = returns[-1].content
            record = json.loads(tool_content) if isinstance(tool_content, str) else tool_content
            if not isinstance(record, dict):
                raise ValueError("Synthetic account tool returned no record")
            reference = record.get("account_reference")
            if not isinstance(reference, str):
                raise ValueError("Synthetic account tool returned no reference")
            return ModelResponse(
                parts=[TextPart(json.dumps({"value": {"reply": reply_for(reference)}}))],
                usage=RequestUsage(input_tokens=10, output_tokens=5),
            )
        reference = "A-100" if "A-100" in text else "A-200" if "A-200" in text else None
        if reference is None:
            raise ValueError("No scripted tool call for this synthetic email")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "lookup_account", {"account_reference": reference}, tool_call_id="lookup"
                )
            ],
            usage=RequestUsage(input_tokens=10, output_tokens=5),
        )
    if '"id":"identify"' in text:
        second_account = (
            "A-200"
            if "Review invoice INV-7 for account A-100 and cancel renewal for account A-200."
            in text
            else None
        )
        second_category = "cancellation"
        second_description = "Cancel renewal."
        second_id = "cancellation_request"
        if "Review invoice INV-7 and invoice INV-8 for account A-100." in text:
            second_account = "A-100"
            second_category = "billing"
            second_description = "Review invoice INV-8."
            second_id = "second_billing_request"
        assessment_response = {
            "results": [
                {
                    "questionId": "identify",
                    "type": "request_units",
                    "answerability": {"status": "answerable", "issues": []},
                    "answer": {
                        "units": [
                            {
                                "id": "billing_request",
                                "status": "active",
                                "categoryId": "billing",
                                "subject": "A-100",
                                "description": "Review invoice INV-7.",
                            },
                            {
                                "id": second_id,
                                "status": "active",
                                "categoryId": second_category,
                                "subject": second_account,
                                "description": second_description,
                            },
                        ],
                        "relations": [],
                    },
                    "reason": "The email requests two independent support actions.",
                    "evidence_strength": "strong",
                }
            ]
        }
        return ModelResponse(
            parts=[TextPart(json.dumps(assessment_response))],
            usage=RequestUsage(input_tokens=10, output_tokens=5),
        )
    billing = (
        "Please review the duplicate charge on invoice INV-7 for account A-100." in text
        or "Please review invoice INV-7." in text
    )
    cancellation = "Please cancel renewal for account A-200." in text
    if '"id":"classify"' in text:
        option = (
            "billing"
            if billing and not cancellation
            else "cancellation"
            if cancellation and not billing
            else None
        )
        response_value: object = {
            "results": [
                {
                    "questionId": "classify",
                    "type": "choice",
                    "answerability": {
                        "status": "answerable" if option else "not_answerable",
                        "issues": []
                        if option
                        else [
                            "multiple_valid_options"
                            if billing and cancellation
                            else "no_supported_answer"
                        ],
                    },
                    "answer": {"optionId": option} if option else None,
                    "reason": "The email states the requested support action."
                    if option
                    else "One queue is not supported by the email.",
                    "evidence_strength": "strong",
                }
            ]
        }
    elif "Extract the active" in text:
        reference = "A-100" if "A-100" in text else "A-200" if "A-200" in text else None
        action = "review the duplicate charge" if billing else "cancel renewal"
        response_value = {"value": {"requested_action": action, "account_reference": reference}}
    elif "Write a short draft reply" in text:
        reference = "A-100" if "A-100" in text else "A-200" if "A-200" in text else "the account"
        response_value = {"value": {"reply": reply_for(reference)}}
    else:
        raise ValueError("No scripted response for this synthetic email")
    return ModelResponse(
        parts=[TextPart(json.dumps(response_value))],
        usage=RequestUsage(input_tokens=10, output_tokens=5),
    )


@asynccontextmanager
async def model_factory(
    profiles: ModelProfiles, *, environment: Mapping[str, str]
) -> AsyncIterator[Mapping[str, ModelBinding]]:
    del environment
    yield {
        alias: ModelBinding(
            model=FunctionModel(scripted_response),
            settings=ModelSettings(),
            admission=CapacityLimiter(concurrency=1, queue_limit=0),
            output_mode="native",
            supports_text=True,
            supports_json_schema=True,
            supports_tools=True,
        )
        for alias in profiles.models
    }
