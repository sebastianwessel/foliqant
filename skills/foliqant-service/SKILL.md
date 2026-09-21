---
name: foliqant-service
description: "Build and test Foliqant workflow bundles and embedded in-memory async integrations. Use when working on the service Python package, compiler, runner, model/MCP adapters or small HTTP example; not for persistence, queue workers, application authentication, model training or data curation."
---

# Foliqant workflow service

Use the [service guide](../../service/README.md),
[embedded example](../../examples/embedded-workflow/README.md), and
[model-enabled inbox example](../../examples/inbox/README.md) as implemented API
authority. The service is one in-memory pipeline: accept one envelope, execute a
compiled sequence of deterministic steps, and return one terminal result. It does
not own persistence, idempotent intake, queue workers, child jobs, application
authentication or packaged HTTP/Redis transports. Do not present those removed
surfaces as pending requirements.

Keep the service in its own uv project. Never import training or curation packages.
Reuse native decision contracts and semantic validation from
`foliqant_decisions`; do not duplicate their schemas in the service.

## Configuration and host boundary

Read [deployment and HTTP example](references/deployment-http.md) for
`foliqant.yaml`, CLI/bootstrap or example work. Configuration is a strict,
versioned local contract. Do not add implicit environment interpolation, endpoint
discovery, executable imports, compatibility aliases, storage profiles, auth
profiles or worker settings.

`tenant_id` and `principal_id` are independently optional invocation context. If
an explicit `Identity` is omitted, the application derives context from validated
envelope metadata. If one is supplied, use it only to check consistency and fill
missing values. This is not authentication or authorization. Remote embedding
hosts own both concerns before calling the service.

A W3C trace carrier is also optional context. Keep it separate from permission,
do not forward baggage, and never let trace fields enable debug behavior.

## Compiler and in-memory runtime

- Compile YAML/Markdown bundles with `compile_workflow` and explicit registries.
  Compilation reads confined local files only and never discovers endpoints.
  Supported steps are `decision`, `llm`, `mcp`, `handler` and `finish`.
- Bind data with tagged literals or RFC 6901 pointers. Earlier results live at
  `/steps/<id>/result`; public output uses `decisions`. Missing and null differ.
- Build `WorkflowSchemas(plan)` from frozen schema resources. Never retrieve a
  schema during an invocation.
- Decode untrusted bytes with `decode_envelope`. Use `accept_envelope` for optional
  identity consistency/enrichment and immutable core mapping.
- Reuse one local `CapacityLimiter` for application admission and the configured
  per-model/per-tool limiters. Each invocation owns records, budgets, identity
  context and trace context. Shared clients must hold no caller state.
- `WorkflowRunner.run` returns the result for the current call only. Map internal
  values through `to_execution_result`; do not serialize core dataclasses.
- Reserve model/tool attempts before external I/O. Missing usage is unknown, not
  zero. Keep operation timeouts inside the original monotonic run deadline.
- Cancellation propagates. It never proves a remote operation stopped. A started
  blocking SDK call retains capacity until it actually finishes.
- Handlers are trusted host registrations, never YAML imports. Keep external
  effects read-only; writes needing durable operation identity are out of scope.

## Models

Construct `ModelProfiles`, own `open_model_bindings` for the application lifetime,
and inject `ModelExecutor`. Aliases and model IDs are explicit; no `/models`
discovery or hidden SDK retries. Structured output uses supported native or tool
mode, followed by independent host validation. Fully inline confined local schema
references for provider calls. Reject unsupported dynamic/recursive schemas and
provider/mode combinations before admission or attempt reservation.

Native decisions use the shared strict contract and semantic validator. Refusals,
truncation and invalid output remain failures. No prompt-only structured-output
fallback is allowed. Never relax validation merely to obtain a result.

## MCP and OAuth

Reuse `McpProfiles`, `DeclaredToolCatalog`, `McpClientSessionFactory` and
`McpRuntime`. Use the maintained MCP SDK for Streamable HTTP and stdio. Discovery
must match the configured catalog; validate and freeze arguments before host tool
authorization and I/O, and validate results independently. Every step allowlists
its tools. Required or named tool choice needs a successful validated call.
Input-required ends in `needs_review`; do not add automatic interaction rounds.

MCP OAuth profiles contain a credential-hook ID, never a token. Use
`SdkOAuthCredentialProvider` with explicit allowed HTTPS authorization origins and
host-owned protected token storage partitioned by complete server/resource/auth
and optional tenant/principal context. Interactive login is an explicit operator
action. Never put tokens in model messages, tool arguments, metadata, logs or
traces. Stdio receives the fixed safe environment baseline plus the configured
overlay, not the entire process environment.

Caller-supplied optional identity context may be forwarded only in the configured
domain-qualified MCP `_meta` field. Forward W3C trace fields separately without
baggage. MCP OAuth and `ToolAuthorizer` protect the external tool boundary; they
do not implement application authentication.

## Async ownership and safe observations

Use native async I/O. Put blocking SDK work behind the owned bounded
`BlockingExecutor` plus SDK timeouts. Do not create per-request event loops, call
`asyncio.run` in runtime code, block the loop, or launch unbounded tasks. Open
shared clients once in bootstrap and close them only after active work drains.
Report incomplete cleanup safely.

Use safe JSON logging with fixed allowlisted fields. Diagnostics, errors, logs and
telemetry never contain payloads, identities, prompts, responses, tool values,
credentials or raw exceptions. The caller-facing `ExecutionResult` intentionally
contains its accepted business payload and validated step outputs.

Optional telemetry requires an explicit endpoint. Sanitize spans before queueing,
including SDK events and tool definitions. Use configured bounded labels only.
Observation/export failures cannot replace the business result. Embedded use must
not silently replace a host global tracer.

## CLI, HTTP example and checks

The package supports offline `init`, `validate`, `explain`, `doctor` and foreground
`run`. Offline commands do not open model/MCP endpoints. There is no migration,
queue worker, durable lookup/cancel or packaged HTTP server command.

The runnable HTTP example is a thin adapter around one in-memory call. It may not
create detached jobs or imply authentication, persistence, recovery or production
hosting. Keep its request/result shapes aligned with generated envelope and
execution-result schemas.

From `service/`:

```sh
uv sync --locked --all-extras --group dev
uv run --no-sync python -m pytest tests
uv run --no-sync mypy src
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync python scripts/generate_schemas.py --check
```

Tests use synthetic inputs and offline adapters by default. Protocol fixtures prove
wire behavior, not live provider accuracy, application security, persistence or
production recovery.
