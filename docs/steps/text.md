# Write a text response

Use an `llm` step with `output: text` for a support-email summary, draft, or other prose. The result is a string. It is not a decision with issue codes, and the runtime does not claim that every sentence is supported.

List `summarize` in `config/support_triage/triage/flow.yaml`, then save:

```yaml
# config/support_triage/triage/summarize.step.yaml
type: llm
input:
  message:
    pointer: /payload/message
instructions: Write one concise sentence describing the requested support action.
output: text
```

For “Please send invoice INV-42,” the public record at `/flows/triage/steps/summarize` may contain:

```json
{"status": "completed", "result": "The customer requests a copy of invoice INV-42."}
```

The precise wording varies by model. The step needs a model with text support, selected through the workflow's `defaults.model` or this step's `model`. `input` is required and can be empty. With no `prompt`, the named inputs are sent as compact JSON. To guide presentation, add a template:

```yaml
prompt: |
  Summarize this support email in one sentence: {{ message }}
```

Every placeholder must name a declared input. Values are JSON-rendered once, so source text cannot create new template placeholders. Bound values and earlier step results are data; bind them explicitly when needed. A Markdown step can use its body for instructions instead of the YAML field. See [JSON response](llm.md) for a schema-constrained result.

`max_iterations` is an optional step field, default 4 (1–1024), counting logical model turns including the final answer. A simple text response normally needs one turn; `max_iterations: 1` enforces that. Provider retries count against the separate request-attempt limit, described in [execution limits](../configuration/limits.md).

Provider refusal, invalid or truncated output, timeouts, and exhausted budgets are technical failures. If the text must meet a strict business shape or completeness rule, use a JSON schema or a typed decision and evaluate the content with reviewed examples. An attached read-only MCP tool can turn this into a [bounded agent loop](agent-loops.md).
