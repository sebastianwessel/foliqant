# Configure steps

A step is one bounded operation inside a sequential flow. Choose the narrowest
type that matches the work: a typed model decision, general model output, one
MCP call, trusted Python code, or a bounded collection of callable flows.

| Type | Use it for | External work |
| --- | --- | --- |
| [`decision`](decision.md) | Evidence-backed choice, labels, predicate, ordinal, or request extraction | One or more bounded model requests |
| [`llm`](llm.md) | Text generation or JSON extraction | One bounded model conversation |
| [`handler`](handler.md) | Deterministic application policy in trusted Python | One awaited host callback |
| [`mcp`](mcp.md) | One predetermined read-only tool call | One MCP session and tool call |
| [`flow_collection`](flow-collection.md) | A planned list of independent callable-flow invocations | Sequential child flows |

An [agent loop](agent-loops.md) is an `llm` step with an MCP tool allowlist. It
is not another step or workflow type.

## Put a step in a flow

A flow lists step IDs in execution order. With the conventional layout, each ID
resolves beside `flow.yaml`:

```text
config/
  support_triage/
    triage/
      flow.yaml
      classify.step.md
      extract/
        step.md
        output.schema.json
```

```yaml
# config/support_triage/triage/flow.yaml
steps:
  - classify
  - extract
```

For step `extract`, the compiler accepts exactly one conventional definition:
`extract.step.yaml`, `extract.step.md`, `extract/step.yaml`, or
`extract/step.md`. A named step may instead set `definition` to a local file or
an inline mapping. Explicit paths stay inside the flow directory.

Only `decision` and `llm` support Markdown. Their YAML front matter contains
the configuration and their nonblank body supplies `instructions`. Do not also
set `instructions` in the front matter.

## Bind explicit input

Decision sources, LLM inputs, MCP arguments, handler inputs, and collection
items use the same binding forms:

```yaml
message:
  pointer: /payload/message
channel:
  literal: email
language:
  pointer: /payload/language
  optional: true
  default: en
```

Pointers use RFC 6901. During a flow, `/payload` is the resolved flow input and
`/steps/<id>/result` exposes an earlier local result. A missing required pointer
fails with `missing_binding`; JSON `null` is present and does not activate a
default. Optional pointers must declare one explicit `default`.

See [context and bindings](../configuration/context.md) for every scope and
schema rule.

## Understand the result

Steps run in list order. Each public step record appears at
`/flows/<flow>/steps/<step>` and has one of these statuses:

| Status | Meaning | What the flow does |
| --- | --- | --- |
| `completed` | The operation returned a validated result | Continue to the next step |
| `needs_review` | The operation produced business uncertainty or requested input | Stop this flow and use its `on_unresolved` route, if configured |
| `failed` | A technical or contract error occurred | Stop the execution |
| `cancelled` | The caller cancelled execution | Stop and propagate cancellation |
| `skipped` | An earlier operation prevented this step from running | Preserve it in the ledger |

A step never chooses the next flow. The containing flow owns `transition` and
`on_unresolved`; see [flow configuration](../configuration/flows.md). Errors
use stable safe codes such as `invalid_input`, `invalid_output`, `timeout`,
`budget_exhausted`, and `dependency_failure`. Provider exception text is not
part of the public result.

## Validate before running

```sh
foliqant validate --config config/settings.yaml
foliqant explain --config config/settings.yaml --workflow support_triage
foliqant doctor --config config/settings.yaml
```

These commands compile bindings, schemas, model capabilities, and tool declarations
without opening model or MCP endpoints. A configuration that names
custom handlers must instead call `prepare_application(..., handlers=HANDLERS)`
from Python so those trusted registrations are present; see
[handler registration](handler.md#register-before-compilation). Validation proves
the configuration is structurally consistent; it does not prove live endpoint
availability or business accuracy. Continue with [evaluation](../evaluation/index.md)
to test behavior against reviewed cases.
