"""Explicit-provider tool attempt spans independent of SDK global installation."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import Token

from opentelemetry import context as context_api
from opentelemetry import trace as trace_api
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, TracerProvider
from opentelemetry.util.types import AttributeValue

from foliqant.core.errors import ErrorCode, ServiceError

from .privacy import TelemetryLabels


class ToolTelemetry:
    """Trace actual attempts, never tool inputs, outputs, identity or exception text."""

    def __init__(self, tracer_provider: TracerProvider, *, labels: TelemetryLabels) -> None:
        self._tracer = tracer_provider.get_tracer("foliqant.tool")
        self._labels = labels

    @contextmanager
    def observe(self, tool: str, *, attempt: int) -> Iterator[None]:
        """Keep attempt context active through send and typed response validation."""
        span: Span | None = None
        token: Token[context_api.Context] | None = None
        attributes: dict[str, AttributeValue] = {"gen_ai.operation.name": "execute_tool"}
        if tool in self._labels.tools:
            attributes["gen_ai.tool.name"] = tool
        if type(attempt) is int and 1 <= attempt <= 65536:
            attributes["foliqant.request.attempt"] = attempt
        try:
            span = self._tracer.start_span("tool", kind=SpanKind.CLIENT, attributes=attributes)
            token = context_api.attach(trace_api.set_span_in_context(span))
        except Exception:
            pass
        try:
            yield
        except BaseException as error:
            code = (
                error.code
                if isinstance(error, ServiceError)
                else ErrorCode.CANCELLED
                if isinstance(error, asyncio.CancelledError)
                else ErrorCode.TIMEOUT
                if isinstance(error, TimeoutError)
                else ErrorCode.DEPENDENCY_FAILURE
            )
            if span is not None:
                try:
                    span.set_attribute("error.type", code.value)
                    span.set_status(Status(StatusCode.ERROR))
                except Exception:
                    pass
            raise
        finally:
            try:
                if token is not None:
                    context_api.detach(token)
            except Exception:
                pass
            finally:
                if span is not None:
                    try:
                        span.end()
                    except Exception:
                        pass
