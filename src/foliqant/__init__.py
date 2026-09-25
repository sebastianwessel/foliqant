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
    from .contracts.execution import ExecutionInfo, ExecutionResult, ModelUsage, Usage
    from .core.identity import Identity
    from .graph import WorkflowGraph, explain

__all__ = [
    "Envelope",
    "ExecutionInfo",
    "ExecutionResult",
    "Identity",
    "ModelUsage",
    "PreparedApplication",
    "RuntimePlugins",
    "Usage",
    "WorkflowApplication",
    "WorkflowGraph",
    "explain",
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
    if name in {"ExecutionInfo", "ExecutionResult", "ModelUsage", "Usage"}:
        from .contracts import execution

        return cast(object, getattr(execution, name))
    if name == "Identity":
        from .core.identity import Identity

        return Identity
    if name in {"explain", "WorkflowGraph"}:
        from . import graph

        return cast(object, getattr(graph, name))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
