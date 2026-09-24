"""Trusted read-only handlers for the deterministic routing tutorial.

Their contracts (input/output schemas and effect) are declared in
``config/settings.yaml``; the host registers only the callables.
"""

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject
from foliqant.ports.execution import StepContext


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
    "prepare_billing": HandlerRegistration(prepare_billing),
    "prepare_cancellation": HandlerRegistration(prepare_cancellation),
}
