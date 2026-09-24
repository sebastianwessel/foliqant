"""Bounded content-free JSON logging; no message interpolation or exception rendering."""

import json
import logging
import math
import queue
import re
import sys
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import TextIO, cast

from foliqant.core.errors import ErrorCode
from foliqant.core.execution import StepStatus

_MAX_COUNT = 2**53 - 1
_MAX_DURATION_SECONDS = 365 * 24 * 60 * 60
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_LEVELS = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}
_OUTCOMES = frozenset({"cancelled", "completed", "failed", "needs_review", "skipped"})
_EXECUTION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_LOCATION = re.compile(r"[a-z0-9_.]{1,200}\Z")
_ROUTE_KINDS = frozenset({"direct", "cases", "route", "review"})
_STOPS = frozenset({"until", "exhausted", "continue_when", "review", "failure"})
_ISSUES = frozenset({"no_supported_answer", "conflicting_information", "multiple_valid_options"})
_OPERATORS = frozenset(
    {"gt", "gte", "lt", "lte", "matches", "length", "equals", "not_equals", "in", "not_in"}
)
_TARGET_OUTCOMES = frozenset({"completed", "needs_review"})
_MISMATCHES = frozenset({"incompatible_type", "value_too_long"})


class LogEvent(StrEnum):
    """Fixed event names; never derive these from user or provider content."""

    SERVICE_STARTED = "service_started"
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    FLOW_STARTED = "flow_started"
    FLOW_COMPLETED = "flow_completed"
    FLOW_FAILED = "flow_failed"
    DEPENDENCY_REJECTED = "dependency_rejected"
    ROUTE_SELECTED = "route_selected"
    STEP_SKIPPED = "step_skipped"
    REPEAT_STOPPED = "repeat_stopped"
    HANDLER_REVIEW = "handler_review"
    CONDITION_TYPE_MISMATCH = "condition_type_mismatch"
    CONDITION_EVALUATED = "condition_evaluated"
    TELEMETRY_LABELS_DROPPED = "telemetry_labels_dropped"
    EXTERNAL_EVENT = "external_event"


@dataclass(frozen=True, slots=True)
class LogLabels:
    """Nonsecret startup names permitted in logs, never a request-derived allowlist."""

    services: frozenset[str] = frozenset()
    workflows: frozenset[str] = frozenset()
    steps: frozenset[str] = frozenset()
    flows: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for values in (self.services, self.workflows, self.steps, self.flows):
            if type(values) is not frozenset or len(values) > 1024:
                raise ValueError("invalid logging label allowlist")
            for value in values:
                if type(value) is not str or _LABEL.fullmatch(value) is None:
                    raise ValueError("invalid logging label")


_DEFAULT_LABELS = LogLabels()


class SafeJsonFormatter(logging.Formatter):
    """Inspect only fixed fields; arbitrary messages, extras and exceptions are ignored.

    Only bounded scalar fields from a fixed allowlist are emitted. No record is
    retained or modified. Unknown/third-party records become a generic event even
    at DEBUG severity. Flow targets must be configured flow labels or outcomes.
    """

    def __init__(self, labels: LogLabels = _DEFAULT_LABELS) -> None:
        super().__init__()
        self._labels = labels

    def format(self, record: logging.LogRecord) -> str:
        values = record.__dict__
        event = values.get("msg")
        level = values.get("levelno")
        output: dict[str, str | int | float] = {
            "event": event.value if isinstance(event, LogEvent) else LogEvent.EXTERNAL_EVENT.value,
            "level": _LEVELS.get(level, "INFO") if type(level) is int else "INFO",
        }
        fields = values.get("_foliqant_fields")
        if (
            isinstance(event, LogEvent)
            and event is not LogEvent.EXTERNAL_EVENT
            and type(fields) is dict
        ):
            self._add_fields(output, cast(dict[str, object], fields))
        # Resolve synchronously in the emitting task, before a queue worker loses
        # its context. The current SDK context overrides manually supplied IDs.
        # The API is optional for hosts that install no telemetry extra.
        try:
            from opentelemetry.trace import get_current_span

            context = get_current_span().get_span_context()
            if context.is_valid:
                output["trace_id"] = f"{context.trace_id:032x}"
                output["span_id"] = f"{context.span_id:016x}"
        except Exception:
            pass
        return json.dumps(output, ensure_ascii=True, allow_nan=False, separators=(",", ":"))

    def _add_fields(self, output: dict[str, str | int | float], fields: dict[str, object]) -> None:
        for key, allowed in (
            ("service", self._labels.services),
            ("workflow", self._labels.workflows),
            ("step", self._labels.steps),
            ("flow", self._labels.flows),
        ):
            label = fields.get(key)
            if type(label) is str and len(label) <= 64 and label in allowed:
                output[key] = label
        count = fields.get("count")
        if type(count) is int and 0 <= count <= _MAX_COUNT:
            output["count"] = count
        duration = fields.get("duration_seconds")
        if type(duration) in (int, float):
            number = cast(int | float, duration)
            if 0 <= number <= _MAX_DURATION_SECONDS and math.isfinite(number):
                output["duration_seconds"] = number
        error = fields.get("error_code")
        if isinstance(error, ErrorCode):
            output["error_code"] = error.value
        outcome = fields.get("outcome")
        if type(outcome) is str and outcome in _OUTCOMES:
            output["outcome"] = outcome
        attempt = fields.get("attempt")
        if type(attempt) is int and 1 <= attempt <= 64:
            output["attempt"] = attempt
        execution = fields.get("execution_id")
        if type(execution) is str and _EXECUTION_ID.fullmatch(execution):
            output["execution_id"] = execution
        for key, allowed_values in (
            ("route_kind", _ROUTE_KINDS),
            ("stopped_by", _STOPS),
            ("operator", _OPERATORS),
            ("reason", _MISMATCHES),
        ):
            value = fields.get(key)
            if type(value) is str and value in allowed_values:
                output[key] = value
        target = fields.get("target")
        if type(target) is str and (target in self._labels.flows or target in _TARGET_OUTCOMES):
            output["target"] = target
        location = fields.get("location")
        if type(location) is str and _LOCATION.fullmatch(location):
            output["location"] = location
        issues = fields.get("issues")
        if (
            isinstance(issues, tuple)
            and issues
            and all(type(issue) is str and issue in _ISSUES for issue in issues)
        ):
            output["issues"] = ",".join(cast(tuple[str, ...], issues))
        for key, length in (("trace_id", 32), ("span_id", 16)):
            value = fields.get(key)
            if (
                type(value) is str
                and len(value) == length
                and all(char in "0123456789abcdef" for char in value)
                and value != "0" * length
            ):
                output[key] = value


class LoggingRuntime:
    """Own a bounded queue of safe strings and the stderr writer lifecycle.

    Queue overflow, sink failure and emission after close drop events, incrementing
    dropped_records. No log record, exception, payload or caller object is queued.
    A blocked OS sink cannot be killed: close returns False at its deadline and
    its daemon writer can finish once the sink recovers. Bootstrap must check it.
    """

    def __init__(self, stream: TextIO, *, queue_capacity: int) -> None:
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 65536:
            raise ValueError("invalid logging queue capacity")
        self._queue: queue.Queue[str] = queue.Queue(maxsize=queue_capacity)
        self._stream = stream
        self._state_lock = threading.Lock()
        self._closing = threading.Event()
        self._dropped = 0
        self._writer = threading.Thread(target=self._write, name="foliqant-log-writer", daemon=True)
        self._writer.start()

    @property
    def dropped_records(self) -> int:
        """Number dropped by overflow, closed intake or failed sink writes."""
        with self._state_lock:
            return self._dropped

    def _enqueue(self, line: str) -> None:
        with self._state_lock:
            if self._closing.is_set():
                self._dropped += 1
                return
            try:
                self._queue.put_nowait(line)
            except queue.Full:
                self._dropped += 1

    def _write(self) -> None:
        while not self._closing.is_set() or not self._queue.empty():
            try:
                line = self._queue.get(timeout=0.025)
            except queue.Empty:
                continue
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
            except Exception:
                # Never call logging.handleError: it renders original records.
                with self._state_lock:
                    self._dropped += 1
            finally:
                self._queue.task_done()

    def close(self, *, timeout: float = 1.0) -> bool:
        """Stop intake and attempt a bounded drain; False means writer still active.

        Call during bootstrap shutdown outside the event-loop thread (or with
        timeout=0 followed by a bounded executor await). Repeated close is safe.
        """
        if type(timeout) not in (int, float) or not 0 <= timeout <= 30:
            raise ValueError("invalid logging shutdown timeout")
        with self._state_lock:
            self._closing.set()
        self._writer.join(timeout=timeout)
        return not self._writer.is_alive()


class _SafeQueueHandler(logging.Handler):
    def __init__(self, formatter: SafeJsonFormatter, runtime: LoggingRuntime) -> None:
        super().__init__()
        self.setFormatter(formatter)
        self.runtime = runtime

    def emit(self, record: logging.LogRecord) -> None:
        # Sanitize synchronously BEFORE queueing; do not use QueueHandler.prepare,
        # which interpolates raw messages and may format exception content.
        try:
            self.runtime._enqueue(self.format(record))
        except Exception:
            with self.runtime._state_lock:
                self.runtime._dropped += 1

    def close(self) -> None:
        self.runtime.close(timeout=0)
        super().close()


def emit_event(
    logger: logging.Logger,
    event: LogEvent,
    *,
    level: int = logging.INFO,
    service: str | None = None,
    workflow: str | None = None,
    step: str | None = None,
    flow: str | None = None,
    duration_seconds: float | None = None,
    count: int | None = None,
    error_code: ErrorCode | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    outcome: StepStatus | None = None,
    attempt: int | None = None,
    execution_id: str | None = None,
    route_kind: str | None = None,
    target: str | None = None,
    stopped_by: str | None = None,
    operator: str | None = None,
    location: str | None = None,
    issues: tuple[str, ...] | None = None,
    reason: str | None = None,
) -> None:
    """Emit a typed event; the sink enforces field validity and startup labels."""
    if not isinstance(event, LogEvent) or type(level) is not int or level not in _LEVELS:
        raise ValueError("invalid logging event or level")
    logger.log(
        level,
        event,
        extra={
            "_foliqant_fields": {
                "service": service,
                "workflow": workflow,
                "step": step,
                "flow": flow,
                "duration_seconds": duration_seconds,
                "count": count,
                "error_code": error_code,
                "trace_id": trace_id,
                "span_id": span_id,
                "outcome": outcome,
                "attempt": attempt,
                "execution_id": execution_id,
                "route_kind": route_kind,
                "target": target,
                "stopped_by": stopped_by,
                "operator": operator,
                "location": location,
                "issues": issues,
                "reason": reason,
            }
        },
    )


def configure_logging(
    *,
    debug: bool = False,
    labels: LogLabels = _DEFAULT_LABELS,
    queue_capacity: int = 1024,
) -> LoggingRuntime:
    """Install the bounded process-wide stderr sink before accepting work.

    Replaces existing root/named handlers, including SDK handlers. Call after SDK
    setup; integrations must not install further handlers afterward. Embedded
    hosts may instead use SafeJsonFormatter on their own controlled sinks.
    The caller owns the returned runtime and must close it at shutdown. No network
    I/O occurs and slow stderr never blocks producers; full queues drop events.
    """
    if (
        type(debug) is not bool
        or type(labels) is not LogLabels
        or type(queue_capacity) is not int
        or not 1 <= queue_capacity <= 65536
    ):
        raise ValueError("invalid logging configuration")
    runtime = LoggingRuntime(sys.stderr, queue_capacity=queue_capacity)
    handler = _SafeQueueHandler(SafeJsonFormatter(labels), runtime)
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    root = logging.getLogger()
    for previous in root.handlers:
        if isinstance(previous, _SafeQueueHandler):
            previous.close()
    root.handlers[:] = [handler]
    root.filters.clear()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.disabled = False
    for item in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(item, logging.Logger):
            for previous in item.handlers:
                if isinstance(previous, _SafeQueueHandler):
                    previous.close()
            item.handlers.clear()
            item.filters.clear()
            item.propagate = True
            item.disabled = False
            item.setLevel(logging.NOTSET)
    return runtime
