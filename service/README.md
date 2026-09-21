# Workflow service

This is the independent Python package for Foliqant workflow execution. Its
implementation contract is [specification 11](../specs/11-workflow-service.md).
PydanticAI owns model/tool conversations; the service owns deterministic routing,
identity, authorization, admission, persistence and transports.

Implementation is in progress. Available foundations are strict envelopes,
protected metadata validation, immutable core values, safe errors and bounded
async admission, bounded blocking-I/O execution, safe JSON logging, an offline
workflow compiler, embedded async runner, public execution results, PydanticAI
model execution and shared native decision validation. A model-enabled inbox
example is available. The executable service CLI and durable
transports are not yet complete; read-only MCP steps and model tools are available. See the
[implementation status](../plans/workflow-service-status.md) for verified scope.

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
dependency separation, not a claim that the current foundations constitute a
deployable service. The root model-tooling environment is separate.

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

## Embedded execution

The [offline example](../examples/embedded-workflow/README.md) combines a compiled
workflow, frozen input schemas, an async handler and a public execution result.
From the repository root, run:

```sh
uv run --project service --no-sync python examples/embedded-workflow/run.py
```

Construct `WorkflowSchemas(plan)` once and pass it to `WorkflowRunner` alongside
an async `StepExecutor`, a shared `CapacityLimiter`, and optional `ExecutionLimits`.
Pass an accepted envelope and explicit trusted `Identity` to `await runner.run(...)`.
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
injected host MCP runtime and a tool-capable model profile. Model instrumentation is explicitly off until the safe
OTel integration is connected.

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

For HTTP authentication, set `auth` to a host-registered credential-provider ID.
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
only W3C `traceparent`/`tracestate` for discovery and calls. Full OTel instrumentation
is a separate integration still in progress.

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
