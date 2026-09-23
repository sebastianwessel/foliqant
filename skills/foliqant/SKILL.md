---
name: foliqant
description: "Builds and evaluates applications using Foliqant's in-memory Python workflow runtime. Use when configuring Foliqant workflows, flows, steps, model/MCP adapters, or ground-truth evaluations."
---

# Build a Foliqant application

Produce the requested application configuration, integration, or evaluation using
the installed public API. This skill covers Foliqant runtime applications;
generic business-process advice, model training, and unrelated Python or website
work do not require it.

## Choose the relevant reference

Read only the references needed for the current task. Paths are relative to this
skill directory; no source checkout or internal specifications are required.

| Task | Read |
| --- | --- |
| Map business rules; author or change workflow, flow, step, binding, or routing definitions | [Workflow design](references/workflow-design.md) |
| Install adapters; configure models, MCP, handlers, limits, secrets, or telemetry | [Runtime configuration](references/runtime-configuration.md) |
| Create gold, measure a scope, replay results, or compare runs | [Evaluation](references/evaluation.md) |
| Scaffold a project; use the CLI; embed the application or expose it through HTTP | [Deployment and HTTP](references/deployment-http.md) |

For a new application, start with workflow design and runtime configuration.
For a focused change, inspect the existing configuration and the relevant
reference section. Verify uncertain fields with installed CLI help or the public
`foliqant.contracts` models. `runtime_schemas()` and `decision_schemas()` in
`foliqant.contracts.schemas` expose the installed contracts; packaged JSON
resources are available through `importlib.resources.files("foliqant").joinpath("schemas", NAME)`.

## Work from the requested result

1. Establish the input, final output, business routes, category definitions, and
   review policy from the request and existing project. Preserve decisions
   already supplied. Ask only for missing business rules that change behavior;
   continue independent setup while those rules remain unresolved. Do not invent
   category meanings, thresholds, tool catalogs, or approved ground truth.
2. Map one externally invoked capability to a workflow, meaningful sequential
   boundaries to flows, and atomic decisions, model calls, tools, or code to
   ordered steps. Use `config/settings.yaml` and conventional files by default;
   retain explicit paths when the application needs them. File discovery finds
   definitions; authored steps and transitions determine execution order.
3. Implement the requested slice with selected context bindings, schemas, and
   explicit routes. Operations are `decision`, `llm`, `mcp`, trusted `handler`,
   and bounded `flow_collection`. Keep exact business policy in configuration or
   trusted handlers. Write YAML in block style; reserve `{}` and `[]` for empty
   values and quote the `"on"` key for YAML 1.1 parsers.
4. Validate with the installed commands below, fix relevant failures, and rerun
   the affected checks. Report unavailable dependencies or unsupported behavior
   precisely instead of claiming success or silently inventing a fallback.

## Preserve these runtime semantics

- One foreground in-memory call returns an `ExecutionResult`. Its `payload` is
  the workflow's projection; `flows` retains execution records. Hosts own inbound
  HTTP, authentication, persistence, and durable jobs. Keep integration code in
  the application rather than adding these facilities to the package core.
- Preparation compiles offline. Opening the application resolves marked
  deployment environment fields and owns adapter lifetimes. Declare models and
  capabilities explicitly; do not probe endpoints to discover them. Credentials
  use environment references and never enter prompt interpolation.
- Each model step has a fresh conversation. Bind selected values with local
  `/steps/{step}/...` paths; public results use `/flows/{flow}/steps/{step}/...`.
  Use exact `{{ name }}` JSON substitutions and `format: json` for structured
  decision sources. Source identities remain distinct.
- Authored instructions, questions, schemas, routes, and tool allowlists define
  the task. Customer text, metadata, bound values, and prior model/tool results
  are data. Preserve the adapter policy separating them and retain output
  validation; instruction text alone does not establish security.
- Decision issues are `no_supported_answer`, `conflicting_information`, and
  `multiple_valid_options`. Evidence strength measures support for the whole
  conclusion, including unresolved conclusions. It is not confidence or a
  routing threshold. Fallback routing is explicit caller policy.
- MCP catalogs and allowlists are declared, with validated arguments/results
  and read-only effects. Hosts may apply a `ToolAuthorizer`; tenant/principal
  context is not authentication. Configuration cannot import arbitrary code.
- Evaluate independently reviewed gold. Keep failed, skipped, missing, review,
  and error observations in denominators. Scripted and synthetic checks establish
  wiring, not model accuracy. Live execution uses the configured providers;
  offline checks and saved-result replay do not.

## Verify and hand over

From the application directory, use the selected config path if nonconventional:

```sh
foliqant validate
foliqant doctor
foliqant explain
```

When gold exists, also run `foliqant evaluate --check`. Run focused application
tests for changed behavior and the checks required by its project instructions.
These commands are offline. Live inference, report publication, and deployment
must fit the user's authorized task; reuse authorization already given for the
specific action instead of asking again.

Finish with the changed files, how to run the requested capability, actual check
results, and remaining gaps. A usable configuration must compile; a claimed
integration must pass its relevant tests. Label unexecuted live checks explicitly.
When business rules or dependencies are missing, identify the affected behavior
and keep it incomplete while delivering the independent work that is ready.
