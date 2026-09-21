"""Example-only HTTP wrapper around the in-memory pipeline; no authentication."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from foliqant.bootstrap import WorkflowApplication, open_application, prepare_application
from foliqant.contracts.decoding import MAX_ENVELOPE_BYTES, decode_envelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity

_CONFIG = Path(__file__).with_name("foliqant.yaml")


def create_app() -> Starlette:
    """Construct a small local demonstration, not a production ingress platform."""
    prepared = prepare_application(_CONFIG)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with open_application(prepared, environment={}) as pipeline:
            app.state.pipeline = pipeline
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
            pipeline = cast(WorkflowApplication, request.app.state.pipeline)
            # This unauthenticated demo establishes no tenant or principal.
            # A real host supplies its own caller context outside the pipeline.
            result = await pipeline.run("hello", envelope, identity=Identity())
            return JSONResponse(result.model_dump(mode="json"))
        except ServiceError as error:
            status = 400 if error.code in {ErrorCode.INVALID_INPUT, ErrorCode.FORBIDDEN} else 503
            return JSONResponse(
                {"error": {"code": error.code.value, "message": str(error)}}, status_code=status
            )
        except TimeoutError:
            return JSONResponse({"error": "Request timed out"}, status_code=408)

    return Starlette(routes=[Route("/run", run, methods=["POST"])], lifespan=lifespan)


def main() -> None:
    import uvicorn

    from foliqant.adapters.telemetry.logging import configure_logging

    logs = configure_logging()
    try:
        uvicorn.run(
            create_app(),
            host="127.0.0.1",
            port=8765,
            access_log=False,
            log_config=None,
            proxy_headers=False,
        )
    finally:
        logs.close()


if __name__ == "__main__":
    main()
