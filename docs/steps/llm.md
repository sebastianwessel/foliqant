# Configure an LLM step

An `llm` step runs one bounded model conversation over named inputs. Use it for
free text or schema-constrained extraction. Use a [`decision`](decision.md)
step when the task fits its typed business-question contracts.

## Produce text

```yaml
# config/briefing/summarize/summarize.step.yaml
type: llm
input:
  message:
    pointer: /payload/message
instructions: Return one concise sentence.
prompt: |
  Summarize {{ message }}.
output: text
```

`input` is required and may be empty. Without `prompt`, the selected inputs are
sent as a compact JSON object. With `prompt`, every `{{ name }}` placeholder
must name a declared input. Values are inserted once as compact JSON, so strings
remain quoted and template syntax inside a value is not evaluated. `{{{{` and
`}}}}` render literal double braces.

## Produce schema-constrained JSON

Set `output.schema` to an inline JSON Schema or a local path. This runnable form
is used by the [structured extraction tutorial](../tutorials/structured-extraction.md):

```markdown
---
type: llm
input:
  message:
    pointer: /payload/message
output:
  schema: output.schema.json
---
Extract the currently active requested action, any deadline, and the account
reference using only facts stated in the message. Use null for an absent
deadline or account reference.
```

Place `output.schema.json` beside this definition. Local `$ref` resources are
resolved and frozen at compilation. Remote references, escaping paths, and
schema `$id` declarations are rejected. The runtime validates the model output
against the schema before recording it.

## Select the model

The optional `model` field overrides `workflow.yaml` `defaults.model`:

```yaml
model:
  profile: local_qwen
  options:
    max_tokens: 4096
```

Omitted override fields retain the profile values. Preparation verifies that
the profile supports text or JSON Schema output as required. Provider, endpoint,
credentials, output mode, timeouts, and retry policy belong in
[`config/settings.yaml`](../configuration/models.md), not in prompt data.

## Keep task and data separate

`instructions`, output schema, and optional prompt template define the task.
Bound values and earlier results are data. Each step starts a fresh conversation;
there is no implicit message history and no automatic forwarding of the full
envelope. Bind an earlier result explicitly:

```yaml
input:
  reference:
    pointer: /steps/extract/result/reference
```

Prompt-role separation and output validation reduce risk but do not prove model
correctness. Do not interpolate secrets into inputs or instructions.

## Understand results and failures

On success, the step `result` is the returned string or validated JSON value.
Provider refusals, truncated output, malformed structured output, and schema
violations fail with `invalid_output`. Deadline exhaustion returns `timeout`;
request-budget exhaustion returns `budget_exhausted`; unavailable providers
return a safe dependency error. The runtime never converts these failures into
business review.

An LLM step requests review only when an attached MCP tool reports that caller
input is required. For model-selected tools, continue with
[bounded agent loops](agent-loops.md). Evaluate extraction fields and spans with
[task-specific metrics](../evaluation/task-types.md), not structural validation
alone.
