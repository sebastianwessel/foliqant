"""Content-free execution observations; implementations must not perform blocking I/O."""

from dataclasses import dataclass
from typing import Protocol

from foliqant.core.errors import ErrorCode
from foliqant.core.execution import RunStatus


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Only the protected W3C carrier, never arbitrary business metadata."""

    traceparent: str | None = None
    tracestate: str | None = None


class Observation(Protocol):
    def finish(self, outcome: RunStatus, error: ErrorCode | None) -> None:
        """Record a fixed outcome without receiving exceptions or customer data."""
        ...

    def close(self) -> None:
        """End this scope and restore its caller's context in the same task."""
        ...


class ExecutionObserver(Protocol):
    def start(
        self,
        workflow: str,
        *,
        step: str | None = None,
        trace: TraceContext | None = None,
        transport_trace: TraceContext | None = None,
    ) -> Observation:
        """Start a scope using configured names and an optional ingress carrier."""
        ...
