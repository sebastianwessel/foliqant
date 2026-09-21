# Thin HTTP wrapper

This optional example exposes the same
[support-triage workflow](../support_triage/README.md) through one loopback HTTP
route. The wrapper reads a strict envelope, passes its payload and permitted
metadata to the in-memory workflow, and returns its result. It adds no business
steps of its own.

Install the local-model and HTTP dependencies, then start it explicitly:

```sh
uv sync --locked --extra openai --group http-example
PYDANTIC_AI_NO_BANNER=1 \
  uv run --no-sync python -m examples.http_workflow.server --live
```

The default runner reuses the support example’s deployment. Model ID and
endpoint come from the root `.env`; reasoning, temperature, token limit and
timeouts are configured in its `foliqant.yaml`. There is no model discovery. Omitting `--live` prints help
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
Other validated envelope metadata is preserved in the result. The example bounds
request size and read time and returns safe errors.

HTTP remains application code. The package provides no server, route, database,
job ID, retry queue, or result store. Tests inject an offline support runner into
the same wrapper, so the default test suite never contacts a model endpoint.

## Evaluate through HTTP

```sh
uv run --no-sync python -m examples.http_workflow.evaluate
```

The default sends the support example’s same golden cases through an in-process
ASGI client and the real workflow with scripted model responses. No port or model
connection is opened. Transport rejection checks are in `tests/test_http_example.py`.
Use `--live` to measure the configured local Qwen model through this boundary.
The command exits nonzero on failed expectations. Per-step evaluations remain in
`examples.support_triage.evaluate`, avoiding duplicated business cases.

Each evaluation saves a new private report by default and prints its path. Use
`--output` to choose another new path, or `--repeat 3` to make three independent
attempts per authored case. Repetition measures variation over the same synthetic
gold; it does not add case coverage or establish model quality.

Export the shared pipeline gold or save a full private report:

```sh
uv run --no-sync python -m examples.http_workflow.evaluate \
  --write-dataset .foliqant/evaluation/http-support-triage-r8.json
uv run --no-sync python -m examples.http_workflow.evaluate \
  --output .foliqant/evaluation/http-support-report.json
```

Dataset export performs no inference. Report output contains inputs, gold and
returned results; console output omits those details while retaining metrics and
safe failure reasons. Queue and execution-status confusion matrices use explicit
label catalogs. Export paths must be new. Keep generated files under ignored
`.foliqant/` or outside the checkout; add `--live` only for an intended model run.
