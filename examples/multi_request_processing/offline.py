"""Authored model fixtures; real local MCP calls remain in the execution path."""

import json
import re
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

# These fixture responses are deliberately separate from the evaluation gold.
MESSAGES: dict[str, tuple[list[tuple[str, str, str | None, str]], str]] = {
    "Check FOI-2026-0142 and explain how to apply.": (
        [
            ("status", "request_status", "FOI-2026-0142", "active"),
            ("guide", "guidance", None, "active"),
        ],
        "answerable",
    ),
    "Bitte FOI-2026-0310 prüfen und das Antragsverfahren erklären.": (
        [
            ("status", "request_status", "FOI-2026-0310", "active"),
            ("guide", "guidance", None, "active"),
        ],
        "answerable",
    ),
    "Check FOI-2026-0142 and FOI-2026-0310.": (
        [
            ("first", "request_status", "FOI-2026-0142", "active"),
            ("second", "request_status", "FOI-2026-0310", "active"),
        ],
        "answerable",
    ),
    "No action needed.": ([], "answerable"),
    "Keine Aktion erforderlich.": ([], "answerable"),
    "Please help.": ([], "not_answerable"),
    "Bitte helfen.": ([], "not_answerable"),
    "Check my request and explain how to apply.": (
        [("status", "request_status", None, "active"), ("guide", "guidance", None, "active")],
        "answerable",
    ),
    "I withdraw my FOI-2026-0142 lookup. Explain how to apply.": (
        [
            ("status", "request_status", "FOI-2026-0142", "withdrawn"),
            ("guide", "guidance", None, "active"),
        ],
        "answerable",
    ),
}


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
            content = returns[-1].content
            value = json.loads(content) if isinstance(content, str) else content
            return ModelResponse(
                parts=[TextPart(json.dumps({"value": value}))],
                usage=RequestUsage(input_tokens=20, output_tokens=10),
            )
        # Selected child input, never the original email, is passed to this loop.
        reference = re.search(r"FOI-[0-9]{4}-[0-9]{4}", text)
        assert reference is not None
        language = "de" if '"de"' in text else "en"
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "lookup_request",
                    {"reference": reference.group(), "language": language},
                    tool_call_id="lookup",
                )
            ],
            usage=RequestUsage(input_tokens=10, output_tokens=5),
        )
    for message, (units, status) in MESSAGES.items():
        if message in text or json.dumps(message, ensure_ascii=False)[1:-1] in text:
            german = message.startswith(("Bitte", "Keine"))
            reason = (
                (
                    "Die Nachricht legt die Aufträge oder den Verzicht auf Aktionen fest."
                    if german
                    else "The message establishes the requested work or no action."
                )
                if status == "answerable"
                else (
                    "Die gewünschte Aktion ist nicht angegeben."
                    if german
                    else "The requested action is not specified."
                )
            )
            answer = (
                {
                    "units": [
                        {
                            "id": id,
                            "status": state,
                            "categoryId": category,
                            "subject": subject,
                            "description": "Angeforderte Auskunft oder Anleitung."
                            if german
                            else "Requested public-office assistance.",
                        }
                        for id, category, subject, state in units
                    ],
                    "relations": [],
                }
                if status == "answerable"
                else None
            )
            return ModelResponse(
                parts=[
                    TextPart(
                        json.dumps(
                            {
                                "results": [
                                    {
                                        "questionId": "identify",
                                        "type": "request_units",
                                        "answerability": {
                                            "status": status,
                                            "issues": []
                                            if status == "answerable"
                                            else ["no_supported_answer"],
                                        },
                                        "answer": answer,
                                        "reason": reason,
                                        "evidence_strength": "strong",
                                    }
                                ]
                            }
                        )
                    )
                ],
                usage=RequestUsage(input_tokens=10, output_tokens=5),
            )
    raise ValueError("No scripted response for this synthetic message")


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
