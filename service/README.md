# Workflow service

This is the independent Python package for Foliqant workflow execution. Its
implementation contract is [specification 11](../specs/11-workflow-service.md).
PydanticAI owns model/tool conversations; the service owns deterministic routing,
identity, authorization, admission, persistence and transports.

Implemented foundations include strict envelopes, protected metadata, immutable
core values, bounded admission and blocking execution, the offline compiler,
embedded runner, model execution, read-only MCP tools, safe OTel observations,
an executable CLI/bootstrap, and authenticated synchronous HTTP ingress. The
current runtime is nondurable: durable jobs, retrieval/cancellation endpoints,
worker/broker recovery, child workflows, and reconciled writes remain future
work. A separate [PostgreSQL storage adapter](STORAGE.md) provides acceptance,
fenced leases, checkpoints, persisted budgets and a transactional result outbox;
it is not yet connected to the runner. See the [implementation status](../plans/workflow-service-status.md) for
verified scope.

## Development environment

Run from this directory:

```sh
uv sync --locked --all-extras --group dev
uv run --no-sync python -m pytest tests
uv run --no-sync mypy src
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync python scripts/generate_schemas.py --check
```

The local shared decision-contract package is resolved through `tool.uv.sources`.
Keep its source directory alongside this project when building from the repository.
Production dependency installation uses `uv sync --locked --no-dev` with only
the selected adapter extras, for example `--extra openai --extra http`. This is
dependency separation; it does not add durable execution or qualify a provider
deployment. The root model-tooling environment is separate.

## Boundaries

`core/` uses standard-library immutable values and async admission. `contracts/`
validates external representations with Pydantic. Provider, transport, database,
MCP and telemetry SDK imports belong under `adapters/`; bootstrap owns lifecycle.
The runtime never imports model training or curation libraries.

Input adapters must authenticate before binding `tenant_id` and `principal_id`.
These protected metadata fields are independently optional; copying a body field
into a trusted identity is not authentication. Use `decode_envelope` for untrusted
JSON and `accept_envelope` to check claims against verified identity. Pydantic
exceptions may contain input values and must not reach clients or telemetry.

Capacity limits are local to each resource and replica. They prevent an unbounded
in-process queue; they do not promise distributed provider-wide rate limiting.
Cancellation releases local capacity but cannot prove a remote mutation stopped.

## Offline compilation

`foliqant.compiler.compile_workflow` reads one workflow directory and explicit
model/tool/handler registries. It never discovers endpoints or invokes a model.
Its frozen plan includes exact source revisions, schema resources, bindings and
validated routes. The shared native decision adapter checks output structure,
catalog membership and evidence against the actual input before producing route
facts. Uncertainty remains explicit; it does not become a successful route.

Authoring accepts safe YAML and Markdown frontmatter. Duplicate keys, custom
tags and YAML aliases are rejected. Workflow schemas support confined local files
and fragments; declared tool schemas use internal fragments only. Schema `$id`
is unsupported, with depth and node limits enforced. JSON data inside schema
`const`, `default` and examples is not interpreted as schema instructions.
Generate/check the public schemas using the development commands above.

## Deployment, CLI, and synchronous HTTP

`foliqant.yaml` is a strict version 1 deployment document. It maps workflow names
to relative bundle directories and may configure model, MCP, execution, HTTP, and
telemetry profiles. Unknown fields, duplicate YAML keys, aliases, custom tags,
paths escaping the configuration directory, and workflow-name mismatches fail
startup. Environment substitution is not available inside arbitrary YAML values;
credential fields contain environment variable names, and bootstrap resolves a
single environment snapshot before opening adapters.

The installed `foliqant` command provides:

- `init DEST` creates a model-free project without overwriting a path.
- `validate`, `explain`, and `doctor` compile locally without constructing model,
  MCP, HTTP, or telemetry clients. `doctor` reports installed optional extras; it
  does not probe their endpoints.
- `run` executes one envelope synchronously in the current process. Optional
  `--tenant-id` and `--principal-id` values are explicit trusted operator input.
- `serve` owns the configured clients and a synchronous authenticated HTTP
  listener for its process lifetime.

When telemetry is configured, CLI `run` and `serve` own global installation.
Programmatic `open_application` leaves global provider ownership with the
embedding host unless explicitly requested.

Successful commands emit a JSON object (help displays usage text). Technical run
failures emit no success result; they use a fixed safe error object on
stderr without exception text, input, prompts, or credentials. The
[authenticated HTTP example](../examples/http-workflow/README.md) is model-free
and exercises `validate`, `explain`, `run`, and bearer-protected `serve`.

HTTP supports `POST /workflows/{name}/runs`, `GET /health`, and `GET /ready`.
Runs finish within that request and return a terminal result or RFC 9457 problem
details. There is no detached acceptance, job status lookup, or cancellation API;
the current runner does not preserve work across a process restart.

Authentication happens before reading the request body. A bearer profile resolves
each `token_env` once at startup, verifies the credential, establishes optional
tenant/principal identity, and returns explicit workflow grants. A JWT profile
verifies signature, issuer, audience, expiry, issued-at time, and an asymmetric
algorithm (`RS256`, `ES256`, or `EdDSA`) against the fixed HTTPS JWKS endpoint.
The JWKS endpoint must honor `Accept-Encoding: identity`; unexpected compressed
responses are rejected. Cache-lock waits, reads and cleanup share one deadline.
JWT claims establish only the configured identity fields; workflow grants always
come from trusted deployment configuration. Development authentication is allowed
only with `mode: development` and a literal loopback listener. Nonlocal bearer or
JWT deployment requires host-managed TLS termination. The built-in Uvicorn setup
disables proxy headers and never treats forwarded identity headers as trusted.

Authentication and workflow access do not imply permission to use a business
resource. After the ingress workflow grant and compiled step allowlist, bootstrap's
default MCP policy permits only declared read tools. A host embedding the service
can inject `RuntimePlugins(tool_authorizer=...)` to recheck resource-specific
business permissions from trusted identity and validated arguments on every
call. Write tools remain disabled pending durable operation identity and
reconciliation.

## Embedded execution

For the same composition used by the CLI, compile a deployment once and own its
async lifespan. The caller supplies an authenticated and authorized identity;
calling this Python API does not verify a token or grant workflow access:

```python
import asyncio
import os
from pathlib import Path

from foliqant.bootstrap import load_environment, open_application, prepare_application
from foliqant.contracts.envelope import Envelope
from foliqant.core.identity import Identity

async def main() -> None:
    config = Path("examples/http-workflow/foliqant.yaml")
    prepared = prepare_application(config)
    async with open_application(
        prepared, environment=load_environment(config, os.environ)
    ) as application:
        result = await application.run(
            "hello", Envelope(payload={"message": "hello"}),
            identity=Identity(principal_id="example_operator"),
        )
        assert result.execution.status == "completed"

asyncio.run(main())
```

Trusted Python hosts can pass named `HandlerRegistration` objects through
`prepare_application(..., handlers=...)` and runtime adapters through
`open_application(..., plugins=RuntimePlugins(...))`. Handler callbacks are async,
receive frozen inputs and a per-call `StepContext`, and return `StepOutcome`.
Their declared input/output schemas are checked independently of the callback;
selected write handlers cannot be prepared until durable execution exists.
Use the lower-level runner below only when the host needs to own that composition.

The [offline example](../examples/embedded-workflow/README.md) combines a compiled
workflow, frozen input schemas, an async handler and a public execution result.
From the repository root, run:

```sh
uv run --project service --no-sync python examples/embedded-workflow/run.py
```

Construct `WorkflowSchemas(plan)` once and pass it to `WorkflowRunner` alongside
an async `StepExecutor`, a shared `CapacityLimiter`, and optional `ExecutionLimits`.
Pass an accepted envelope and explicit trusted `Identity` to `await runner.run(...)`.
Ingress can also pass a `TraceContext` as `transport_trace=`. A valid transport
carrier takes precedence over envelope telemetry as a whole; the carriers are
never merged, and the original metadata remains unchanged.
Use `to_execution_result(...)` to validate and serialize the returned core result.
The runner rechecks identity claims and input schemas before admission; these
boundary errors raise `ServiceError`. Execution failures return a failed result
with fixed safe error text. `CancelledError` propagates to the caller.

The executor receives immutable inputs and a separate context for each step.
It owns result validation and operation timeouts within the original run deadline.
Reserve every model/tool attempt through `context.budget` before starting I/O.
Failed requests still count. Report measured token usage once per model ticket;
unavailable counts remain null rather than becoming zero. Pure handlers and
finish steps do not consume model or tool attempts.

This runner is nondurable: it has no restart recovery, persisted budgets or
external mutation reconciliation. It does not launch background business tasks.
The offline example demonstrates these boundaries without model calls or external
dependencies; it is not the complete production service application.

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
configured requests. Bedrock remains disabled pending its bounded credential and
worker integration; Azure managed identity and broader Foundry APIs are not yet
implemented. Offline protocol tests do not qualify a live deployment.

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
remain disabled until durable effect tracking and reconciliation are available.

For MCP HTTP authentication, set `auth` to a host-registered credential-provider ID.
The hook receives the current trusted identity, server and context. The provided
`SdkOAuthCredentialProvider` uses the MCP SDK's OAuth discovery, PKCE/state,
resource binding and refresh handling. Supply a storage factory partitioned by
**every** `McpCredentialScope` field and protected at rest; no plaintext token
store is supplied. Explicitly allow the HTTPS authorization-server origins.
Discovery, registration and refresh cannot contact other origins.

Interactive callbacks belong only to an explicit operator login flow. Normal
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

Use native async clients for HTTP, databases, Redis and MCP. Do not call a
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
boolean outcome. These are tested primitives; full transport/provider scaling
and durability acceptance remain part of the ongoing implementation.

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
