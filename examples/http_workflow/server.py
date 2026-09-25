"""Thin HTTP wrapper around the support-triage business workflow."""

import argparse
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import cast

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from examples.support_triage.run import open_configured
from foliqant import Envelope
from foliqant.contracts.decoding import MAX_ENVELOPE_BYTES, decode_envelope
from foliqant.contracts.execution import ExecutionResult
from foliqant.core.errors import TIMEOUT_CODES, ErrorCode, ServiceError

type SupportRun = Callable[[Envelope], Awaitable[ExecutionResult]]
type RunContext = Callable[[], AbstractAsyncContextManager[SupportRun]]


def failure_status(code: ErrorCode, *, retryable: bool) -> int:
    """This example's HTTP status of a failure; a failed run is never a 2xx response.

    Invalid or unauthorized requests are client errors. A failure the boundary
    marked retryable (capacity, rate limit, overload) is 503, a timeout 504;
    every other failure of the run, including model and tool failures, is 500.
    """
    if code is ErrorCode.INVALID_INPUT:
        return 400
    if code is ErrorCode.FORBIDDEN:
        return 403
    if code in TIMEOUT_CODES:
        return 504
    if retryable or code is ErrorCode.CAPACITY_EXCEEDED:
        return 503
    return 500


def create_app(run_context: RunContext = open_configured) -> Starlette:
    """Wrap an injected support runner; the default uses explicit local Qwen settings."""

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with run_context() as run_support:
            app.state.run_support = run_support
            yield

    async def run(request: Request) -> JSONResponse:
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return JSONResponse({"error": "Expected application/json"}, status_code=415)
        try:
            body = bytearray()
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_ENVELOPE_BYTES:
                        return JSONResponse({"error": "Request too large"}, status_code=413)
                    body.extend(chunk)
            envelope = decode_envelope(bytes(body))
            if (
                envelope.metadata.tenant_id is not None
                or envelope.metadata.principal_id is not None
            ):
                raise ServiceError(ErrorCode.FORBIDDEN)
            if not isinstance(envelope.payload, dict):
                raise ServiceError(ErrorCode.INVALID_INPUT)
            run_support = cast(SupportRun, request.app.state.run_support)
            result = await run_support(envelope)
            failure = result.execution.error
            if result.execution.status in {"failed", "cancelled"} and failure is not None:
                # A technical failure is a server error, not a business result.
                status = failure_status(failure.code, retryable=failure.retryable)
                return JSONResponse(result.model_dump(mode="json"), status_code=status)
            return JSONResponse(result.model_dump(mode="json"))
        except ServiceError as error:
            status = failure_status(error.code, retryable=error.retryable)
            if error.code is ErrorCode.FORBIDDEN:
                status = 400  # Identity claims in the body are malformed input here.
            return JSONResponse(
                {"error": {"code": error.code.value, "message": str(error)}}, status_code=status
            )
        except TimeoutError:
            return JSONResponse({"error": "Request timed out"}, status_code=408)

    return Starlette(routes=[Route("/run", run, methods=["POST"])], lifespan=lifespan)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="call the exact local model configured in the repository .env",
    )
    arguments = parser.parse_args()
    if not arguments.live:
        parser.print_help()
        return 0

    import uvicorn

    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=8765,
        access_log=False,
        log_config=None,
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
