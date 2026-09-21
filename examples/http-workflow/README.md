# Small HTTP example

The pipeline itself has no server, authentication, job queue or database. This
example adds one local HTTP handler: read an envelope, await the pipeline, return
the result. The configured finish step returns the original payload without
calling a model. Every request is independent and nothing is persisted.

From the repository root:

```sh
uv sync --project service --locked --group http-example
uv run --project service --no-sync python examples/http-workflow/server.py
```

In a second terminal:

```sh
curl --fail-with-body http://127.0.0.1:8765/run \
  -H 'Content-Type: application/json' \
  -d '{"payload":{"message":"Hallo"},"metadata":{"reference":"example"}}'
```

The response contains the payload, metadata, step results and execution status.
It completes within the request. There is no job ID lookup, retry queue, result
store or background execution. Ctrl+C stops the example.

The handler is deliberately unauthenticated and binds only to loopback. It
supplies an empty identity context, so the body cannot establish a tenant or
principal. Your application decides how caller context is obtained; that is
outside the pipeline. The example bounds body size and read time, and returns
safe errors without exposing raw exceptions.

Starlette and Uvicorn belong to the `http-example` dependency group, not production
pipeline dependencies. Use a different transport by calling the same
`open_application(...).run(...)` API from your own adapter.

You can also run the same input without HTTP:

```sh
uv run --project service --no-sync foliqant run \
  --config examples/http-workflow/foliqant.yaml \
  --workflow hello --input examples/http-workflow/envelope.json
```
