# Workflow service

This is the independent Python package for Foliqant workflow execution. Its
implementation contract is [specification 11](../specs/11-workflow-service.md).
PydanticAI owns model/tool conversations; the service owns deterministic routing,
identity, authorization, admission, persistence and transports.

Implementation is in progress. Available foundations are strict envelopes,
protected metadata validation, immutable core values, safe errors and bounded
async admission, bounded blocking-I/O execution, safe JSON logging, an offline
workflow compiler, embedded async runner, public execution results and shared native
decision validation. The executable CLI, model/MCP integration,
durable transports and full application example are not yet complete. See the
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
Generate/check the six public schemas using the development commands above.

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
dependencies; it is not the complete model-enabled service application.

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
