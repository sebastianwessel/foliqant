"""Trusted policy: a lookup requires an explicit account reference."""

from collections.abc import Mapping

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, JsonValue, freeze_json
from foliqant.ports.execution import StepContext


def _schema(value: dict[str, JsonValue]) -> FrozenObject:
    frozen = freeze_json(value)
    assert isinstance(frozen, Mapping)
    return frozen


async def require_reference(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    reference = inputs["account_reference"]
    if not isinstance(reference, str) or not reference.strip():
        return StepOutcome({"account_reference": ""}, needs_review=True)
    return StepOutcome({"account_reference": reference})


async def assemble_reply(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    return StepOutcome(dict(inputs))


async def select_reply(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    selected = inputs["billing"] if inputs["billing"] is not None else inputs["cancellation"]
    assert isinstance(selected, Mapping)
    return StepOutcome(dict(selected))


HANDLERS = {
    "require_reference": HandlerRegistration(
        require_reference,
        _schema(
            {
                "type": "object",
                "properties": {"account_reference": {"type": ["string", "null"]}},
                "required": ["account_reference"],
                "additionalProperties": False,
            }
        ),
        _schema(
            {
                "type": "object",
                "properties": {"account_reference": {"type": "string"}},
                "required": ["account_reference"],
                "additionalProperties": False,
            }
        ),
    ),
    "assemble_reply": HandlerRegistration(
        assemble_reply,
        _schema(
            {
                "type": "object",
                "properties": {
                    "queue": {"type": "string", "enum": ["billing", "cancellation"]},
                    "account_reference": {"type": "string"},
                    "reply": {"type": "string"},
                },
                "required": ["queue", "account_reference", "reply"],
                "additionalProperties": False,
            }
        ),
        _schema(
            {
                "type": "object",
                "properties": {
                    "queue": {"type": "string", "enum": ["billing", "cancellation"]},
                    "account_reference": {"type": "string"},
                    "reply": {"type": "string"},
                },
                "required": ["queue", "account_reference", "reply"],
                "additionalProperties": False,
            }
        ),
    ),
    "select_reply": HandlerRegistration(
        select_reply,
        _schema(
            {
                "type": "object",
                "properties": {
                    "billing": {"type": ["object", "null"]},
                    "cancellation": {"type": ["object", "null"]},
                },
                "required": ["billing", "cancellation"],
                "additionalProperties": False,
            }
        ),
        _schema(
            {
                "type": "object",
                "properties": {
                    "queue": {"type": "string", "enum": ["billing", "cancellation"]},
                    "account_reference": {"type": "string"},
                    "reply": {"type": "string"},
                },
                "required": ["queue", "account_reference", "reply"],
                "additionalProperties": False,
            }
        ),
    ),
}
