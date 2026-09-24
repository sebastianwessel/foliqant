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

## Set defaults and the starting flow

The top-level workflow fields are:

| Field | Required | Default and effect |
| --- | --- | --- |
| `name` | No | Workflow directory name; if present, it must match the deployment registry key |
| `input_schema` | No | No workflow-level payload schema |
| `defaults.model` | No | No model; every `decision` or `llm` step must otherwise select one |
| `defaults.on_unresolved` | No | Review route inherited by every routed flow without its own `on_unresolved` |
| `start` | Sometimes | Inferred only when exactly one non-callable flow exists |
| `output` | No | Return the accepted workflow input payload |
| `flows` | Yes | Nonempty map of routed instances and callable flows |

Model-backed steps can inherit a deployment profile. A flow definition may set
its own `defaults.model`, which takes precedence for that flow's steps:

```yaml
defaults:
  model: local
start: classify
```

### Choose the first flow

Declare `start` whenever two or more routed flows exist. A callable flow cannot
be the start. `start` names one flow, or selects it from the envelope with an
ordered `route`:

```yaml
start:
  route:
    - when:
        binding:
          pointer: /payload/form/report_type
        present: true
      flow: extract_fields
    - flow: classify_report_type
```

Start conditions read only `/payload` and `/metadata`; every entry targets a
flow, never an outcome, so a run always executes at least one flow. The
compiler rejects cycles and any flow that cannot be reached from a start
candidate through a route, a collection call or a repeat retry.

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
workflow-boundary `input`, `transition`, or `on_unresolved`; an allowlisted
`flow_collection` step invokes one with a complete child input, and a
[`repeat`](repeat.md) may run one as its retry flow. See
[Flow collection steps](../steps/flow-collection.md).

A routed flow may also declare [`repeat`](repeat.md) to run a bounded number
of attempts, optionally with a callable retry flow between them.

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
explicit with a `default`. A binding with `default` is optional; one without is
required:

```yaml
output:
  pointer: /flows/respond/result
  default:
    disposition: review
```

Defaults apply only when the pointer is missing. An explicit JSON `null` is a
present value and remains `null`.

After alternative branches, `first_of` returns the result of whichever branch
ran, without a join flow or handler:

```yaml
output:
  first_of:
    - pointer: /flows/billing/result
    - pointer: /flows/cancellation/result
  default:
    disposition: needs_review
```

`first_of` selects the first member that resolves to a non-null value. An
object output assembles several values into one payload with exactly these
keys; each field is any binding:

```yaml
output:
  fields:
    queue:
      pointer: /flows/classify/result
    reply:
      pointer: /flows/respond/result/reply
      default: null
```

See [context](context.md#combine-candidates-and-build-objects) for the rules.

## Define transitions

Every routed flow requires a `transition`, used after the flow completes. A
direct transition selects another flow or ends the workflow:

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

### Match an exact value with `cases`

For branching on one enumerated string, match it exactly:

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

Case keys compare exactly. A missing binding with a default, `null`, or an
unmatched string uses `default`; other JSON types are invalid. The model never
invents a route, and there is no expression evaluator or coercion.

The compiler derives the **allowed values** of the routed field from its static
schema: `enum` and `const` (also inside `anyOf`/`oneOf` with `null`), decision
selections (catalog IDs plus the fallback category), predicate answers, and the
declared output schemas of handlers, MCP tools and LLM steps, through object
outputs. Then:

| Code | Level | When |
| --- | --- | --- |
| `unmatched_case` | error | a case key is not an allowed value, so the case can never be taken |
| `uncovered_value` | warning | an allowed value has no case and silently falls to `default` |
| `default_covers_mismatch` | error | `default_covers` does not list exactly the uncovered values |
| `case_on_unknown_type` | info | the field's values are unknown, so cases cannot be checked |

State deliberately uncovered values to silence the warning:

```yaml
transition:
  binding:
    pointer: /flows/lookup_fund/result/status
  cases:
    found:
      flow: enrich
  default:
    flow: request_details
  default_covers:
    - not_found
    - ambiguous
```

### Route on conditions

Use an ordered `route` when the decision is not one exact value: presence,
several fields, patterns, counts. Each entry but the last has a
[condition](conditions.md) in `when`; the first true entry wins; the last entry
has no `when` and is the mandatory otherwise target:

```yaml
transition:
  route:
    - when:
        binding:
          pointer: /flows/extract_fields/result/status
        equals: invalid
      flow: repair_extraction
    - when:
        all:
          - binding:
              pointer: /payload/form/report_type
            present: true
          - binding:
              pointer: /payload/form/report_type
            not_equals: custom_report
      flow: extract_fields
    - flow: classify_report_type
```

Route conditions read `/payload`, `/metadata` and `/flows/{id}/result` of any
flow that may have run before this point; absence is tolerated. Targets are
flows or outcomes, as in `cases`. A `route` whose last entry has `when` fails
with `route_without_otherwise`; an entry without `when` before the last fails
with `misplaced_otherwise`. Entries that can never be selected report
`route_unreachable_entry`.

Prefer `cases` for one enumerated value (it gets coverage checks) and `route`
for everything else. Neither needs a handler whose only job is to compute a
routing key.

## Handle review

Steps execute sequentially. If a step produces a valid unresolved result, the
flow stops and uses `on_unresolved` instead of `transition`. Without a review
route the workflow ends with `needs_review`; validation reports each such flow
with the `review_ends_run` info diagnostic, naming what the host then receives:
the workflow output resolved from the flows that ran, or its default when none
of the flows it binds can have run.

Use one target when all uncertainty follows the same path:

```yaml
on_unresolved:
  flow: manual_review
```

Or route supported review issue codes explicitly:

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
`multiple_valid_options`. Decision steps and trusted handlers report issues;
when several issues select different targets, the required `default` wins, and
a review without issues also uses the default.

### Route review

`on_unresolved` accepts the same ordered `route` form as `transition`:

```yaml
on_unresolved:
  route:
    - when:
        binding:
          pointer: /payload/channel
        equals: portal
      flow: portal_review
    - outcome: needs_review
```

### Inherit a workflow review default

Set `defaults.on_unresolved` once instead of repeating it on every flow. A flow
with its own `on_unresolved` keeps it:

```yaml
defaults:
  model: local
  on_unresolved:
    flow: manual_review
flows:
  manual_review:
    input: {}
    transition:
      outcome: needs_review
    on_unresolved:
      outcome: needs_review
```

The target flow must be reachable from every inheriting flow without creating
a cycle, so it normally declares its own review route; otherwise compilation
fails with `invalid_default_review_route`. A review route cannot target its own
flow (`review_route_to_self`) or go directly to `outcome: completed`, but it may
enter an explicit review-handling flow that later completes. Earlier review
records remain visible in the final result. Technical failure never follows
`on_unresolved`.

All route targets must exist, routed flows cannot target callable flows, and the
complete graph must be acyclic. To retry a flow a bounded number of times, use
[`repeat`](repeat.md) instead of unrolling copies of it.

## Validate the graph offline

```sh
foliqant validate --config config/settings.yaml
foliqant validate --strict --config config/settings.yaml
foliqant explain --config config/settings.yaml --workflow support_intake --format mermaid
```

Validation checks start selection, reachability, cycles, transitions, route
coverage, conditions, known binding paths, and provable schema compatibility
without calling a model or tool. Warnings and infos are listed in the
`diagnostics` of the output; `--strict` fails on any warning. `explain` renders
the graph as JSON, Mermaid or Graphviz. None of this proves that business
categories, route cases, or review policy are correct. Exercise those with
reviewed cases from the [evaluation guides](../evaluation/index.md).

Continue with [Flows](flows.md) to define each sequential boundary or
[Context](context.md) to understand exactly which values its bindings can see.
