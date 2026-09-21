---
name: foliqant
description: "Build, test and evaluate Foliqant in-memory Python pipelines. Use when working on the reusable package, workflow bundles, model/MCP adapters, golden evaluations or runnable examples; not model training or data curation."
---

# Foliqant Python package

Use the [package guide](../../docs/README.md) and
[runnable examples](../../examples/README.md) as public usage authority. The root
uv project installs `foliqant`; model development has its own project in `model/`.

## Boundaries

The library accepts input, executes configured steps in memory, and returns a
result. Do not add persistence, queues, background jobs, application login,
HTTP/Redis transport packages or infrastructure placeholders. A small HTTP host
belongs under examples. MCP OAuth is outbound tool access, not app authentication.

Native decision contracts live once in `foliqant.decisions`. Runtime code never
imports model training/curation. Importing contracts must not initialize model
clients or telemetry. Core depends on standard-library values and ports; SDKs and
Pydantic adapters stay outside it.

## Author and run

- `prepare_application` compiles configuration and confined workflow bundles
  offline; `open_application` owns clients and async cleanup; `app.run` returns
  the result of the current call. No endpoint discovery or hidden SDK retries.
- Supported steps: `decision`, `llm`, `mcp`, trusted `handler`, and `finish`.
  Explicit graph routes are deterministic. Model output cannot invent a route.
- Bind inputs with tagged literals or RFC 6901 pointers. Earlier results are
  `/steps/<id>/result`; public returned records are under `decisions`.
- Missing differs from null. A final output referencing a conditional step needs
  an explicit optional/default binding. Do not bypass compiler dominance checks.
- Decode untrusted input with `decode_envelope`. Optional `tenant_id` and
  `principal_id` are caller context; explicit `Identity` checks consistency and
  fills missing values. Neither path authenticates a caller.
- Keep W3C context separate from identity and permissions; never forward baggage.
- Inject registered handlers explicitly, never executable imports from YAML.
  External effects remain read-only within the current scope.

Read [configuration and HTTP boundaries](references/deployment-http.md) when
changing bootstrap, CLI or the thin HTTP example.

## Models and tools

Use explicit `ModelProfiles`, model aliases and provider IDs. Structured output
uses supported native or tool mode, followed by independent host validation.
Refusals, truncation and invalid values remain failures. Never weaken validation
or silently fall back to prompted JSON to get an accepted answer.

Use the official MCP SDK behind the existing port. Declared catalogs, allowlists,
argument/result validation and host `ToolAuthorizer` checks remain mandatory.
Required/named tools require a validated successful invocation. Input-required
results stop for review; do not add automatic interaction rounds.

MCP OAuth uses host credential hooks, protected caller-scoped token storage and
explicit allowed authorization origins. Never include tokens in model messages,
metadata, diagnostics or telemetry. Stdio receives only its safe environment plus
configured overlay; never the complete process environment.

## Evaluate

Use `foliqant.evaluation` with explicit ground truth. Wrap `app.run` for complete
pipelines; wrap `app.run_step(workflow, step_id, envelope)` for isolated steps.
For isolated steps, payload keys are resolved input/source/argument names; no
upstream steps run and no routes are followed. Both paths use the same executor,
limits and output validation.

Compare exact/set/custom checks on the same immutable suite. Missing/skipped
outputs and scorer failures stay in denominators; schema validity and model
confidence are not accuracy. Review/failure rates remain separate from assertion
agreement. Durations and token usage are measured; unknown is not zero.

Prompt variants are explicit caller configurations, not automatically generated
by the evaluator. Record revisions, use a separate holdout after selection, and
keep one request at a time by default. No hidden judge calls. Store real golden
corpora and reports outside Git. Code-created synthetic cases demonstrate wiring,
not population accuracy or production reliability.

## Async safety and verification

Use native async I/O and bounded admission. Blocking SDK calls use the existing
owned `BlockingExecutor`; cancellation does not release capacity before the work
stops. Shared clients contain no request-local identity or mutable results. Drain
active work before closing dependencies; caller cancellation must propagate.

Only safe allowlisted values enter logs or telemetry. Explicit result objects
contain caller business data; error messages and diagnostics do not. Debug/export
settings are explicit. Embedded use must not replace host global tracing silently.

From the repository root:

```sh
uv sync --locked --all-extras --group dev
uv run --no-sync pytest tests
uv run --no-sync mypy src examples
uv run --no-sync ruff check src tests scripts examples
uv run --no-sync ruff format --check src tests scripts examples
uv run --no-sync python scripts/generate_schemas.py --check
```

Default tests use synthetic inputs and offline adapters. Live examples require
explicit invocation and the configured local model; never run them alongside
active data generation. Keep schemas, docs, examples and this skill aligned with
actual callable behavior. Do not invent commands or production guarantees.
