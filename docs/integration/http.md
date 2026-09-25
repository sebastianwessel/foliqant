# Expose an HTTP endpoint

Foliqant provides an in-process async API; the HTTP server belongs to your
application. The repository's [Starlette wrapper](https://github.com/sebastianwessel/foliqant/blob/main/examples/http_workflow/server.py)
is a complete, small example around the
[support triage workflow](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_triage/README.md).
It has one `POST /run` route and binds only to `127.0.0.1`.

## Try the example

From the repository root, install its explicit dependencies and configure the
local model as described in the [example README](https://github.com/sebastianwessel/foliqant/blob/main/examples/http_workflow/README.md):

```sh
uv sync --locked --extra openai --group http-example
PYDANTIC_AI_NO_BANNER=1 \
  uv run --no-sync python -m examples.http_workflow.server --live
```

The `--live` flag intentionally opens the configured model client. In another
terminal, send the complete envelope:

```sh
curl --fail-with-body http://127.0.0.1:8765/run \
  -H 'Content-Type: application/json' \
  -d '{"payload":{"requestId":"http-001","message":"Cancel renewal for account C-1049 by 30 September 2026."},"metadata":{}}'
```

The example returns the serialized `ExecutionResult`. A completed or
`needs_review` result is HTTP 200. A failed run is a server error with the
result as its body: `503` for a failure the boundary marked `retryable` or
`capacity_exceeded`, `504` for `request_timeout` and `run_timeout`, and `500`
for every other failure, including model and tool failures. Invalid input is
`400`. It has no authentication and is intended for loopback use.

## Adapt the boundary

Keep two lifetimes separate: clients live as long as the server; each request
awaits one workflow execution. The example opens its configured runner in
Starlette's lifespan:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette

from examples.support_triage.run import open_configured


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    async with open_configured() as run_support:
        app.state.run_support = run_support
        yield
```

The `open_configured` helper prepares the support configuration, registers its
handlers, opens the application, and yields a function bound to `support_triage`.
Use your own workflow and handler registrations when adapting it. Inside the
route, **after reading and validating the request**, the integration is just:

```python
result = await request.app.state.run_support(envelope)
failure = result.execution.error
if result.execution.status in {"failed", "cancelled"} and failure is not None:
    status = failure_status(failure.code, retryable=failure.retryable)
    return JSONResponse(result.model_dump(mode="json"), status_code=status)
return JSONResponse(result.model_dump(mode="json"))
```

`failure_status` is the example's mapping from a failure's code and
`retryable` flag to the HTTP status described above; a failed run is never a
2xx response.

These are fragments of the linked runnable server, not a second server you
need to create. Register its route with `Route("/run", run, methods=["POST"])`
and pass the lifespan to `Starlette`. Start that ASGI application with your
server process; never call `asyncio.run` from an async request handler.

The example's `lifespan` opens `open_configured()` once and stores the runner on
the app. Its route reads the request stream with a five-second read deadline and
a one-MiB cap before calling `decode_envelope`. That decoder rejects malformed
UTF-8, duplicate JSON keys, non-finite numbers, excessive nesting, and invalid
envelopes with a safe `invalid_input` error. The route checks `Content-Type` and
requires the support workflow's object payload.

For a remotely reachable host, authenticate the caller *before* constructing
trusted `Identity` and authorizing the workflow call. Do not trust
`tenant_id` or `principal_id` claims from the body as proof of identity. The
loopback example rejects those claims entirely. A host with trusted identity can
call `app.run("support_triage", envelope, identity=identity)`; matching metadata
is accepted and missing identity fields are filled from the trusted value.
Foliqant checks consistency, while the host performs authentication and
authorization.

Preserve the full result when deciding your response. Its top-level `payload`
is the workflow's configured projection; `flows` records local steps and
`execution.status` says whether the invocation completed, needs review, or
failed. A `needs_review` result is valid business output. A `failed` result
contains a safe `execution.error` and often completed step records, which the
host may store under its own privacy policy. Admission errors can raise
`ServiceError` before any result exists; see [Handle errors](errors.md).

The example is a transport illustration. Your host must choose HTTP status
mapping, authentication, rate limits, idempotency, response storage, and any
durable queue. Foliqant does not supply these services. For process lifetime
and shutdown, see [Deploy and operate](deployment.md).
