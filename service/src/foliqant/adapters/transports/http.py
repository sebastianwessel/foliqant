"""Authenticated synchronous Starlette boundary for workflow execution."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from foliqant.contracts.decoding import decode_envelope
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.contracts.execution import ExecutionResult
from foliqant.contracts.http import DeploymentMode, HttpConfig, validate_http_mode
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity
from foliqant.ports.auth import AuthenticatedCaller, Authenticator
from foliqant.ports.observation import TraceContext

if TYPE_CHECKING:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import Lifespan


class WorkflowApplication(Protocol):
    """Minimal composition-root surface consumed by the HTTP transport."""

    @property
    def workflow_names(self) -> tuple[str, ...]: ...

    @property
    def ready(self) -> bool: ...

    async def run(
        self,
        workflow: str,
        envelope: Envelope,
        *,
        identity: Identity,
        transport_trace: TraceContext | None = None,
    ) -> ExecutionResult: ...


_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_CONFIGURATION: 503,
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.INVALID_OUTPUT: 502,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.MISSING_BINDING: 422,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.BUDGET_EXHAUSTED: 422,
    ErrorCode.DEPENDENCY_FAILURE: 503,
    ErrorCode.CONFLICT: 409,
    ErrorCode.UNCERTAIN_EFFECT: 503,
    ErrorCode.CANCELLED: 503,
    ErrorCode.CAPACITY_EXCEEDED: 429,
}

_TITLE_BY_CODE: dict[ErrorCode, str] = {
    ErrorCode.INVALID_CONFIGURATION: "Invalid configuration",
    ErrorCode.INVALID_INPUT: "Invalid input",
    ErrorCode.INVALID_OUTPUT: "Invalid output",
    ErrorCode.UNAUTHENTICATED: "Authentication required",
    ErrorCode.FORBIDDEN: "Forbidden",
    ErrorCode.NOT_FOUND: "Not found",
    ErrorCode.MISSING_BINDING: "Missing binding",
    ErrorCode.TIMEOUT: "Timeout",
    ErrorCode.BUDGET_EXHAUSTED: "Budget exhausted",
    ErrorCode.DEPENDENCY_FAILURE: "Dependency unavailable",
    ErrorCode.CONFLICT: "Conflict",
    ErrorCode.UNCERTAIN_EFFECT: "Uncertain effect",
    ErrorCode.CANCELLED: "Cancelled",
    ErrorCode.CAPACITY_EXCEEDED: "Capacity exceeded",
}


@dataclass(frozen=True, slots=True)
class _HttpFailure(Exception):
    code: ErrorCode
    status: int | None = None
    retryable: bool = False


def _header_values(request: Request, name: str) -> tuple[str, ...]:
    encoded_name = name.lower().encode("ascii")
    return tuple(
        value.decode("latin-1")
        for key, value in request.scope.get("headers", ())
        if key.lower() == encoded_name
    )


def _single_header(request: Request, name: str, *, max_length: int | None = None) -> str | None:
    values = _header_values(request, name)
    if len(values) > 1:
        raise _HttpFailure(ErrorCode.INVALID_INPUT, status=400)
    if not values:
        return None
    value = values[0]
    if max_length is not None and len(value) > max_length:
        raise _HttpFailure(ErrorCode.INVALID_INPUT, status=400)
    return value


def _authorization(request: Request) -> str | None:
    return _single_header(request, "authorization", max_length=8192)


def _validate_content_headers(request: Request, max_body_bytes: int) -> None:
    content_type = _single_header(request, "content-type", max_length=256)
    if content_type is None or content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise _HttpFailure(ErrorCode.INVALID_INPUT, status=400)

    content_length = _single_header(request, "content-length", max_length=32)
    if content_length is None:
        return
    if not content_length.isascii() or not content_length.isdecimal():
        raise _HttpFailure(ErrorCode.INVALID_INPUT, status=400)
    if int(content_length) > max_body_bytes:
        raise _HttpFailure(ErrorCode.INVALID_INPUT, status=413)


def _transport_trace(request: Request) -> TraceContext | None:
    traceparent = _single_header(request, "traceparent", max_length=512)
    tracestate = _single_header(request, "tracestate", max_length=512)
    if traceparent is None and tracestate is None:
        return None
    return TraceContext(traceparent=traceparent, tracestate=tracestate)


async def _read_body(request: Request, config: HttpConfig) -> bytes:
    body = bytearray()
    try:
        async with asyncio.timeout(config.body_timeout):
            async for chunk in request.stream():
                if len(body) + len(chunk) > config.max_body_bytes:
                    raise _HttpFailure(ErrorCode.INVALID_INPUT, status=413)
                body.extend(chunk)
    except TimeoutError:
        raise _HttpFailure(ErrorCode.TIMEOUT, status=504, retryable=True) from None
    except _HttpFailure:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as error:
        # Starlette's ClientDisconnect is optional with the HTTP extra. Avoid
        # importing or exposing it here: an interrupted receive cancels work.
        if error.__class__.__name__ == "ClientDisconnect":
            raise asyncio.CancelledError from None
        raise _HttpFailure(ErrorCode.INVALID_INPUT, status=400) from None
    return bytes(body)


async def _wait_for_disconnect(request: Request) -> None:
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


async def _run_until_disconnect(
    request: Request, operation: Coroutine[Any, Any, ExecutionResult]
) -> ExecutionResult:
    run_task: asyncio.Task[ExecutionResult] = asyncio.create_task(operation)
    disconnect_task = asyncio.create_task(_wait_for_disconnect(request))
    try:
        done, _ = await asyncio.wait(
            (run_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED
        )
        if run_task in done:
            disconnect_task.cancel()
            await asyncio.gather(disconnect_task, return_exceptions=True)
            return await run_task
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        raise asyncio.CancelledError
    except BaseException:
        run_task.cancel()
        disconnect_task.cancel()
        await asyncio.gather(run_task, disconnect_task, return_exceptions=True)
        raise


def _problem_content(
    code: ErrorCode,
    *,
    status: int,
    retryable: bool,
    result: ExecutionResult | None = None,
) -> dict[str, Any]:
    content: dict[str, Any] = {
        "type": f"urn:foliqant:error:{code.value}",
        "title": _TITLE_BY_CODE[code],
        "status": status,
        "detail": str(ServiceError(code)),
        "code": code.value,
        "retryable": retryable,
    }
    if result is not None:
        content["result"] = result.model_dump(mode="json")
    return content


def create_http_app(
    application: WorkflowApplication,
    authenticator: Authenticator,
    *,
    config: HttpConfig,
    mode: DeploymentMode = "production",
    lifespan: Lifespan[Starlette] | None = None,
) -> Starlette:
    """Create a synchronous workflow API with an optional owned ASGI lifespan."""

    try:
        from starlette.applications import Starlette
        from starlette.exceptions import HTTPException
        from starlette.responses import JSONResponse
        from starlette.routing import Route
    except ImportError:
        raise RuntimeError("the 'http' service extra is required for the HTTP adapter") from None

    validate_http_mode(config, mode)

    def problem_response(
        code: ErrorCode,
        *,
        status: int | None = None,
        retryable: bool = False,
        result: ExecutionResult | None = None,
    ) -> Response:
        response_status = status if status is not None else _STATUS_BY_CODE[code]
        headers: dict[str, str] = {}
        if response_status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        if response_status == 429:
            headers["Retry-After"] = "1"
        return JSONResponse(
            _problem_content(code, status=response_status, retryable=retryable, result=result),
            status_code=response_status,
            headers=headers,
            media_type="application/problem+json",
        )

    async def run_workflow(request: Request) -> Response:
        try:
            authorization = _authorization(request)
            try:
                caller: AuthenticatedCaller = await authenticator.authenticate(authorization)
            except ServiceError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception:
                raise ServiceError(ErrorCode.DEPENDENCY_FAILURE, retryable=True) from None

            workflow = request.path_params["name"]
            if workflow not in caller.workflows:
                raise ServiceError(ErrorCode.FORBIDDEN)
            if workflow not in application.workflow_names:
                raise ServiceError(ErrorCode.NOT_FOUND)

            _validate_content_headers(request, config.max_body_bytes)
            transport_trace = _transport_trace(request)
            body = await _read_body(request, config)
            envelope = decode_envelope(body, max_bytes=config.max_body_bytes)
            # The composition root repeats this check when it creates the
            # immutable accepted envelope. Keeping it here prevents body claims
            # from reaching any workflow-level admission or validation work.
            accept_envelope(envelope, caller.identity)
            result = await _run_until_disconnect(
                request,
                application.run(
                    workflow,
                    envelope,
                    identity=caller.identity,
                    transport_trace=transport_trace,
                ),
            )
            if not isinstance(result, ExecutionResult):
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            if result.execution.status in {"failed", "cancelled"}:
                error = result.execution.error
                if error is None:
                    raise ServiceError(ErrorCode.INVALID_OUTPUT)
                return problem_response(
                    error.code,
                    retryable=error.retryable,
                    result=result,
                )
            return JSONResponse(result.model_dump(mode="json"), status_code=200)
        except _HttpFailure as error:
            return problem_response(error.code, status=error.status, retryable=error.retryable)
        except ServiceError as error:
            return problem_response(error.code, retryable=error.retryable)
        except TimeoutError:
            return problem_response(ErrorCode.TIMEOUT, retryable=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            return problem_response(ErrorCode.DEPENDENCY_FAILURE, retryable=True)

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def ready(_: Request) -> Response:
        try:
            is_ready = application.ready is True
        except Exception:
            is_ready = False
        if is_ready:
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "unavailable"}, status_code=503)

    async def http_exception(_: Request, error: Exception) -> Response:
        status = error.status_code if isinstance(error, HTTPException) else 404
        if status == 404:
            return problem_response(ErrorCode.NOT_FOUND)
        return problem_response(ErrorCode.INVALID_INPUT, status=400)

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/ready", ready, methods=["GET"]),
            Route("/workflows/{name}/runs", run_workflow, methods=["POST"]),
        ],
        exception_handlers={HTTPException: http_exception},
        lifespan=lifespan,
    )
