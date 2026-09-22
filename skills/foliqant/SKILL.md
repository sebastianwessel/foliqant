---
name: foliqant
description: "Build, test, and evaluate Foliqant in-memory Python workflows. Use when mapping business processes, configuring the runtime, authoring workflows/flows/steps, integrating model or MCP adapters, or creating golden evaluations."
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
`foliqant.contracts.schemas.runtime_schemas()` and `decision_schemas()`.
Generated JSON resources are available through
`importlib.resources.files("foliqant").joinpath("schemas", NAME)` for
editor tooling. The Python models remain authoritative. Do not assume the
Foliqant source repository or its internal specifications are available.

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

Use `config/settings.yaml` and the conventional workflow, flow, and step files
unless explicit paths improve the application. Keep ordered steps and routes
authored; filesystem order never determines execution. Supported operations are
`decision`, `llm`, `mcp`, trusted `handler`, and bounded `flow_collection`.

Write authored YAML mappings and sequences in block style. Reserve `{}` and
`[]` for intentional empty values, and quote the `"on"` key so YAML 1.1
compatibility parsers do not coerce it to a boolean.

Bind only selected context. Each model step starts a fresh conversation. Use
exact `{{ name }}` JSON substitutions for LLM prompts and `format: json` for
structured decision evidence. Within flows use `/steps/{step}/...`; routing
and public results use `/flows/{flow}/...`.

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
configure a path only when customizing it. Pipeline cases contain workflow
envelopes; flow and step cases contain already-resolved boundary input. Use
public expectation paths under `/flows/{flow}/steps/{step}`.

Keep failed, skipped, missing, review, and error observations in denominators.
Use `evaluate --check` and replay for offline work. Normal evaluation uses the
configured providers and requires intended external calls. Scripted fixtures
prove wiring only; do not claim general model quality, security, latency, cost,
or cache efficiency from them.

## Verify changes

For a downstream application, run `foliqant validate`, `foliqant doctor`,
`foliqant evaluate --check` when gold is present, and focused application
tests. Use `foliqant explain` to inspect the compiled plan. Query
`runtime_schemas()` or `decision_schemas()` when tooling needs the installed
contract; do not hand-edit generated schemas or copy assumptions from another
release.
