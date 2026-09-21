---
name: foliqant-service
description: "Build and test Foliqant workflow bundles and embedded async service integrations. Use for the service Python package, workflow compiler, execution ports and adapters; not for model training or data curation."
---

# Foliqant workflow service

Use the [service guide](../../service/README.md) and
[runnable embedded example](../../examples/embedded-workflow/README.md) as the
implemented API authority. The service is still under implementation: the
embedded runner works, but a deployable CLI, provider/MCP adapters, durable
transports and recovery are not yet available. Never present planned commands or
production guarantees as working features.

Keep the service in its own uv project and environment. Select only needed extras
for production; use the dev group for testing. Never import training or curation
packages. Native decision types and semantic validation come from the shared
`foliqant_decisions` package, not a duplicated schema or `foliqant_model`.

## Workflow and runtime boundaries

- Compile YAML/Markdown bundles with `compile_workflow` and explicit registries.
  Compilation never discovers endpoints. Use supported `decision`, `llm`, `mcp`,
  `handler` and `finish` authoring; dispatch is not implemented. Provider/tool
  execution still needs an injected executor. Do not infer executable support
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

Use native async I/O. A blocking-only SDK uses the owned bounded
`BlockingExecutor` plus SDK timeouts; started workers retain capacity after caller
cancellation. Keep request identity, auth and trace state off shared mutable
adapters. Only safe allowlisted events reach the JSON logger, never raw errors,
prompts, responses or credentials.

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
