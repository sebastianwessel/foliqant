# Extract or produce a JSON response

Use an `llm` step with `output.schema` when a support workflow needs an application-specific JSON value, such as a reference and requested action. The schema defines the result shape; it does not make unsupported facts true. For fixed business questions with answerability and issue codes, use a [decision](decision.md).

List `extract` in `config/support_triage/triage/flow.yaml`, then save:

```yaml
# config/support_triage/triage/extract.step.yaml
type: llm
input:
  message:
    pointer: /payload/message
instructions: Extract the invoice reference stated in the email. Use null if absent.
output:
  schema: invoice.schema.json
```

Place the schema beside the step:

```json
{
  "type": "object",
  "properties": {
    "invoice_reference": {"type": ["string", "null"]}
  },
  "required": ["invoice_reference"],
  "additionalProperties": false
}
```

For “Please send invoice INV-42,” the completed public record at `/flows/triage/steps/extract` can contain `"result": {"invoice_reference": "INV-42"}`. The value type is a JSON object. If no reference is stated, the validated result is `{"invoice_reference": null}`; it is still a completed LLM step. A downstream policy can decide whether that absence needs review.

`input` is required and may be empty. Without `prompt`, the named inputs are sent as compact JSON. If you add a `prompt`, each `{{ name }}` placeholder must name a declared input; inserted values are rendered once as JSON, and template syntax inside values is not evaluated. `{{{{` and `}}}}` render literal double braces. Bind an earlier result explicitly, for example `pointer: /steps/classify/result/answer/optionId`; there is no implicit conversation history or forwarding of the whole envelope.

Use a local schema path or inline schema. Local `$ref` resources must stay in the bundle; remote references and escaping paths fail compilation. The selected model must declare JSON Schema capability. Set `defaults.model` on the workflow or `model` here; see [models](../configuration/models.md). A Markdown `.step.md` file can put configuration in front matter and instructions in the body, with exactly one instruction source.

`max_iterations` defaults to 4 logical model turns (1–1024), including a final answer turn. A plain extraction normally uses one. Add `max_iterations: 1` on this step when you want to disallow additional model turns; provider retries of one request do not count as new iterations. The separate provider-attempt and deadline limits are in [execution limits](../configuration/limits.md).

Malformed output, schema violations, provider refusal, and truncation fail with `invalid_output`; timeouts and exhausted budgets are technical failures. This step requests `needs_review` only if an attached MCP tool asks for caller input. Test extraction accuracy against known messages using [task scoring](../evaluation/task-types.md). For model-selected lookups, continue with [agent loops](agent-loops.md); for prose, see [text response](text.md).
