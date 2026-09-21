# Embedded workflow example

Prepare the locked base environment once, then run the deterministic offline
example from the repository root:

```bash
uv sync --project service --locked --no-dev
uv run --project service --no-sync python examples/embedded-workflow/run.py
```

The script compiles the workflow, validates its envelope payload, binds a trusted
identity supplied directly by the demo host, follows deterministic handler
`next`/`on_unresolved` routing, and prints the strict execution result as JSON.
The identity values demonstrate protected metadata; no authentication occurs.
The executor performs no inference or network access. `WorkflowRunner` is an
embedded, nondurable runner; this example makes no production or durability claim.

## Optional telemetry

Install only the additional telemetry dependencies and run the same workflow:

```bash
uv sync --project service --locked --no-dev --extra telemetry
uv run --project service --no-sync python examples/embedded-workflow/run.py --telemetry
```

Without endpoint variables this creates no exporter and sends no network traffic.
The result on stdout stays unchanged. The demo owns safe JSON logging and the
process-global OTel tracer; it shuts both down within their configured bounds.
To send to an already running local OTLP/HTTP collector, configure signals
explicitly (omit either endpoint to disable that signal):

```bash
FOLIQANT_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318/v1/traces \
FOLIQANT_OTLP_METRICS_ENDPOINT=http://127.0.0.1:4318/v1/metrics \
FOLIQANT_OTLP_ALLOW_INSECURE_HTTP=true \
  uv run --project service --no-sync python examples/embedded-workflow/run.py --telemetry
```

The collector receives workflow/step spans and duration metrics with approved
configuration names, without request content or identity. No collector is started
or downloaded by this command. This example is process bootstrap, not a helper
for replacing telemetry already owned by an embedding application. See the
[service guide](../../service/README.md#safe-opentelemetry) for model/MCP wiring
and production header references.
