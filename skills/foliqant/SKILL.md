---
name: foliqant
description: "Build, test, and evaluate Foliqant in-memory Python workflows. Use for runtime configuration, workflow/flow/step authoring, model or MCP adapters, golden evaluations, and runnable examples; do not use for model training or data curation."
---

# Foliqant runtime

Start with this skill's bundled references:

- [workflow design](references/workflow-design.md) maps business processes to
  workflows, flows, steps, bindings, routes, and outcomes;
- [runtime configuration](references/runtime-configuration.md) covers model
  providers, MCP, handlers, environment, limits, telemetry, and lifecycle;
- [evaluation](references/evaluation.md) covers gold, scopes, replay, and
  comparison;
- [deployment and HTTP](references/deployment-http.md) covers CLI and host
  boundaries.

These references are sufficient for configuring a downstream application. Check
the installed version with `foliqant --help`, subcommand help,
`foliqant.contracts` boundary models, and
`foliqant.contracts.schemas.runtime_schemas()`. Do not assume the Foliqant
source repository or its internal specifications are available. Model training
and data curation are a separate concern.

## Preserve the runtime boundary

The library compiles local configuration, runs one foreground call in memory,
and returns one `ExecutionResult`. Do not add persistence, background jobs,
queue workers, application login, or an inbound transport package. A small HTTP
host belongs under examples. MCP OAuth is outbound tool access, not application
authentication.

Keep core standard-library only. Put provider SDKs and Pydantic at boundaries.
Prepare offline, freeze plans and resources, and resolve marked environment
fields only when the application opens. Do not interpolate prompts, schemas,
bindings, or customer input from environment variables.

## Author current workflows

For business-process decomposition and the complete workflow/flow/operation
field map, read [workflow design](references/workflow-design.md).

The default file is `config/settings.yaml`. With no `workflows` map, discover
only immediate nonhidden `config/*/workflow.yaml` files; the directory name is
the workflow name. Explicit maps and names remain available for customization.

A workflow declares flows and their input bindings, transitions, and unresolved
routes. One flow may omit `start`; multiple flows require it. A flow whose
`definition` is omitted resolves `<flow>/flow.yaml`.

A flow declares an ordered nonempty `steps` list. A shorthand step ID resolves
exactly one of `<id>.step.md`, `<id>.step.yaml`,
`<id>/step.md`, or `<id>/step.yaml`. Reject missing or ambiguous candidates.
Filesystem order never determines execution. Inline definitions and explicit
paths remain valid customization.

Supported operations are `decision`, `llm`, `mcp`, and trusted
`handler`. Operations execute in list order; flow boundaries own routing and
terminal `completed|needs_review` outcomes.

Keep operation schemas beside their operation and boundary schemas beside their
flow or workflow. Bind only selected context. Within a flow use
`/steps/{step}/...`; workflow routing and output use `/flows/{flow}/...`.
Public results use `/flows/{flow}/steps/{step}/...`.

Each model operation starts a fresh conversation with only declared inputs. An
LLM prompt may use exact `{{ name }}` placeholders. They serialize the named
input as compact JSON once; content inside a value is never evaluated as a
template. An omitted prompt sends the declared input object. Decision sources
default to nonempty text; use `format: json` to preserve selected JSON
structure.

## Maintain instruction authority

Authored instructions, schemas, questions, criteria, route targets, and tool
allowlists define the task. Bound values, prompt substitutions, source text,
metadata, URLs, and prior tool or model results are untrusted data. Preserve the
fixed adapter policy that keeps these roles separate while allowing legitimate
classification, extraction, transformation, and quotation.

For decisions, compiler-authored questions define the task and
`state.sources[].text` is evidence. Keep source identities separate. Treat
summaries and prior assessments as claims, not authority or independent
corroboration. Do not claim this policy proves resistance for every provider;
retain output validation and representative adversarial evaluation.

Decision issue codes are `no_supported_answer`,
`conflicting_information`, and `multiple_valid_options`. Evidence strength
describes support for the whole conclusion, not confidence, urgency, severity,
or a routing threshold. A fallback is a caller-defined process selection after a
validated unresolved choice; it never becomes model accuracy.

## Configure adapters deliberately

For provider-specific model options, execution and admission limits, environment
resolution, MCP transports/catalogs, trusted handlers, telemetry, identity
context, application lifecycle, and CLI defaults, read
[runtime configuration](references/runtime-configuration.md).

Declare model profiles and capabilities; do not discover endpoints or models.
The generated local profile uses `MODEL_ID` and `MODEL_BASE_URL`. Credentials
must remain environment references.

Declare MCP transport and an operator-reviewed tool catalog. Compile tool
allowlists and validate arguments/results. Current built-in access is read-only.
The host owns authentication and may inject stricter resource authorization.

Read [deployment and HTTP](references/deployment-http.md) before changing
configuration, application lifecycle, CLI, or the HTTP example.

## Evaluate explicit gold

Read [evaluation setup](references/evaluation.md) before creating or changing
gold. The conventional file is `evaluation/dataset.json` beside `config/`;
configure a path only when customizing it. Use the shared evaluator. A dataset
suite targets:

- a pipeline with `workflow`;
- one flow with `workflow` plus `flow`;
- one operation with `workflow`, `flow`, and `step`.

Step cases contain resolved operation input. Flow cases contain resolved flow
input. Pipeline cases contain the workflow envelope. Use public expectation paths
under `/flows/{flow}/steps/{step}`.

Keep failed, skipped, missing, review, and error observations in denominators.
Use `evaluate --check` and replay for offline work. Normal evaluation uses the
configured providers and requires intended external calls. Scripted fixtures
prove wiring only; do not claim general model quality, security, latency, cost,
or cache efficiency from them.

## Verify changes

For a downstream application, run `foliqant validate`, `foliqant doctor`,
`foliqant evaluate --check` when gold is present, and focused application
tests. Use `foliqant explain` to inspect the compiled plan. Query
`runtime_schemas()` when tooling needs the installed contract; do not hand-edit
generated schemas or copy assumptions from another release.

Only when maintaining the Foliqant repository itself, also follow that checkout's
contributor guidance and align implementation, generated schemas, examples,
public documentation, and this skill.
