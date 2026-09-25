"""Trusted handlers of the conditional intake example.

Their contracts are declared in ``config/settings.yaml``; the host registers
only the callables. Routing is configuration: these handlers return data.
"""

import re

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject
from foliqant.ports.execution import StepContext

_REFERENCE = re.compile(r"A-[0-9]{3}")


async def check_reference(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    """Normalize an account reference and report whether it is well formed."""
    del context
    reference = inputs["reference"]
    if not isinstance(reference, str) or not reference.strip():
        return StepOutcome({"status": "missing", "account_reference": None})
    normalized = reference.strip().upper()
    status = "valid" if _REFERENCE.fullmatch(normalized) else "invalid"
    return StepOutcome({"status": status, "account_reference": normalized})


async def open_review(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    """Summarize why the request needs a person, from the facts that exist."""
    del context
    if inputs["plan"] == "Unknown":
        reason = "account_unknown"
    elif inputs["reference_status"] == "missing":
        reason = "reference_missing"
    elif inputs["reference_status"] == "invalid":
        reason = "reference_invalid"
    else:
        reason = "unclassified"
    return StepOutcome({"reason": reason})


HANDLERS = {
    "check_reference": HandlerRegistration(check_reference),
    "open_review": HandlerRegistration(open_review),
}
