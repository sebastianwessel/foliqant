"""Public API for the Foliqant in-memory workflow runtime."""

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .bootstrap import (
        PreparedApplication,
        RuntimePlugins,
        WorkflowApplication,
        load_environment,
        open_application,
        prepare_application,
    )
    from .contracts.envelope import Envelope
    from .contracts.execution import ExecutionResult
    from .core.identity import Identity

__all__ = [
    "Envelope",
    "ExecutionResult",
    "Identity",
    "PreparedApplication",
    "RuntimePlugins",
    "WorkflowApplication",
    "load_environment",
    "open_application",
    "prepare_application",
]


def __getattr__(name: str) -> object:
    """Load public runtime types only when callers request them."""

    if name in {
        "PreparedApplication",
        "RuntimePlugins",
        "WorkflowApplication",
        "load_environment",
        "open_application",
        "prepare_application",
    }:
        from . import bootstrap

        return cast(object, getattr(bootstrap, name))
    if name == "Envelope":
        from .contracts.envelope import Envelope

        return Envelope
    if name == "ExecutionResult":
        from .contracts.execution import ExecutionResult

        return ExecutionResult
    if name == "Identity":
        from .core.identity import Identity

        return Identity
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
