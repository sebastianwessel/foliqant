# Let the model use read-only tools

An agent loop is an `llm` step with a `tools` policy. The model can request one or several allowlisted read-only MCP calls, inspect validated results, and produce the step's final **JSON or text** output. It remains one step in a sequential flow: the model cannot choose another flow, add tools, grant access, or persist memory.

First configure the model profile and MCP server in `config/settings.yaml`. The model must declare `supports_tools: true` and support the chosen output kind. The MCP profile must declare each tool's exact input schema, optional output schema, and `effect: read`. See [models](../configuration/models.md) and [MCP setup](../configuration/mcp.md).

## One lookup, JSON answer

List `answer` in `config/support_triage/triage/flow.yaml`. Save this step in `config/support_triage/triage/answer.step.yaml`, with a local `answer.schema.json` defining the required `status` string:

```yaml
type: llm
input:
  message:
    pointer: /payload/message
instructions: Use the lookup to answer the customer's invoice-status question. Do not invent a status.
output:
  schema: answer.schema.json
tools:
  server: support_records
  allow:
    - lookup_invoice
  choice: required
max_iterations: 2
```

`config/support_triage/triage/answer.schema.json` can contain:

```json
{
  "type": "object",
  "properties": {"status": {"type": "string"}},
  "required": ["status"],
  "additionalProperties": false
}
```

For a successful lookup, the public step can contain `"status": "completed"` and `"result": {"status": "paid"}`, where the object is validated against your schema. `choice: required` means at least one allowed tool call must succeed before final output. For exactly one named tool, set `choice:` to a mapping with `name: lookup_invoice`. With `choice: auto`, the model may answer without calling a tool.

## Several allowed lookups, text answer

The same server policy may allow several tool names. The model selects among them and may make more than one call while limits remain:

```yaml
# config/support_triage/triage/draft.step.yaml
type: llm
input:
  message:
    pointer: /payload/message
instructions: Answer using verified invoice or subscription records only.
output: text
tools:
  server: support_records
  allow:
    - lookup_invoice
    - lookup_subscription
  choice: required
max_iterations: 3
```

A completed `result` here is a string, for example `"Invoice INV-42 is paid; the subscription remains active."` Tool calls emitted together execute sequentially. A step policy names **one MCP server**, though it may allow several tools from that server. Use separate steps for different servers. The `allow` list must be nonempty and unique; named choice must be on it.

## Set the limits where they apply

`max_iterations` belongs on the LLM step. It counts logical model turns, including the final answer, and defaults to **4** (allowed range 1–1024). The runtime stops before a fifth turn at the default; a final answer on turn four succeeds. Provider retries of one turn do not consume another iteration. This limit also applies to LLM steps with no tools.

`execution` in `config/settings.yaml` supplies separate local limits:

```yaml
execution:
  model_requests_per_step: 4
  tool_calls_per_step: 3
  run_timeout: 300
  model_timeout: 60
  tool_timeout: 30
```

These are the defaults. `model_requests_per_step` counts actual provider attempts, including retries; `tool_calls_per_step` counts tool attempts. Attempts are reserved before I/O and failures still consume them. The root `run_timeout` is an absolute invocation deadline. `model_timeout` bounds a logical model request after its first admission, shared by that request's retries; `tool_timeout` bounds tool work. A model profile also has its own `request_timeout`, admission and retry policy, and an MCP profile has call/admission/output limits. The earliest applicable deadline wins. See [execution limits](../configuration/limits.md), [models](../configuration/models.md), and [MCP setup](../configuration/mcp.md).

The iteration and attempt limits are independent. For example, `max_iterations: 3` permits three model turns, but the default four provider attempts may be exhausted sooner if a request retries. Conversely, raising the provider-attempt budget does not let a loop exceed three turns. A tool call consumes no extra model iteration by itself; the model turn after the tool result does.

If the MCP server requests caller input, the step returns `needs_review` with `result: null`. If a required or named call never succeeds, final validation fails with `invalid_output`. Exceeding an iteration or attempt limit fails with `budget_exhausted`; invalid arguments, authorization denial, catalog drift, and timeouts are technical failures. Each invocation starts with fresh model/tool state. Evaluate tool selection and final answers with [reviewed cases](../evaluation/task-types.md); the [model tool-loop tutorial](../tutorials/model-tool-loop.md) has an offline scripted example.
