---
name: foliqant-service
description: "Build and test Foliqant workflow bundles and embedded async service integrations. Use for the service Python package, workflow compiler, execution ports and adapters; not for model training or data curation."
---

# Foliqant workflow service

Use the [service guide](../../service/README.md) and
[offline example](../../examples/embedded-workflow/README.md) and
[model-enabled inbox example](../../examples/inbox/README.md) as the
implemented API authority. The service is still under implementation: the
embedded runner, model executor and read-only MCP adapters work, but a deployable
CLI, durable transports and recovery are not yet available. Never present planned commands or
production guarantees as working features.

Keep the service in its own uv project and environment. Select only needed extras
for production; use the dev group for testing. Never import training or curation
packages. Native decision types and semantic validation come from the shared
`foliqant_decisions` package, not a duplicated schema or `foliqant_model`.

## Workflow and runtime boundaries

- Compile YAML/Markdown bundles with `compile_workflow` and explicit registries.
  Compilation never discovers endpoints. Use supported `decision`, `llm`, `mcp`,
  `handler` and `finish` authoring; dispatch is not implemented. Provider/tool
  execution needs a configured executor. Do not infer executable support
  merely because a step compiles.
- Bind data explicitly with `{pointer: /payload/...}` or `{literal: ...}`.
  Previous step results live under `/steps/<id>/result`. Public output uses
  `decisions`, not `steps`. Missing optional bindings require explicit defaults;
  null is not missing. Quote predicate route keys `"true"` and `"false"` in YAML.
- Construct `WorkflowSchemas(plan)` once from frozen schema resources. References
  are confined to the bundle. Never introduce request-time schema retrieval.
- Decode untrusted bytes with `decode_envelope`; use `accept_envelope` with an
  explicit trusted `Identity`. Body IDs are claims, not authentication. Tenant
  and principal are independently optional and remain immutable downstream.
- `WorkflowRunner.run` is async and needs an executor, validator and admission
  limiter. Reuse admission per resource, not one global serialization lock.
  Each invocation owns its records, context and budgets. Map its result through
  `to_execution_result`; do not serialize internal dataclasses directly.
- Reserve attempts through `StepContext.budget` before external I/O, including
  failed calls. Missing usage is unknown, not zero. The executor validates
  output and enforces model/tool timeouts within the original deadline. No
  adapter may let model output pick arbitrary routes or mutate trusted identity.
- Business uncertainty follows `on_unresolved` or ends in `needs_review` before
  ordinary routing. Technical failures are safe errors, not answerability values.
  Cancellation propagates. The embedded runner does not persist or reconcile
  effects; never claim that local cancellation proves a remote mutation stopped.

For model steps, construct `ModelProfiles`, then own `open_model_bindings` in the
application lifespan. Inject `ModelExecutor(bindings, schemas)` into the runner.
Aliases and model IDs are explicit; no `/models` discovery. Structured output
mode is `native` or `tool`, not a prompt-only fallback. Tool mode only formats the
output; it does not grant function-tool access. Store credentials outside profiles
and pass environment references. The factory disables SDK retries, validates
effective options and closes clients even when startup fails.

Native decisions use shared semantic/evidence validation. LLM schema references are fully inlined
offline for providers; public results unwrap the provider's `value` object and
are checked against the original schema. Dynamic/recursive provider schemas fail
before inference. Use non-strict provider mode for authored schemas to preserve
constraints; Anthropic requires `tool` mode for these steps. Local provider
preflight precedes admission and accounting; unsupported modes never silently
switch or consume an attempt. Never relax validation to accept a refusal or truncated reply.
Missing token measurements remain unknown. Bedrock remains incomplete; do not
imply it is ready because its dependencies or contracts exist.

For MCP, reuse `McpProfiles` and the existing `DeclaredToolCatalog`. Construct
`McpClientSessionFactory` once and inject it plus a required host `ToolAuthorizer`
into `McpRuntime`. Use `McpExecutor` for explicit calls or inject the runtime into
`ModelExecutor(..., tools=runtime)`. `supports_tools` is a model capability, not
permission. Every step allowlists tools and every call reauthorizes its frozen
arguments against the current trusted identity. Do not share authenticated SDK
clients or mutable token state between callers.

Catalog schema drift, duplicate names, pagination cycles and oversized catalogs
fail closed. Tool results use declared schema validation; nontext blocks are not
silently discarded. Required/named choices need a successful validated call in
model context, then allow final output. Automatic interaction/retry rounds are
disabled. MCP input-required becomes `needs_review`; interactive continuation and
write-effect execution await durable identity/reconciliation support.

HTTP auth profiles contain a registered credential-hook ID, never a token. Use
`SdkOAuthCredentialProvider` with explicit allowed HTTPS authorization origins
and a host storage factory partitioned by the complete credential scope, with
protection at rest. Interactive callbacks are for explicit operator login only;
normal runs use stored/refreshable credentials and never launch a browser. Pass
only present trusted IDs under the configured domain-qualified `_meta` key;
forward W3C trace fields separately, without baggage or business metadata.
Stdio uses trusted command/args and the SDK's fixed safe environment baseline
plus an explicit overlay. Never copy all environment secrets into a child.

Use native async I/O. A blocking-only SDK uses the owned bounded
`BlockingExecutor` plus SDK timeouts; started workers retain capacity after caller
cancellation. Keep request identity, auth and trace state off shared mutable
adapters. Only safe allowlisted events reach the JSON logger, never raw errors,
prompts, responses or credentials.

## Safe observations

Use the `telemetry` extra and `TelemetryRuntime.build` with explicit signal URLs,
header environment references and a reviewed `TelemetryLabels` allowlist. Missing
or empty endpoints allocate no exporters. Keep runtime ownership in bootstrap;
`install_global()` is explicit for the MCP SDK and refuses an existing host
provider. Pass `WorkflowTelemetry` to the runner, `ModelTelemetry` to the model
executor and W3C `trace_carrier` to MCP. Do not patch SDK global tracers or reset
OTel globals between tests; isolate global installation tests in a subprocess.

Never bypass `SafeSpanProcessor`: it sanitizes before batch queueing, including
SDK exception events, tool definitions, names and resource/scope/link metadata.
Disabling model content capture alone is insufficient. Keep PydanticAI raw
metrics disabled; host counters use actual measurement presence, not default
zeros or duplicate component totals. Approve labels at startup, never from input.
No identity, prompts, responses or baggage belongs in observations. Configure
safe JSON logging before SDK initialization, including debug runs. Shutdown is
async/bounded; report incomplete telemetry cleanup without changing a completed
business outcome. See the service guide and the embedded `--telemetry` example.

## Checks

From `service/`:

```sh
uv sync --locked --all-extras --group dev
uv run --no-sync python -m pytest tests
uv run --no-sync mypy src
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync python scripts/generate_schemas.py --check
```

Update canonical boundary types and regenerate service schemas together. Keep
the root example executable and test real compiler/validator/runner integration,
concurrent isolation, cancellation and error redaction with offline adapters.
Default tests must not discover endpoints or issue model requests. Protocol
simulation is not evidence of live model accuracy or production recovery.
