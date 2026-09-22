# Map a business process to workflows, flows, and operations

Use this reference when translating requirements into runtime configuration.
Confirm the result with the installed `foliqant validate` and
`foliqant explain` commands. For programmatic contract inspection, use the
installed `foliqant.contracts.workflow` models or
`foliqant.contracts.schemas.runtime_schemas()`.

## Contents

- [Choose boundaries](#choose-boundaries)
- [Worked mapping](#worked-mapping-support-intake)
- [File conventions](#file-conventions-and-customization)
- [Workflow fields](#workflow-fields)
- [Bindings and scope](#bindings-and-scope)
- [Routing and unresolved outcomes](#routing-and-unresolved-outcomes)
- [Operation fields](#operation-fields)
- [Schemas and Markdown](#schemas-and-markdown)
- [Trust and evaluation](#trust-and-evaluation)

## Choose boundaries

Use one **workflow** for one externally invoked business capability with one
input contract and one final payload. Use a **flow** when the process crosses a
named sequential boundary that needs its own input, output, review behavior, or
route. Use a **step** for one independently executable model, tool, or trusted
code operation.

Work through the process in this order:

1. Define the workflow input schema and final output projection.
2. Identify sequential boundaries with independently meaningful inputs/outputs.
3. Put each boundary in one flow and order its operations explicitly.
4. Bind only the context each flow and operation needs.
5. Route completed flow results and unresolved outcomes explicitly.
6. Add pipeline gold for business behavior, then flow and step gold for
   diagnosis.

Do not create a flow merely to wrap every step. A flow should express a business
boundary, reusable sequence, or independently testable responsibility.

## Worked mapping: support intake

Suppose an application accepts one support message, assigns exactly one queue,
extracts a customer reference, and looks up the account only for a supported
billing request. Map the process before writing files:

| Requirement | Runtime boundary | Reason |
| --- | --- | --- |
| Accept and finish one support request | `support_intake` workflow | This is the capability invoked by the host and the final result it returns. |
| Classify and extract from the same message | `triage` flow with `classify`, then `extract` steps | Both operations share one sequential boundary; extraction can consume only explicitly bound context. |
| Read an account from a remote system | `account_lookup` flow with one MCP step | This is a separate capability and failure/review boundary with its own resolved input. |
| Unsupported, conflicting, or multi-queue request | `triage.on_unresolved -> needs_review` | Review is explicit and cannot fall through to account access. |
| Billing route with an account reference | `triage.transition -> account_lookup` | The flow route, rather than a model step, controls whether the tool may run. |
| Other supported queues | terminal `completed` or another authored flow | Every case and default is visible in the workflow graph. |

The workflow file owns that graph:

```yaml
name: support_intake
start: triage
defaults:
  model: local
input_schema: input.schema.json
output:
  pointer: /flows/triage/result
  optional: true
  default:
    status: needs_review
flows:
  triage:
    input:
      message:
        pointer: /payload/message
    transition:
      binding:
        pointer: /flows/triage/result/queue
      cases:
        billing:
          flow: account_lookup
        cancellation:
          outcome: completed
      default:
        outcome: needs_review
    on_unresolved:
      outcome: needs_review
  account_lookup:
    input:
      account_id:
        pointer: /flows/triage/result/account_id
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
```

The `triage` flow declares its order explicitly:

```yaml
steps:
  - classify
  - extract
```

The filesystem does not infer this list. Give the choice question at least two
described categories and criteria that distinguish billing from cancellation.
Bind the lookup step only to the extracted account ID. Add a pipeline case
proving the tool flow is skipped for cancellation, a triage-flow case for review
handling, and a classify-step case for each category. Gold comes from business
review, not from a previous model response.

## File conventions and customization

```text
config/
  settings.yaml
  intake/
    workflow.yaml
    input.schema.json
    triage/
      flow.yaml
      classify.step.md
      extract/
        step.md
        output.schema.json
```

- Omitting `workflows` discovers immediate nonhidden
  `config/*/workflow.yaml`; the directory supplies `name`.
- A sole flow permits omitted `start`; multiple flows require it.
- Omitting `flows.<id>.definition` resolves `<id>/flow.yaml`.
- A shorthand step ID resolves exactly one `<id>.step.md|yaml` or
  `<id>/step.md|yaml`.
- Missing or multiple candidates fail. Filesystem order never defines behavior.
- Explicit paths and inline flow/step definitions remain valid customization.

## Workflow fields

| Field | Shape | Rule |
| --- | --- | --- |
| `name` | ID, optional | Defaults to workflow directory |
| `start` | flow ID, optional | Inferred only for exactly one flow |
| `defaults.model` | model profile ID, optional | Default for decision/LLM operations |
| `input_schema` | JSON Schema object or local path, optional | Validates workflow payload |
| `output` | binding, optional | Projects final top-level payload |
| `flows` | nonempty map | Each key is a flow instance ID |

Each flow instance has:

| Field | Shape | Rule |
| --- | --- | --- |
| `definition` | inline flow or local path, optional | Convention is `<flow>/flow.yaml` |
| `input` | binding map | Resolves the flow payload |
| `transition` | target or match route | Required completed-flow route |
| `on_unresolved` | target or unresolved route, optional | Defaults to terminal review |

A flow definition has optional `input_schema`, optional `output`, and a
nonempty ordered `steps` list. Step IDs are unique within the flow.

## Bindings and scope

A binding contains either `literal` or `pointer`. Optional pointers also
declare their default:

```yaml
fixed_value:
  literal: <any JSON>
required_value:
  pointer: /payload/value
optional_value:
  pointer: /payload/value
  optional: true
  default: <any JSON>
```

Optional pointers require an explicit default. Required pointers cannot have a
default. JSON null is present and does not trigger a default.

At workflow scope use `/payload`, `/metadata`, and completed
`/flows/{flow}`. At flow scope use `/payload`, `/metadata`, and preceding
`/steps/{step}`. Public results use
`/flows/{flow}/steps/{step}`.

Every model step starts a fresh conversation. There is no implicit history or
full-envelope forwarding.

## Routing and unresolved outcomes

A direct target names a flow or terminal outcome:

```yaml
transition:
  flow: next_flow
# or
transition:
  outcome: completed
```

Exact match routing uses:

```yaml
transition:
  binding:
    pointer: /flows/triage/result/queue
  cases:
    billing:
      flow: billing
  default:
    outcome: needs_review
```

An unresolved route can be one target or:

```yaml
on_unresolved:
  no_supported_answer:
    flow: clarify
  conflicting_information:
    outcome: needs_review
  multiple_valid_options:
    outcome: needs_review
  default:
    outcome: needs_review
```

Unresolved routes cannot directly complete a workflow. Do not parse public
reason text to route.

## Operation fields

### Decision

```yaml
type: decision
model: local                   # optional profile/override/inline profile
sources:
  message:
    pointer: /payload/message
  account:
    pointer: /payload/account
    format: json
instructions: Apply the question to the supplied evidence.
question: # use questions for two or more full DecisionQuestion objects
  type: choice
  criteria:
    - Choose exactly one supported category.
  catalog:
    categories:
      - id: billing
        description: A billing request.
      - id: cancellation
        description: A request to cancel an active service.
fallback:                      # single choice only
  category:
    id: review
  "on":
    - no_supported_answer
```

Single-question shorthand supports:

- `choice`: `criteria`, `catalog` with at least two categories;
- `multiselect`: `criteria`, `catalog`, `minSelections`,
  `maxSelections`;
- `predicate`: `criteria`;
- `ordinal`: `criteria`, ordered `levels`;
- `request_units`: `criteria`, `catalog`, `allowNoMatch`.

Use `questions` for two or more full native decision questions. Source
`format` is `text` by default or `json`.

Source values are pointer or literal bindings plus optional `format`:

```yaml
sources:
  message:
    pointer: /payload/message
  account:
    pointer: /payload/account
    format: json
  channel:
    literal: email
```

`text` requires a nonempty string. `json` renders any selected JSON value
canonically. Source IDs are unique map keys.

Shorthand catalogs contain a `categories` sequence whose entries each have
`id` and `description`. A choice requires at least two categories. A
multiselect requires a nonempty catalog,
`minSelections >= 0`, and
`1 <= maxSelections <= category count`, with minimum no greater than maximum.
Category IDs normalize to lowercase ASCII snake case, must begin with a letter,
and must be unique; descriptions are nonempty authored semantics. Ordinal
`levels` contain at least two ordered objects with `id` and `description`.
The
shorthand question receives the operation's question identity and all declared
sources.

Every item in full `questions` has common fields:

| Field | Meaning |
| --- | --- |
| `id` | Unique question ID |
| `type` | `choice|multiselect|predicate|ordinal|request_units` |
| `prompt` | Nonempty question text |
| `criteria` | Nonempty unique rule strings |
| `allowedSourceIds` | Nonempty unique subset of declared source IDs |

Type-specific full-question fields are:

| Type | Additional fields |
| --- | --- |
| `choice` | `options`: at least two unique values with `id` and `description` |
| `multiselect` | nonempty `options`, `minSelections`, `maxSelections` |
| `predicate` | none |
| `ordinal` | at least two ordered unique `levels` |
| `request_units` | `catalog`: unique values with `id` and `description`; `allowNoMatch` |

For multiselect, `0 <= minSelections <= maxSelections <= option count`.
`questions` requires at least two entries in runtime authoring; use
`question` for the single-question shorthand.

A fallback is supported only with shorthand `choice`. It contains a category
with required `id` and optional `description` outside the normal catalog,
plus a unique nonempty `on`
list drawn from `no_supported_answer`, `conflicting_information`, and
`multiple_valid_options`. It selects a process category only when the validated
unresolved issues are all covered; it does not rewrite the native answer.

### LLM

```yaml
type: llm
model:
  profile: local
  options:
    max_tokens: 800
    temperature: 0
input:
  message:
    pointer: /payload/message
instructions: Return a concise structured extraction.
prompt: Extract from {{ message }}.   # optional
output:
  schema: output.schema.json         # or output: text
tools:                               # optional
  server: records
  allow:
    - lookup
  choice: auto # also accepts a mapping with name: lookup
```

Prompt placeholders must exactly name declared inputs. Values render as compact
JSON once. `{{{{` and `}}}}` produce literal double braces; expressions are
invalid.

### MCP

```yaml
type: mcp
server: records
tool: lookup
arguments:
  reference:
    pointer: /payload/reference
```

The server, tool, argument schema, result schema, and read effect must be
declared in deployment settings:

```yaml
input_schema:
  type: object
output_schema:
  type: object
effect: read
```

`output_schema` may be omitted, while `input_schema` and `effect` are
required. The current runner rejects `effect: write`.

### Trusted handler

```yaml
type: handler
handler: normalize
input:
  value:
    pointer: /payload/value
```

YAML selects only a host-registered handler name. Register an async callable with
explicit input/output schemas and `effect: read`. The current runner rejects
write handlers.

## Schemas and Markdown

Boundary and LLM output schemas accept inline objects or local paths. Resolve
paths from the declaring file and colocate operation-specific schemas with the
operation. Local `$ref` is supported; remote resources, escaping paths, and
`$id` are rejected.

Decision and LLM Markdown files use YAML frontmatter plus body instructions.
Providing both body text and an `instructions` field is ambiguous and fails.

## Trust and evaluation

Questions, criteria, instructions, schemas, routes, and tool allowlists are
authored authority. Bound values and prior outputs are data. Preserve the fixed
adapter policy and local output validation.

For every business workflow author:

- pipeline cases for final status, payload, transitions, and key intermediate
  observations;
- flow cases with resolved flow inputs;
- step cases with resolved operation inputs;
- unresolved, conflicting, multi-option, and malformed-dependency cases that
  matter to the process.

Gold must be independently reviewed. An application may keep small authored
synthetic fixtures in its own `evaluation/` directory; generated reports,
customer data, and private gold remain private.
