"""The process logging sink must never interpret arbitrary record content."""

import asyncio
import gc
import io
import json
import logging
import threading
import time
import weakref
from collections.abc import Iterator

import pytest

from foliqant.adapters.telemetry.logging import (
    LogEvent,
    LogLabels,
    SafeJsonFormatter,
    configure_logging,
    emit_event,
)
from foliqant.core.errors import ErrorCode


class Hostile:
    def __str__(self) -> str:
        raise AssertionError("untrusted __str__ called")

    def __repr__(self) -> str:
        raise AssertionError("untrusted __repr__ called")


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    states = [(root, root.handlers[:], root.level, root.propagate, root.disabled)]
    states.extend(
        (item, item.handlers[:], item.level, item.propagate, item.disabled)
        for item in logging.Logger.manager.loggerDict.values()
        if isinstance(item, logging.Logger)
    )
    yield
    for logger, handlers, level, propagate, disabled in states:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


def test_external_content_is_not_read_or_rendered() -> None:
    record = logging.LogRecord(
        "PRIVATE_NAME",
        logging.ERROR,
        "PRIVATE_PATH",
        1,
        Hostile(),
        (),
        None,
    )
    record.args = (Hostile(),)
    record.exc_info = (RuntimeError, RuntimeError("SECRET_HTTP_TOKEN"), None)
    record.exc_text = "SECRET_SDK_TRACEBACK"
    record.stack_info = "SECRET_STACK"
    record.customer = {"identity": "SECRET_CUSTOMER", "nested": Hostile()}
    record._foliqant_fields = {"service": "allowed", "count": 1}
    rendered = SafeJsonFormatter(LogLabels(services=frozenset({"allowed"}))).format(record)
    assert json.loads(rendered) == {"event": "external_event", "level": "ERROR"}
    assert "SECRET" not in rendered
    assert "PRIVATE" not in rendered


def test_internal_event_only_emits_typed_allowed_fields() -> None:
    logger = logging.getLogger("test.safe.fields")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeJsonFormatter(LogLabels(services=frozenset({"service"}))))
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    emit_event(
        logger,
        LogEvent.STEP_COMPLETED,
        service="service",
        workflow="not_allowed",
        step="SECRET_STEP",
        duration_seconds=1.25,
        count=3,
        error_code=ErrorCode.REQUEST_TIMEOUT,
        trace_id="a" * 32,
        span_id="b" * 16,
    )
    assert json.loads(stream.getvalue()) == {
        "event": "step_completed",
        "level": "INFO",
        "service": "service",
        "duration_seconds": 1.25,
        "count": 3,
        "error_code": "request_timeout",
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
    }
    logger.handlers.clear()
    logger.propagate = True


@pytest.mark.parametrize("value", [Hostile(), True, -1, float("inf"), float("nan"), 10**100])
def test_invalid_numeric_fields_are_dropped(value: object) -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, LogEvent.RUN_COMPLETED, (), None)
    record._foliqant_fields = {"count": value, "duration_seconds": value}
    assert json.loads(SafeJsonFormatter().format(record)) == {
        "event": "run_completed",
        "level": "INFO",
    }


def test_unknown_fields_and_non_enum_error_codes_never_escape() -> None:
    record = logging.LogRecord("test", logging.WARNING, "", 0, LogEvent.RUN_FAILED, (), None)
    record._foliqant_fields = {
        "payload": {"nested": Hostile()},
        "error_code": "request_timeout",
        "trace_id": "0" * 32,
        "span_id": "B" * 16,
        "service": Hostile(),
    }
    assert json.loads(SafeJsonFormatter().format(record)) == {
        "event": "run_failed",
        "level": "WARNING",
    }


@pytest.mark.usefixtures("restore_logging")
@pytest.mark.parametrize("debug", [False, True])
def test_process_setup_filters_existing_sdk_handlers_and_uses_stderr(
    capsys: pytest.CaptureFixture[str],
    debug: bool,
) -> None:
    private_sink = io.StringIO()
    sdk = logging.getLogger("test.sdk")
    sdk.handlers = [logging.StreamHandler(private_sink)]
    sdk.propagate = False
    sdk.setLevel(logging.DEBUG)
    runtime = configure_logging(debug=debug)
    sdk.debug("SECRET_DEBUG", extra={"nested": Hostile()})
    sdk.error("SECRET_HTTP_EXCEPTION", exc_info=(RuntimeError, RuntimeError("SECRET_TOKEN"), None))
    assert runtime.close(timeout=1)
    output = capsys.readouterr()
    assert output.out == ""
    rows = [json.loads(line) for line in output.err.splitlines()]
    assert rows == ([{"event": "external_event", "level": "DEBUG"}] if debug else []) + [
        {"event": "external_event", "level": "ERROR"},
    ]
    assert private_sink.getvalue() == ""
    assert "SECRET" not in output.err


@pytest.mark.usefixtures("restore_logging")
def test_sink_failure_does_not_invoke_unsafe_logging_error_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BrokenStream(io.StringIO):
        def write(self, value: str) -> int:
            raise OSError("SECRET_SINK_EXCEPTION")

    monkeypatch.setattr("sys.stderr", BrokenStream())
    runtime = configure_logging()
    logging.getLogger("test.broken").error(Hostile())
    assert runtime.close(timeout=1)
    assert runtime.dropped_records == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("label", ["", "a\nsecret", "https://secret", "x" * 65])
def test_invalid_startup_labels_fail_safely(label: str) -> None:
    with pytest.raises(ValueError, match="invalid logging label"):
        LogLabels(services=frozenset({label}))


@pytest.mark.usefixtures("restore_logging")
def test_slow_sink_queue_overflow_and_bounded_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowStream(io.StringIO):
        def write(self, value: str) -> int:
            entered.set()
            assert release.wait(timeout=2)
            return super().write(value)

    sink = SlowStream()
    monkeypatch.setattr("sys.stderr", sink)
    runtime = configure_logging(queue_capacity=1)
    logger = logging.getLogger("test.slow")
    logger.info("SECRET_FIRST")
    assert entered.wait(timeout=1)
    started = time.monotonic()
    logger.info("SECRET_SECOND")
    logger.info("SECRET_DROPPED")
    assert time.monotonic() - started < 0.25
    assert runtime.dropped_records == 1
    started = time.monotonic()
    assert not runtime.close(timeout=0.01)
    assert time.monotonic() - started < 0.25
    try:
        assert "SECRET" not in sink.getvalue()
    finally:
        release.set()
        assert runtime.close(timeout=1)
    assert len(sink.getvalue().splitlines()) == 2
    assert "SECRET" not in sink.getvalue()


@pytest.mark.usefixtures("restore_logging")
def test_reconfigure_stops_old_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stderr", io.StringIO())
    first = configure_logging()
    second = configure_logging()
    assert first.close(timeout=1)
    assert second.close(timeout=1)


@pytest.mark.usefixtures("restore_logging")
async def test_blocked_writer_does_not_retain_record_or_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowStream(io.StringIO):
        def write(self, value: str) -> int:
            entered.set()
            assert release.wait(timeout=2)
            return super().write(value)

    monkeypatch.setattr("sys.stderr", SlowStream())
    runtime = configure_logging(queue_capacity=2)
    record = logging.LogRecord("SECRET_LOGGER", logging.ERROR, "", 0, Hostile(), (), None)
    record_ref = weakref.ref(record)
    try:
        logging.getLogger().handle(record)
        del record
        gc.collect()
        assert record_ref() is None
        assert await asyncio.to_thread(entered.wait, 1)
        beats = 0

        async def heartbeat() -> None:
            nonlocal beats
            for _ in range(3):
                await asyncio.sleep(0)
                beats += 1

        await asyncio.wait_for(heartbeat(), timeout=0.25)
        assert beats == 3
        assert not runtime.close(timeout=0)
    finally:
        release.set()
        assert await asyncio.to_thread(runtime.close, timeout=1)


@pytest.mark.parametrize("capacity", [0, -1, True, 65537])
def test_invalid_queue_capacity_rejected_before_thread_creation(capacity: int) -> None:
    with pytest.raises(ValueError, match="invalid logging configuration"):
        configure_logging(queue_capacity=capacity)


@pytest.mark.usefixtures("restore_logging")
def test_current_trace_correlates_internal_and_external_logs_without_content() -> None:
    from opentelemetry.sdk.trace import TracerProvider

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeJsonFormatter(LogLabels(flows=frozenset({"triage"}))))
    logger = logging.getLogger("test.trace.correlation")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    provider = TracerProvider(shutdown_on_exit=False)
    try:
        with provider.get_tracer("test").start_as_current_span("PRIVATE_SPAN") as span:
            emit_event(logger, LogEvent.STEP_COMPLETED, flow="triage")
            logger.error("PRIVATE_EXCEPTION", exc_info=RuntimeError("PRIVATE_TOKEN"))
            expected = span.get_span_context()
        emit_event(logger, LogEvent.RUN_COMPLETED)
        rows = [json.loads(row) for row in stream.getvalue().splitlines()]
        for row in rows[:2]:
            assert row["trace_id"] == f"{expected.trace_id:032x}"
            assert row["span_id"] == f"{expected.span_id:016x}"
        assert rows[0]["flow"] == "triage"
        assert "trace_id" not in rows[2]
        assert "PRIVATE" not in stream.getvalue()
    finally:
        provider.shutdown()
