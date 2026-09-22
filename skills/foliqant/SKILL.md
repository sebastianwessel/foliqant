---
name: foliqant
description: "Builds, tests and evaluates Foliqant in-memory Python pipelines. Use when working on the reusable package, workflow bundles, model/MCP adapters, golden evaluations or runnable examples; not model training or data curation."
---

# Foliqant Python package

Use the [package guide](../../docs/index.md) and
[runnable examples](../../examples/README.md) as public usage authority. The root
uv project installs `foliqant`; model development has its own project in `model/`.

## Boundaries

The library accepts input, executes configured steps in memory, and returns a
result. Do not add persistence, queues, background jobs, application login,
HTTP/Redis transport packages or infrastructure placeholders. A small HTTP host
belongs under examples. MCP OAuth is outbound tool access, not app authentication.

Decision inputs and question types stay in `foliqant.decisions` at native v2.
Runtime output uses `foliqant.contracts.decisions.DecisionOutput` at schema v3:
required `reason` (nonblank, at most 400 characters) and
`evidence_strength: limited|strong|null`, with no explanation or citation arrays.
Keep model tooling's native v2 output unchanged; it is a separate contract.
The issue codes remain `no_supported_answer`, `conflicting_information`, and
`multiple_valid_options`. Missing information and catalog gaps share the first
code; do not recreate that split with extra flags or parse reasons to route.
Strength is support for the whole reported assessment: answerability, issues
and any substantive answer. Strongly justified abstentions and unknown predicates
are valid; null means unassessed, not unanswered. Never assess urgency/severity
instead of support, or treat strength as confidence or an automatic route threshold.
Limited interpretations must satisfy the authored criteria without inventing
essential missing facts. Collections include material claims about members and
unresolved parts; many clear items cannot compensate for a weak material claim.
Request units have no evidence array; non-null subjects must occur verbatim in
allowed input text.
Keep the assessed construct precise: an undecided remedy need not weaken a clear
purpose classification. Absence of information about a fact is not evidence that
the fact is false; an unknown set is not an established empty collection.
Runtime code never imports model training/curation. Importing contracts must not
initialize model clients or telemetry. Core depends on standard-library values and ports; SDKs and
Pydantic adapters stay outside it.

## Author and run

- `prepare_application` compiles configuration and confined workflow bundles
  offline; `open_application` owns clients and async cleanup; `app.run` returns
  the result of the current call. No endpoint discovery or hidden SDK retries.
- Supported steps: `decision`, `llm`, `mcp`, trusted `handler`, and `finish`.
  Explicit graph routes are deterministic. Model output cannot invent a route.
- Start small with inline `steps`; use separate files only when useful. Never
  mix layouts. Reuse inline or file schemas through the same compiler. Every
  operation has a success transition; `finish` owns the terminal outcome.
- Markdown instructions belong in frontmatter or the body, not both. Use one
  `next` when all successful answers share a route.
- Bind inputs with tagged literals or RFC 6901 pointers. Earlier results are
  `/steps/<id>/result`; public returned records are under `decisions`.
- Pass selected prior fields through existing bindings, including MCP arguments;
  do not add automatic conversation history or a second context DSL.
- Single-choice fallback is a category object with an explicit issue allowlist.
  Preserve the native result and separate `selection.origin: model|fallback`.
  Unresolved routes may use an issue map with a required default; disagreement
  uses the default. Never route a fallback as a successful model answer.
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
Steps may select an alias, override `{profile, model?, options?}`, or provide a
full provider configuration. Reuse provider validation and environment resolution;
derived profiles share source admission. Inline credentials require references.
Omitted options inherit and explicit null clears optional settings.
Decision steps append shared guidance for answerability, short reasons and
evidence strength; keep business criteria in the workflow.
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

When asked to set up evaluations, read [evaluation setup](references/evaluation.md)
and the [public guide](../../docs/guides/testing-and-evaluation.md). Use the user's
independently authored gold and business process as authority. Add the optional
`evaluation.dataset` reference and strict JSON cases; declare metric labels
explicitly. Ask for missing business outcomes instead of fabricating gold,
thresholds, or acceptance policy. Never derive expected answers from predictions.

`foliqant evaluate --check` is offline validation, `--replay REPORT` scores saved
results without SDK/model calls, and normal `evaluate` runs configured workflows.
Use full-pipeline suites and optional isolated-step suites with resolved inputs.
No hidden judge calls or executable configuration imports. Store golden corpora
and full reports outside Git. Report what was actually checked: synthetic or
scripted successes prove wiring, not model quality or production reliability.

The existing Python `evaluate` and `compare_variants` APIs accept immutable
suites and explicit versioned async scorers. Keep missing/skipped/error outcomes
in denominators; do not confuse schema validity, confidence, or assertion pass
rate with classification accuracy. Use a separate holdout after selecting
explicit prompt/model variants. No automatic prompt optimization is implied.

Diagnose gold/contract mismatches before tuning prompts. Correct independently
established semantics with a new gold revision and retain the original report;
never rewrite expectations just to match predictions. Score the required action,
evidence strength, metadata propagation, and intended review/routing as well as
category labels.
Use verbatim evidence for extractive contracts; exact substring checks do not
prove a free-form reason is semantically correct. Keep repeated attempts
distinct from independent source cases and report unknown usage as unknown.

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

If a required API, configuration value or authorization boundary is undefined,
report the concrete gap and continue independent work; do not invent a fallback.
Existing user authorization remains valid within its scope. Model lifecycle work
belongs to `foliqant-model`, not this skill.
