# Configure workflows

A workflow is one application-facing capability. Its `workflow.yaml` owns the
accepted payload shape, the starting flow, all routed and callable flow
instances, deterministic transitions, review handling, and the final public
payload projection.

## Define the public input schema

Set `input_schema` to an inline JSON Schema object or a local schema file:

```yaml
# config/support_intake/workflow.yaml
input_schema: input.schema.json
```

The path is relative to the workflow directory. If `input_schema` is omitted,
the workflow accepts any finite JSON payload. A schema makes invalid caller
input fail before a flow or external dependency runs, so prefer a closed object
for stable application contracts:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "message": {"type": "string", "minLength": 1, "maxLength": 4000},
    "language": {"type": "string", "enum": ["en", "de"]}
  },
  "required": ["message", "language"],
  "additionalProperties": false
}
```

Workflow-boundary pointers can read the original `/payload`, accepted
`/metadata`, and results of previously completed routed flows under
`/flows/{flow}/result`.

## Set the default model and starting flow

The top-level workflow fields are:

| Field | Required | Default and effect |
| --- | --- | --- |
| `name` | No | Workflow directory name; if present, it must match the deployment registry key |
| `input_schema` | No | No workflow-level payload schema |
| `defaults.model` | No | No model; every `decision` or `llm` step must otherwise select one |
| `start` | Sometimes | Inferred only when exactly one non-callable flow exists |
| `output` | No | Return the accepted workflow input payload |
| `flows` | Yes | Nonempty map of routed instances and callable flows |

Model-backed steps can inherit a deployment profile:

```yaml
defaults:
  model: local
start: classify
```

Declare `start` whenever two or more routed flows exist. A callable flow cannot
be the start. The compiler rejects cycles and any flow that cannot be reached
from the start through a route or a collection call.

## Declare flow instances

A routed flow instance binds workflow-boundary data into the flow, names its
definition, and defines what happens after successful completion:

```yaml
flows:
  classify:
    definition: classify/flow.yaml
    input:
      message:
        pointer: /payload/message
      language:
        pointer: /payload/language
    transition:
      flow: respond
```

`definition` is optional. For flow `classify`, omission resolves
`classify/flow.yaml` beside `workflow.yaml`. `input` is required, including
`input: {}` when the flow deliberately receives an empty object. Every required
property in the flow's input schema must have a binding.

A callable flow has a smaller declaration:

```yaml
flows:
  lookup_status:
    callable: true
```

It resolves `lookup_status/flow.yaml` by convention. Callable flows have no
workflow-boundary `input`, `transition`, or `on_unresolved`; only an allowlisted
`flow_collection` step can invoke one with a complete child input. See
[Flow collection steps](../steps/flow-collection.md).

## Project the workflow output

Use `output` to select the value returned as the top-level result `payload`:

```yaml
output:
  pointer: /flows/respond/result
```

Without `output`, the accepted workflow input payload is returned. An output
may also be a fixed JSON value:

```yaml
output:
  literal:
    accepted: true
```

When a referenced flow does not run on every terminal path, make absence
explicit:

```yaml
output:
  pointer: /flows/respond/result
  optional: true
  default:
    disposition: review
```

Defaults apply only when the pointer is missing. An explicit JSON `null` is a
present value and remains `null`.

## Define transitions and review handling

Every routed flow requires a `transition`. A direct transition selects another
flow or ends the workflow:

```yaml
transition:
  flow: respond
```

```yaml
transition:
  outcome: completed
```

```yaml
transition:
  outcome: needs_review
```

For deterministic branching, match an exact string flow result:

```yaml
transition:
  binding:
    pointer: /flows/classify/result
  cases:
    billing:
      flow: billing
    cancellation:
      flow: cancellation
  default:
    outcome: needs_review
```

Case keys compare exactly. A missing optional binding, `null`, or unmatched
string uses `default`; other JSON types are invalid. The model never invents a
route, and there is no expression evaluator or coercion.

Steps execute sequentially. A completed flow uses `transition`. If a step
produces a valid unresolved result, the flow stops and uses `on_unresolved`.
Without that field, the workflow ends with `needs_review`.

Use one unresolved target when all uncertainty follows the same path:

```yaml
on_unresolved:
  flow: manual_review
```

Or route supported decision issue codes explicitly:

```yaml
on_unresolved:
  no_supported_answer:
    flow: request_details
  conflicting_information:
    flow: manual_review
  default:
    outcome: needs_review
```

The supported keys are `no_supported_answer`, `conflicting_information`, and
`multiple_valid_options`. When several issues select different targets, the
required `default` wins. Missing issues and non-decision review also use the
default. An unresolved route cannot go directly to `outcome: completed`, but it
may enter an explicit review-handling flow that later completes. Earlier review
records remain visible in the final result. Technical failure does not follow
`on_unresolved`.

All route targets must exist, routed flows cannot target callable flows, and the
complete graph must be acyclic.

## Validate the graph offline

```sh
foliqant validate --config config/settings.yaml
foliqant explain --config config/settings.yaml --workflow support_intake
```

Validation checks start selection, reachability, cycles, transitions, known
binding paths, and provable schema compatibility without calling a model or
tool. It does not prove that business categories, route cases, or review policy
are correct. Exercise those with reviewed cases from the
[evaluation guides](../evaluation/index.md).

Continue with [Flows](flows.md) to define each sequential boundary or
[Context](context.md) to understand exactly which values its bindings can see.
