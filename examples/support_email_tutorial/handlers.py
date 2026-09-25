"""Trusted policy: a lookup requires an explicit account reference.

The contract of `require_reference` is declared in `config/settings.yaml`;
the host registers only the callable.
"""

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject
from foliqant.ports.execution import StepContext


async def require_reference(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    """Stop the branch for review unless extraction found a usable reference."""
    del context
    reference = inputs["account_reference"]
    if not isinstance(reference, str) or not reference.strip():
        return StepOutcome(
            {"account_reference": ""},
            needs_review=True,
            unresolved_issues=("no_supported_answer",),
        )
    return StepOutcome({"account_reference": reference})


HANDLERS = {"require_reference": HandlerRegistration(require_reference)}
