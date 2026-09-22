"""Keep optional observation failures outside business execution semantics."""

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from foliqant.ports.observation import ExecutionObserver, Observation, TraceContext

from .errors import ErrorCode, ServiceError
from .execution import RunStatus
from .json import FrozenObject


@dataclass(slots=True)
class Outcome:
    status: RunStatus = "completed"
    error: ErrorCode | None = None


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
) -> Iterator[Outcome]:
    """Report safe outcomes without letting a broken observer mask business work."""
    observation: Observation | None = None
    outcome = Outcome()
    if observer is not None:
        try:
            observation = observer.start(
                workflow, flow=flow, step=step, trace=trace, transport_trace=transport_trace
            )
        except Exception:
            pass
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
