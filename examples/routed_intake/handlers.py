"""Trusted read-only handlers for the deterministic routing tutorial."""

from collections.abc import Mapping

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, JsonValue, freeze_json
from foliqant.ports.execution import StepContext

INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "queue": {"type": "string", "enum": ["billing", "cancellation"]},
        "action": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": ["queue", "action", "message"],
    "additionalProperties": False,
}


def _schema(value: dict[str, JsonValue]) -> FrozenObject:
    result = freeze_json(value)
    assert isinstance(result, Mapping)
    return result


async def prepare_billing(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    """Prepare a billing handoff without another model request."""
    del context
    return StepOutcome(
        {
            "queue": "billing",
            "action": "request_invoice_review",
            "message": inputs["message"],
        }
    )


async def prepare_cancellation(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    """Prepare a cancellation handoff without another model request."""
    del context
    return StepOutcome(
        {
            "queue": "cancellation",
            "action": "prepare_cancellation",
            "message": inputs["message"],
        }
    )


HANDLERS = {
    "prepare_billing": HandlerRegistration(
        prepare_billing, _schema(INPUT_SCHEMA), _schema(OUTPUT_SCHEMA)
    ),
    "prepare_cancellation": HandlerRegistration(
        prepare_cancellation, _schema(INPUT_SCHEMA), _schema(OUTPUT_SCHEMA)
    ),
}
