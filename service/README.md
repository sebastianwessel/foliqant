# In-memory workflow pipeline

Foliqant accepts an envelope, runs configured steps in memory, and returns a
result. PydanticAI handles model conversations; deterministic rules choose the
next step. The package does not provide application authentication, a server,
a job queue, database storage, restart recovery or background execution.

Use [the embedded example](../examples/embedded-workflow/README.md),
[the model example](../examples/inbox/README.md), or
[the MCP example](../examples/mcp-tools/README.md). A
[small HTTP example](../examples/http-workflow/README.md) wraps the same API;
HTTP is example code, not part of the pipeline package.

## Setup

The service has its own uv project and environment, separate from model tooling.
From the repository root:

```sh
uv sync --project service --locked --all-extras --group dev
uv run --project service --no-sync python -m pytest service/tests
uv run --project service --no-sync mypy --config-file service/pyproject.toml service/src
uv run --project service --no-sync ruff check --config service/pyproject.toml service/src service/tests
uv run --project service --no-sync python service/scripts/generate_schemas.py --check
```

Production uses `uv sync --project service --locked --no-dev` with only the
selected provider/MCP/telemetry extras, for example `--extra openai --extra mcp`.
Training packages, test tooling and example HTTP servers are not runtime
dependencies. The shared local `foliqant-decisions` package supplies the same
native contracts used by model tooling.

## Run a pipeline

Compile a configuration once and own the model/tool client lifespan:

```python
import asyncio
from pathlib import Path

from foliqant.bootstrap import open_application, prepare_application
from foliqant.contracts.envelope import Envelope

async def main() -> None:
    prepared = prepare_application(Path("examples/http-workflow/foliqant.yaml"))
    async with open_application(prepared, environment={}) as pipeline:
        result = await pipeline.run(
            "hello", Envelope(payload={"message": "Hallo"})
        )
        print(result.model_dump_json())

asyncio.run(main())
```

`result` contains payload, metadata, decisions and execution status/usage. A run
returns its final result; no accepted-job receipt or status lookup exists. A
business hold is `needs_review`; technical failures have fixed safe error codes.
Cancellation propagates to the caller. Process shutdown does not preserve runs.

Version 1 configuration maps workflow names to directories below its own folder:

```yaml
version: 1
workflows:
  hello: workflows/hello
models: {}
mcp: {}
```

Optional `execution` settings bound concurrent work, time and model/tool attempts;
`telemetry` enables configured safe observations. Model and MCP profiles are
explained below. Unknown settings, duplicate keys, unsafe YAML and escaping file
paths fail validation. Credentials use environment variable references, not YAML
interpolation. `load_environment(config_path, os.environ)` reads the configuration
folder's `.env` once; process values take precedence.

The CLI provides `init`, `validate`, `explain`, `doctor` and `run`. Validation and
inspection are offline; `doctor` checks installed extras without contacting any
endpoint. `run` reads a JSON envelope from a regular file or stdin, awaits the
pipeline, and prints its result. No `serve`, `worker` or migration command exists.

```sh
uv run --project service --no-sync foliqant run \
  --config examples/http-workflow/foliqant.yaml \
  --workflow hello --input examples/http-workflow/envelope.json
```

## Metadata, async execution and extension points

`tenant_id` and `principal_id` are independently optional context fields. The
pipeline validates and propagates them; it does not authenticate anyone.
`pipeline.run(...)` uses the envelope's validated metadata by default. An explicit
`identity=Identity(...)` from the host checks matching claims and enriches missing
fields. The embedding application owns any authentication needed before calling
this API. Do not describe caller-provided metadata as verified credentials.

Protected `metadata.telemetry` contains only W3C `traceparent` and `tracestate`.
A valid explicit transport carrier can take precedence without modifying the
original metadata. Identity, arbitrary business data and baggage never enter
telemetry. Each concurrent call has isolated immutable context, results and budgets.

Supported step types are native `decision`, `llm`, read-only `mcp`, registered
async `handler`, and `finish`. Schemas and graph routing compile offline. Bind data
with JSON pointers such as `/payload/text` and `/steps/classify/result`; missing
and null are different. The model cannot invent a new route.

Use `HandlerRegistration` through `prepare_application(..., handlers=...)` for
async Python functions. Inputs and outputs are schema-validated. Use
`RuntimePlugins` to provide model factories, MCP credential hooks or a tool
permission policy. These are direct Python extension points; configuration does
not import arbitrary executable modules. A tool allowlist controls external MCP
operations, not application login. Mutation/reconciliation is outside this scope.

The lower-level `WorkflowRunner` accepts a compiled plan, executor, schema
validator, admission limiter and optional observer. Its core is independent of
provider SDKs and inbound transport choices. Prefer `open_application` unless
your embedding host needs to own this composition itself.

All execution I/O is async. Concurrent-call limits do not create background jobs;
the caller continues awaiting its own result. Attempt reservations precede I/O,
failures consume attempts, and unavailable token usage stays unknown. Client
lifetimes outlive their active operations. Configure real operation timeouts;
Python cannot forcibly stop a custom callback that ignores cancellation.

## PydanticAI model execution

`ModelProfiles` validates named deployment profiles. Select a provider, explicit
model ID and `output_mode` (`native` or `tool`). This chooses how structured
results are requested; it does not authorize external tools. The service does
not discover model names or reserve aliases such as `primary`.

```python
from foliqant.adapters.models import ModelExecutor
from foliqant.adapters.models.providers import open_model_bindings
from foliqant.contracts.models import ModelProfiles

profiles = ModelProfiles.model_validate({
    "models": {
        "deciding": {
            "provider": "openai_compatible",
            "model": "your-configured-model-id",
            "base_url": "http://127.0.0.1:1234/v1",
            "allow_insecure_http": True,
            "output_mode": "native",
            "concurrency": 1,
            "options": {"max_tokens": 2048, "temperature": 0.1},
        }
    }
}, strict=True)

# Within an async application, with WorkflowSchemas(plan) already constructed:
async with open_model_bindings(profiles, environment={}) as bindings:
    executor = ModelExecutor(bindings, schemas)
    # Inject executor into WorkflowRunner and await runner.run(...).
```

For authenticated providers, use `api_key_env` to reference an environment key
and pass the resolved environment snapshot to the factory. Never embed secrets
in profiles. An absent key reference on a compatible profile means deliberately
unauthenticated access. Official OpenAI, Azure OpenAI (versioned or v1) and
Anthropic clients use explicit API selection, zero SDK retries and bounded
timeouts. Client cleanup also runs after partial initialization failure. Unsafe
ambient SDK header/account overrides are rejected instead of silently changing
configured requests. Bedrock, Azure managed identity and broader Foundry APIs are not supported by
these adapters. Offline protocol tests do not qualify a live deployment.

PydanticAI owns the model conversation. The executor independently validates native
decision semantics and source evidence before routing. A schema-output LLM step
uses a provider object containing `value`, then exposes only the unwrapped,
validated value to the workflow. Local schema references are fully inlined from
the frozen plan, preserving sibling constraints as intersections. Dynamic references and SDK-unsupported recursive output schemas fail
before inference; input validation retains its separate supported schema scope.
Authored LLM schemas use non-strict provider output mode so SDK conversion cannot
close dictionaries or erase authored constraints. Providers must accept that mode;
Anthropic authored schema steps require `tool` mode, while canonical native
decisions also support Anthropic `native` mode. Unsupported combinations fail
local validation before consuming an attempt; there is no automatic mode switch.
The host always validates the original output schema.

See the [model-enabled inbox example](../examples/inbox/README.md) for a runnable
application with Markdown decision steps, profiles and safe logging.

Requests consume a budget only after admission, immediately before I/O. Each
request has an operation timeout within the original run deadline. Failed
requests count, missing token measurements stay null, and truncation/refusal is
an invalid output rather than a successful partial decision. Automatic output
repairs are currently disabled. Tool-bearing LLM steps require an explicitly
injected host MCP runtime and a tool-capable model profile. Model instrumentation
is connected when the deployment includes telemetry; no telemetry profile means
no model observation adapter or exporter.

## MCP tools and authentication

The [local MCP example](../examples/mcp-tools/README.md) runs a real stdio server
and a compiled workflow without model inference.

Install the `mcp` extra. Define `McpProfiles` with a declared catalog, then build
one `McpClientSessionFactory` and `McpRuntime` for the application. The factory
creates a fresh SDK session for each caller; it never shares authenticated clients.
Pass the runtime to `McpExecutor` for explicit MCP steps, or as
`ModelExecutor(..., tools=runtime)` for model-selected tool calls.

```python
from foliqant.adapters.mcp.runtime import McpExecutor, McpRuntime
from foliqant.adapters.mcp.transport import McpClientSessionFactory
from foliqant.contracts.mcp import McpProfiles

profiles = McpProfiles.model_validate({
    "servers": {"records": {
        "transport": {
            "type": "streamable_http",
            "endpoint": "https://tools.example.com/mcp",
        },
        "identity_meta_key": "example.com/identity",
        "catalog": {"tools": {"lookup": {
            "input_schema": {
                "type": "object",
                "properties": {"reference": {"type": "string"}},
                "required": ["reference"],
            },
            "effect": "read",
        }}},
    }},
})
# authorizer implements async authorize(server, tool, arguments, context).
# It checks current business permissions; the catalog alone is not user authorization.
factory = McpClientSessionFactory(profiles, credential_providers={})
runtime = McpRuntime(profiles, factory, authorizer)
executor = McpExecutor(runtime)
```

Use the server's exact input/output schemas in the reviewed catalog. Discovery
checks every declared tool and its schema before use; extra discovered tools do
not gain permission. Arguments are validated before authorization or budget
reservation. Structured results require a matching declared output schema; text
results remain strings, with multiple text blocks joined by a newline. Unsupported
content blocks and oversized results fail safely. SDK protocol timeouts map to
`timeout`, and each started tool call stays charged even if it fails.

Each step allowlists tool names. A required or named tool must successfully return
a validated result to the model before the workflow accepts its answer. After
that call, the model may finish normally. The service performs no hidden tool
retries, automatic sampling or elicitation rounds. Input-required responses end
in `needs_review`; interactive continuation is not yet implemented. Write tools
are outside this read-only pipeline scope.

For MCP HTTP authentication, set `auth` to a host-registered credential-provider ID.
The hook receives the current trusted identity, server and context. The provided
`SdkOAuthCredentialProvider` uses the MCP SDK's OAuth discovery, PKCE/state,
resource binding and refresh handling. Supply a storage factory partitioned by
**every** `McpCredentialScope` field and protected at rest; no plaintext token
store is supplied. Explicitly allow the HTTPS authorization-server origins.
Discovery, registration and refresh cannot contact other origins.

Interactive callbacks belong to the embedding application’s explicit tool-login flow. Normal
workflow providers omit them and never launch a browser. Tokens never belong in
request bodies, metadata, workflow files or model context. The profile's optional
domain-qualified identity key forwards only present trusted IDs in MCP `_meta`;
it does not authenticate the user. A host `trace_carrier` callback can supply
only W3C `traceparent`/`tracestate` for discovery and calls. When telemetry is
configured, bootstrap also supplies protected W3C propagation to MCP and installs
the owned global provider for SDK spans.

Stdio profiles accept only trusted deployment commands and arguments. The SDK
inherits its fixed safe environment baseline plus the explicit `env` overlay;
the service does not copy the complete environment or mutate it around async
calls. Child stderr is discarded so it cannot expose uncontrolled diagnostics.
Session cleanup remains in its owning task with a ten-second deadline. Caller
baggage is removed task-locally while preserving trace context, then restored
after session exit.

## Async execution rules

Use native async clients for model and MCP I/O. Do not call a
synchronous SDK directly from an async handler. For a blocking-only integration,
`adapters.execution.blocking.BlockingExecutor` owns a bounded worker pool, copies
task-local context and rejects excess work before creating an internal task.
Configure the SDK's own network timeout as well as the caller's deadline.

Cancelling or timing out a queued operation prevents it from starting. A started
blocking operation keeps its capacity until the worker actually finishes, even
after its caller stops waiting. This prevents abandoned requests from exceeding
the configured limit. It does not prove whether a remote side effect happened;
mutation recovery must reconcile that outcome before retrying.

At shutdown, stop intake and call `await executor.aclose(timeout=...)`. A false
result means a worker remains active. Python cannot forcibly stop that thread,
and it may delay interpreter exit. Native async adapters still need bounded
cancellation and their own client cleanup.

Safe logging queues only sanitized JSON strings, with bounded capacity. Slow
stderr does not block the event loop; overflow is counted in `dropped_records`.
The bootstrap owns the returned logging runtime and drains it outside the event
loop using `await asyncio.to_thread(runtime.close, timeout=...)`, checking the
boolean outcome. These are local execution limits, not a job queue or a distributed rate limiter.

## Safe OpenTelemetry

Install the optional `telemetry` extra. `TelemetryRuntime` owns separate trace and
metric providers; it creates no exporter when a signal endpoint is missing or
empty. It never discovers a collector or uses ambient OTLP endpoint/header
settings. Configure explicit OTLP/HTTP signal URLs, including `/v1/traces` or
`/v1/metrics`; HTTP needs `allow_insecure_http=True` for local development.

```python
from foliqant.adapters.telemetry.observation import WorkflowTelemetry
from foliqant.adapters.telemetry.privacy import TelemetryLabels
from foliqant.adapters.telemetry.runtime import TelemetryRuntime
from foliqant.contracts.telemetry import TelemetryConfig

labels = TelemetryLabels(
    services=frozenset({"workflow_service"}),
    workflows=frozenset({"inbox"}),
    steps=frozenset({"classify", "done"}),
    models=frozenset({"your-configured-model-id"}),
    providers=frozenset({"openai"}),
)
telemetry = TelemetryRuntime.build(
    TelemetryConfig(service_name="workflow_service"),
    labels=labels,
    environment={},
)
observer = WorkflowTelemetry(
    telemetry.tracer_provider, labels=labels, meter_provider=telemetry.meter_provider
)
# Pass observer=observer when constructing WorkflowRunner.
# At application shutdown: clean = await telemetry.aclose()
```

Add `ModelTelemetry(telemetry.tracer_provider, telemetry.meter_provider, labels)`
from `foliqant.adapters.telemetry.models` as `ModelExecutor(..., telemetry=...)`
to observe actual model requests. Only one SDK inference span is emitted per
request. Host metrics count available input/output tokens once; missing usage is
not recorded as zero and component subsets are not added to totals. The upstream
SDK omits explicit zero counts from span attributes; use the host metrics, not
absence of a span attribute, to distinguish measured zero from unavailable usage.

For the MCP SDK, call `telemetry.install_global()` once during process startup,
before creating sessions. It explicitly owns global tracing and W3C propagation;
it refuses to replace an already configured host provider. Embedded applications
can pass the providers directly for workflow/model tracing without installing
globals, but must separately arrange safe host-owned tracing for MCP SDK spans.
Pass `trace_carrier` from `foliqant.adapters.telemetry.observation` to
`McpRuntime(..., trace_carrier=trace_carrier)` for protected W3C metadata. The SDK
creates its client-operation span and stamps the current context on each request.
Neither the observer nor MCP forwards baggage or uses trace fields as identity.

Labels must be reviewed nonsecret configuration, never values collected from
requests. Prompts, results, identity values, exception text, tool schemas and
arbitrary SDK attributes/events are removed before export queueing. Tracestate
may propagate over W3C transport but is removed from exported span contexts.
Metric labels are filtered before aggregation; raw SDK metrics and exemplars are
disabled. Workflow/step durations use seconds and fixed outcomes/error codes.

Configure JSON logging before SDK startup, including in debug mode. Header
credentials use references such as `traces_headers_env={"Authorization":
"OTLP_AUTHORIZATION"}` with an explicit environment snapshot; never put their
values in configuration. Conflicting ambient TLS/client-credential SDK settings
are rejected rather than silently applied. Export failures are nonfatal;
`startup_failures` reports signals that could not initialize, and `aclose()`
returns false if bounded shutdown could not finish. OTel exposes no timeout
argument on its public batch shutdown API: one owned daemon cleanup thread can
continue after this caller wait expires (the SDK batch wait is up to 30 seconds).
Repeated close calls wait on that same worker rather than starting more threads. Observe these safe health
signals without changing a completed business result. Do not keep the event loop
open awaiting an unbounded exporter flush.

The [embedded example](../examples/embedded-workflow/README.md) includes a
`--telemetry` option and makes no model requests.
