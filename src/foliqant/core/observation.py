"""Keep optional observation failures outside business execution semantics."""

import asyncio
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType

from foliqant.ports.observation import (
    ExecutionObserver,
    Observation,
    ObservationValue,
    TraceContext,
)

from .errors import ErrorCode, ServiceError
from .execution import StepStatus
from .json import FrozenObject

_TRACEPARENT = re.compile(r"[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}\Z")


@dataclass(slots=True)
class Outcome:
    """Mutable scope result plus safe access to optional events and trace ids."""

    status: StepStatus = "completed"
    error: ErrorCode | None = None
    _observation: Observation | None = None

    def event(self, name: str, **attributes: ObservationValue) -> None:
        """Record an event; a broken observer never affects business execution."""
        if self._observation is None:
            return
        try:
            self._observation.event(name, MappingProxyType(dict(attributes)))
        except Exception:
            pass

    def carrier(self) -> Mapping[str, str]:
        """Return this scope's W3C carrier, or an empty mapping."""
        if self._observation is None:
            return MappingProxyType({})
        try:
            carrier = self._observation.carrier()
            if isinstance(carrier, Mapping) and all(
                type(key) is str and type(value) is str and len(value) <= 512
                for key, value in carrier.items()
            ):
                return MappingProxyType(
                    {
                        key: value
                        for key, value in carrier.items()
                        if key in {"traceparent", "tracestate"}
                    }
                )
        except Exception:
            pass
        return MappingProxyType({})

    def trace_ids(self) -> tuple[str, str] | None:
        """Return ``(trace_id, span_id)`` of a valid scope carrier."""
        match = _TRACEPARENT.fullmatch(self.carrier().get("traceparent", ""))
        if match is None or set(match[1]) == {"0"} or set(match[2]) == {"0"}:
            return None
        return match[1], match[2]


def incoming_trace(metadata: FrozenObject) -> TraceContext | None:
    """Select bounded transport fields; validity is owned by the W3C propagator."""
    value = metadata.get("telemetry")
    if not isinstance(value, Mapping):
        return None

    def field(name: str) -> str | None:
        item = value.get(name)
        return item if isinstance(item, str) and len(item) <= 512 else None

    return TraceContext(field("traceparent"), field("tracestate"))


@contextmanager
def observe(
    observer: ExecutionObserver | None,
    workflow: str,
    *,
    flow: str | None = None,
    step: str | None = None,
    trace: TraceContext | None = None,
    transport_trace: TraceContext | None = None,
    attributes: Mapping[str, ObservationValue] | None = None,
) -> Iterator[Outcome]:
    """Report safe outcomes without letting a broken observer mask business work."""
    observation: Observation | None = None
    outcome = Outcome()
    if observer is not None:
        try:
            if attributes:
                observation = observer.start(
                    workflow,
                    flow=flow,
                    step=step,
                    trace=trace,
                    transport_trace=transport_trace,
                    attributes=MappingProxyType(dict(attributes)),
                )
            else:
                observation = observer.start(
                    workflow, flow=flow, step=step, trace=trace, transport_trace=transport_trace
                )
        except Exception:
            pass
        outcome._observation = observation
    try:
        yield outcome
    except asyncio.CancelledError:
        outcome.status, outcome.error = "cancelled", ErrorCode.CANCELLED
        raise
    except BaseException as error:
        outcome.status = "failed"
        outcome.error = (
            error.code
            if isinstance(error, ServiceError)
            else ErrorCode.TIMEOUT
            if isinstance(error, TimeoutError)
            else ErrorCode.DEPENDENCY_FAILURE
        )
        raise
    finally:
        if observation is not None:
            try:
                observation.finish(outcome.status, outcome.error)
            except Exception:
                pass
            finally:
                try:
                    observation.close()
                except Exception:
                    pass
