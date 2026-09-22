# Build a workflow

A workflow is a reviewed graph of flows executed in memory. Each flow receives
explicit input, runs an ordered list of operations, projects a result, and routes
to another flow or a terminal outcome. The compiler freezes the graph, schemas,
bindings, prompts, and declared capabilities before clients open.

## Start from the generated layout

```sh
uv run --no-sync foliqant init /tmp/my-workflow
```

The generated project uses the conventional layout:

```text
config/
  settings.yaml
  .env.example
  demo/
    workflow.yaml
    summarize/
      flow.yaml
      summarize.step.md
envelope.json
```

`config/settings.yaml` contains shared adapter and execution settings. Its
`workflows` map is optional:

```yaml
models:
  local:
    provider: openai_compatible
    model: $MODEL_ID
    base_url: $MODEL_BASE_URL
    allow_insecure_http: true
    output_mode: native
    supports_tools: false
```

When `workflows` is absent, Foliqant discovers only immediate nonhidden
`config/*/workflow.yaml` files. The directory name becomes the workflow name.
Use an explicit mapping when the public name or location must differ:

```yaml
workflows:
  public_name: another_directory
```

Paths are relative to the settings file and must remain below its directory.

Names use lowercase snake case and begin with a letter. YAML duplicate keys,
aliases, custom tags, and unknown fields are rejected.

## Define workflow and flow boundaries

`config/demo/workflow.yaml` defines workflow input, its starting flow, flow
instances, routing, and the public payload projection:

```yaml
defaults:
  model: local
input_schema: input.schema.json
output:
  pointer: /flows/summarize/result
flows:
  summarize:
    input:
      message:
        pointer: /payload/message
    transition:
      outcome: completed
```

`name` defaults to the workflow directory. A workflow with one flow may omit
`start`; a workflow with multiple flows must declare it.

A flow definition owns an optional input schema, an ordered nonempty step list,
and an optional output projection:

```yaml
# config/demo/summarize/flow.yaml
input_schema: input.schema.json
output:
  pointer: /steps/summarize/result
steps:
  - summarize
```

Omitting a flow `definition` resolves `<flow>/flow.yaml`. Each shorthand step
ID resolves exactly one of `<id>.step.md`, `<id>.step.yaml`,
`<id>/step.md`, or `<id>/step.yaml` beside the flow definition. Missing or
ambiguous candidates are rejected.

Conventions resolve definitions only. The authored `steps` list fixes operation
order, and authored flow transitions fix routing. Filesystem order never affects
execution. An explicit `definition` path or inline definition remains available
when the conventional layout is not appropriate.

A flow's successful `transition` names either another flow or a terminal
outcome:

```yaml
transition:
  flow: publish
# or
transition:
  outcome: completed
```

Route on an exact scalar flow result with `binding`, `cases`, and a required
`default`:

```yaml
transition:
  binding:
    pointer: /flows/classify/result/queue
  cases:
    billing:
      flow: billing
    cancel:
      flow: cancellation
  default:
    outcome: needs_review
```

Unresolved operations stop with `needs_review` unless the flow instance defines
`on_unresolved`. It may be one target or issue-specific targets with a required
`default`. An unresolved route cannot complete the workflow directly.

## Choose an operation

Flows run their operations in list order. There are five operation types:

| Type | Purpose |
| --- | --- |
| `decision` | Answer one or more evidence-backed typed questions |
| `llm` | Produce text or JSON matching an authored schema |
| `mcp` | Call one declared and allowed MCP tool |
| `handler` | Call a trusted async Python handler registered by the host |
| `flow_collection` | Invoke a bounded ordered list of allowlisted callable flows |

Operations do not route or terminate a workflow. Flow boundaries own routing
and outcomes.

### Collect callable flow results

Use a callable flow for a reusable operation invoked only by a
`flow_collection` step. It has a definition but no workflow input binding or
transition:

```yaml
flows:
  lookup_status:
    callable: true
```

A collection step selects an array of `{id, flow, input}` items, allowlists the
callable flow IDs, and applies a compiled item limit:

```yaml
type: flow_collection
items:
  pointer: /payload/items
flows:
  - lookup_status
  - prepare_guidance
max_items: 8
```

`max_items` defaults to 32 and accepts 1 through 1024. Item IDs must be unique;
every selected flow must be in the step allowlist. Items execute sequentially
under the root deadline and budgets. Collection nesting is bounded to 16 levels.
Callable flows cannot be route targets or the workflow start.

The runtime validates all item identities, selected flows, and child inputs
before child I/O begins. An empty list completes. A child review remains in the
ledger and execution continues; a technical child failure stops the collection
and marks remaining items skipped. `run_flow` is always a fresh isolated call,
so use a collection step when child calls must share one root execution.

The public step record has `kind: flow_collection`. Successful and review
results expose an ordered `result.items` ledger. On failure, the records produced
through failure remain in `partial_result.items`, including failed and skipped
items. Each item identifies its `id` and `flow` and contains the normal flow
status, steps, result when present, usage, and timing.

An LLM operation with a colocated output schema can be written as Markdown:

```markdown
---
type: llm
input:
  message:
    pointer: /payload/message
output:
  schema: output.schema.json
---
Extract the account reference from the supplied message.
```

Only `decision` and `llm` definitions accept Markdown bodies. Use the body or
an `instructions` field, not both. Keep a step's schemas beside that step when
they are specific to it; keep workflow and flow boundary schemas beside those
boundaries.

## Classify with an explicit fallback

A single-choice decision may define a process fallback for selected unresolved
issue codes:

```yaml
fallback:
  category:
    id: review
    description: Requests awaiting human review.
  "on":
    - no_supported_answer
```

The fallback category is not presented to the model and must not duplicate a
normal catalog ID. It applies only to a validated `not_answerable` result whose
issues are all listed in `on`. It does not handle timeouts, invalid output, or
undetermined results.

The native answer and issues remain unresolved. The public step record adds a
separate `selection` with `origin: fallback`; score native model correctness
and process selection separately. Route the containing flow's unresolved result
through `on_unresolved`.

## Bind only the required context

Bindings are explicitly tagged JSON values:

```yaml
fixed_channel:
  literal: email
current_message:
  pointer: /payload/message
accepted_metadata:
  pointer: /metadata/source
prior_step:
  pointer: /steps/extract/result/reference
completed_flow:
  pointer: /flows/classify/result/queue
optional_language:
  pointer: /payload/language
  optional: true
  default: en
```

The example shows a fixed value, current boundary input, accepted metadata, a
prior step, a completed flow, and a fallback used only when a pointer is
missing.

Pointers use RFC 6901: `~1` escapes `/` and `~0` escapes `~`. JSON
`null` is present, so it does not activate a missing-value default. Optional
pointers require an explicit default; required pointers cannot have one.

Each operation starts a fresh model conversation and receives only its declared
inputs. Foliqant does not forward the full envelope, prior prompts, messages, or
an implicit conversation history. A later operation can consume an earlier
result only through an explicit binding.

For a decision source, choose how its selected JSON value becomes evidence:

```yaml
sources:
  message:
    pointer: /payload/message
  account:
    pointer: /payload/account
    format: json
```

`format: text` is the default and requires a nonempty string. `format: json`
renders the selected value as canonical JSON, preserving structure without
inventing prose.

## Use prompt substitution deliberately

An LLM step may omit `prompt`; its declared inputs are then supplied as a JSON
object. To place selected inputs into a user prompt, use exact
`{{ name }}` placeholders:

```yaml
type: llm
input:
  message:
    pointer: /payload/message
  language:
    pointer: /payload/language
instructions: Return one concise sentence.
prompt: |
  Summarize {{ message }} in {{ language }}.
output: text
```

Every placeholder must name a declared input. Values are inserted once as
compact JSON: strings remain quoted, objects keep their structure, and template
syntax inside a value is never evaluated. `{{{{` and `}}}}` render literal
double braces. Expressions, missing names, and unmatched double braces are
rejected during compilation.

## Keep instructions and input separate

Business instructions, decision questions and criteria, output schemas, route
targets, and tool allowlists are authored configuration. Bound source values,
prompt substitutions, metadata, filenames, URLs, and prior model or tool results
are data.

The model adapters add a fixed policy that tells the provider to keep those
roles separate. Embedded requests in data cannot extend the task, permissions,
tools, or output contract. The policy still permits legitimate extraction,
classification, transformation, and quotation of instruction-like business
content when the authored task requires it.

For decisions, compiler-authored questions and criteria define the task.
`state.sources[].text` remains untrusted evidence, and derived summaries or
prior assessments remain claims rather than independent corroboration. This is a
defense boundary, not proof that a particular model will always comply. Validate
outputs and include adversarial cases in representative evaluations.

## Share and resolve schemas

`input_schema` and `output.schema` accept an inline JSON Schema object or a
local file path. References inside a schema file resolve relative to that file;
references in an inline schema resolve relative to the containing definition.
Remote resources, escaping paths, and schema `$id` declarations are rejected.

Compilation freezes the referenced files and binds their bytes to the compiled
revision. Execution does not reread them. The compiler checks graph
availability and provable type conflicts; runtime validation still handles
dynamic values and the parts of JSON Schema that static checks cannot prove.

## Inspect public results

The returned `ExecutionResult` has five roots:

```text
/payload
/metadata
/flows
/transitions
/execution
```

Flow records are under `/flows/{flow}`; their operation records are under
`/flows/{flow}/steps/{step}`. A projected flow value is
`/flows/{flow}/result`. The workflow's own `output` binding becomes the
top-level `payload`.

Within a running flow, authored bindings use the local `/steps/{step}/...`
scope. Workflow routing and output bindings use `/flows/{flow}/...`. Public
results never expose a separate flat `decisions` map.

## Validate before opening clients

```sh
foliqant validate
foliqant explain --workflow demo
foliqant doctor
```

These commands compile offline. Preparation validates model capabilities,
schema references, flow routes, bindings, MCP declarations, and trusted handler
contracts without contacting model or MCP endpoints. Successful preparation
does not establish endpoint availability, output quality, or future model
behavior.

In Python:

```python
from pathlib import Path

from foliqant import prepare_application

prepared = prepare_application(Path("config/settings.yaml"))
plan = prepared.plans["demo"]
print(plan.name, plan.start, plan.revision)
```

Compilation failures report a safe source location, field, reason, and hint
without echoing authored values or raw parser exceptions. Correct the explicit
source file and prepare again.

The [support triage example](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_triage/README.md)
shows a decision followed by schema extraction. The
[public-request example](https://github.com/sebastianwessel/foliqant/blob/main/examples/public_request_mcp/README.md)
shows a declared read-only MCP operation. Continue with
[testing and evaluation](testing-and-evaluation.md).
