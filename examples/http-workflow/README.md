# Thin HTTP wrapper

This optional example exposes the same
[support-triage workflow](../support_triage/README.md) through one loopback HTTP
route. The wrapper reads a strict envelope, awaits the in-memory workflow, and
returns its result. It adds no business steps of its own.

Install the local-model and HTTP dependencies, then start it explicitly:

```sh
uv sync --locked --extra openai --group http-example
PYDANTIC_AI_NO_BANNER=1 \
  uv run --no-sync python examples/http-workflow/server.py --live
```

The default runner uses the exact Qwen model ID, endpoint, low reasoning,
temperature `0.1`, token limit, and timeout from the root `.env`, as described in
the support example. There is no model discovery. Omitting `--live` prints help
and performs no call.

In a second terminal:

```sh
curl --fail-with-body http://127.0.0.1:8765/run \
  -H 'Content-Type: application/json' \
  -d '{"payload":{"requestId":"http-001","message":"Cancel renewal for account C-1049 by 30 September 2026."},"metadata":{}}'
```

The handler is deliberately unauthenticated and binds only to loopback. It
rejects tenant or principal claims in the request body; a production host must
authenticate the caller and establish trusted identity before calling Foliqant.
The example bounds request size and read time and returns safe errors.

HTTP remains application code. The package provides no server, route, database,
job ID, retry queue, or result store. Tests inject an offline support runner into
the same wrapper, so the default test suite never contacts a model endpoint.
