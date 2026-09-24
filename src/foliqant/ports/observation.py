"""Content-free execution observations; implementations must not perform blocking I/O."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foliqant.core.errors import ErrorCode
from foliqant.core.execution import StepStatus

type ObservationValue = str | int | bool | tuple[str, ...]
"""Content-free attribute values: configured labels, counts and fixed codes."""


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Only the protected W3C carrier, never arbitrary business metadata."""

    traceparent: str | None = None
    tracestate: str | None = None


class Observation(Protocol):
    def finish(self, outcome: StepStatus, error: ErrorCode | None) -> None:
        """Record a fixed outcome without receiving exceptions or customer data."""
        ...

    def close(self) -> None:
        """End this scope and restore its caller's context in the same task."""
        ...

    def event(self, name: str, attributes: Mapping[str, ObservationValue]) -> None:
        """Record a fixed-name event with configured labels and codes only."""
        ...

    def carrier(self) -> Mapping[str, str]:
        """Return the W3C carrier of this scope, or an empty mapping."""
        ...


class ExecutionObserver(Protocol):
    def start(
        self,
        workflow: str,
        *,
        flow: str | None = None,
        step: str | None = None,
        trace: TraceContext | None = None,
        transport_trace: TraceContext | None = None,
        attributes: Mapping[str, ObservationValue] | None = None,
    ) -> Observation:
        """Start a scope using configured names and an optional ingress carrier.

        ``attributes`` carry content-free facts such as the attempt number.
        """
        ...
