# Build a workflow

A workflow is a local, reviewed graph executed in memory. A deployment names
its bundle directory; `workflow.yaml` defines its input, start step and output.
Keep a small workflow in one file, or put larger steps in separate YAML or
Markdown files. Both forms compile into the same immutable plan before clients
open or a request runs.

## Run a small inline workflow

Create a model-free project:

```sh
uv run --no-sync foliqant init /tmp/my-workflow
```

Its `foliqant.yaml` contains:

```yaml
version: 1
workflows:
  demo: workflows/demo
```

Replace `/tmp/my-workflow/workflows/demo/workflow.yaml` with this complete
workflow. It accepts an object containing a message and returns that message
as the result payload:

```yaml
version: 1
name: demo
start: done
input_schema:
  type: object
  properties:
    message: {type: string}
  required: [message]
  additionalProperties: false
output: {pointer: /payload/message}
steps:
  done:
    type: finish
    outcome: completed
```

Use the generated `envelope.json`, whose payload contains `message: hello`:

```sh
uv run --no-sync foliqant validate --config /tmp/my-workflow/foliqant.yaml
uv run --no-sync foliqant explain --config /tmp/my-workflow/foliqant.yaml
uv run --no-sync foliqant run --config /tmp/my-workflow/foliqant.yaml --workflow demo --input /tmp/my-workflow/envelope.json
```

The run returns `execution.status: completed` and `payload: "hello"`. It does not
need a model, credentials or network access. Without `output`, the result keeps
the accepted input payload.

Deployment paths are relative to `foliqant.yaml` and must stay below its
folder. Workflow schema paths are relative to the bundle directory. Names use
lowercase snake case, beginning with a letter. YAML duplicate keys, aliases,
custom tags and unknown configuration fields are rejected.

## Choose one step source

With inline steps, the keys below `steps` are the step IDs. Do not add a
separate `name` field inside those steps. `start` is always explicit; the order
of mapping keys or filenames never determines execution.

For separate files, remove `steps` from `workflow.yaml`. The equivalent
model-free bundle is:

```text
workflows/demo/
  workflow.yaml
  steps/
    done.yaml
```

`workflow.yaml`:

```yaml
version: 1
name: demo
start: done
```

`steps/done.yaml`:

```yaml
type: finish
outcome: completed
```

The filename supplies the step ID; a file may use an explicit `name` to override
it. Each ID must be unique. The compiler rejects a bundle that combines inline
`steps` with `steps/*.yaml` or `steps/*.md`; choose one form for the whole bundle.

| Step | Purpose | Successful transition |
| --- | --- | --- |
| `decision` | Ask native evidence-backed questions | `next`, or exhaustive `on_answer` routes for one choice, ordinal or predicate question |
| `llm` | Produce text or JSON matching an authored schema | `next` |
| `mcp` | Call a declared read-only tool | `next` |
| `handler` | Call a registered async Python function | `next` |
| `finish` | End with `completed` or `needs_review` | None |

Every successful operation needs an explicit transition. A missing `next` is
not an implicit successful finish. All targets must exist, every step must be
reachable from `start`, and cycles are rejected. Use a `finish` step to state
the intended terminal outcome.

An operation reporting uncertainty follows `on_unresolved` before any success
route. If `on_unresolved` is absent, it ends the run with `needs_review`. For a
decision, this prevents an unresolved answer from falling through to `next`.

## Share schemas and bind values

A workflow's `input_schema` accepts a JSON Schema object, as above, or a local
file path such as `schemas/input.json`. An LLM step's `output` accepts `text`
or `{schema: ...}`, where the schema value is likewise an object or file path.
Inline and file schemas use the same validation and frozen resource registry.

For example, after declaring an `extractor` model profile in
[deployment configuration](../reference/runtime-configuration.md), this complete
`workflow.yaml` extracts a message subject:

```yaml
version: 1
name: demo
start: extract
defaults: {model: extractor}
input_schema:
  type: object
  properties:
    message: {type: string}
  required: [message]
  additionalProperties: false
output: {pointer: /steps/extract/result}
steps:
  extract:
    type: llm
    input:
      message: {pointer: /payload/message}
    instructions: Extract a concise subject from the message.
    output:
      schema:
        type: object
        properties:
          subject: {type: string}
        required: [subject]
        additionalProperties: false
    next: done
  done:
    type: finish
    outcome: completed
```

To share its output schema, move the object under `output.schema` into
`schemas/subject.json` and change that field to `schema: schemas/subject.json`.
Schemas may refer to other local resources with `$ref`. References inside a
schema file resolve relative to that file; references in an inline schema
resolve relative to the bundle directory. A fragment such as `#/$defs/subject`
refers to that schema's own definitions. Remote resources, escaping paths and
schema `$id` declarations are rejected. Compilation freezes the resources and
binds their exact file bytes to the revision; running a prepared workflow does
not reread them.

Bindings are explicitly tagged values:

| Binding | Meaning |
| --- | --- |
| `{literal: "email"}` | Use a fixed JSON value |
| `{pointer: /payload/message}` | Read accepted input |
| `{pointer: /metadata/source}` | Read accepted metadata |
| `{pointer: /steps/extract/result/subject}` | Read a step result |
| `{pointer: /payload/language, optional: true, default: "en"}` | Use the default only when the pointer is missing |

Pointers use RFC 6901: `~1` escapes `/` and `~0` escapes `~` in a key. JSON `null`
is a present value, so it never activates a missing-value default. Optional
pointers require an explicit `default`; a required pointer cannot have one.

A required prior-step binding must name a step that runs on every graph path
to its consumer. Use an optional binding when a branch may skip the producer.
Similarly, an output projection must be available at every possible terminal,
including an earlier operation's implicit `needs_review` outcome, or have an
explicit default. Schemas may describe a property without requiring it; actual
missing values still fail at runtime unless the binding has a default.

## Write instructions in Markdown

For a separate `steps/extract.md`, put step configuration in YAML frontmatter
and instructions in the body. A complete step file replacing `extract` from the
previous example is:

```markdown
---
type: llm
model: extractor
input:
  message: {pointer: /payload/message}
output: text
next: done
---
Extract a concise subject from the message.
```

Only `decision` and `llm` steps accept a Markdown body. Use either the body or an
`instructions` field; providing both is an error. Keep the matching `done`
finish step and remove the inline `steps` mapping when moving to step files.

## Validate before opening clients

The CLI's `validate`, `explain` and `doctor` commands are offline. `doctor` also
reports whether selected optional dependencies are installed. In Python, use
the public preparation entry point:

```python
from pathlib import Path

from foliqant import prepare_application

prepared = prepare_application(Path("/tmp/my-workflow/foliqant.yaml"))
plan = prepared.plans["demo"]
print(plan.name, plan.start, plan.revision)
```

Preparation validates declared model capabilities, schema references, graph
routes and bindings without contacting model or MCP endpoints. Registered
handler schemas and declared MCP schemas also let it check required input keys,
forbidden fields and obviously incompatible JSON types. It rejects mandatory
paths proven impossible by known schemas, such as a missing property in a
closed object or a child of a string. An optional binding to an absent path
keeps its explicit fallback.

These are conservative checks, not a proof that arbitrary JSON Schemas are
compatible. Open schemas, conditional/composite schemas, overlapping union
types, value constraints and actual property presence still need runtime
validation. Successful preparation does not establish endpoint availability,
model quality or the validity of future responses. External tool and model
outputs remain independently validated during execution.

Compilation failures include a stable `reason`, bundle-relative source
location, a field when available, and a corrective `hint`. For example,
`missing_transition` points to `next`; `mixed_step_sources` points to `steps`.
Diagnostics omit rejected values and raw parser or validation exceptions.
A `*` in a field path represents an authored mapping key that was omitted from
the diagnostic. Correct the source file and prepare again.

The [support triage bundle](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_triage/workflow.yaml)
shows decision routing followed by schema extraction. The
[public-request bundle](https://github.com/sebastianwessel/foliqant/blob/main/examples/public_request_mcp/workflow.yaml)
shows a declared MCP call. Continue with
[testing and evaluation](testing-and-evaluation.md) to check your own workflow.

## Keep policy outside prompts

The workflow fixes route targets and tool allowlists. Models cannot create
steps or grant tool permissions. The embedding application owns caller
authentication and resource authorization. Current integrations are read-only;
mutating business actions belong in the application's own authorization and
reconciliation flow.
