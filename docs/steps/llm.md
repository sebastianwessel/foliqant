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

`max_iterations` defaults to 8 logical model turns (1–1024), including a final answer turn; reaching it fails with `iteration_limit_reached`. A plain extraction normally uses one. Add `max_iterations: 1` on this step when you want to disallow additional model turns; provider retries of one request and output corrections do not count as new iterations. The separate provider-attempt and deadline limits are in [execution limits](../configuration/limits.md).

Malformed output and schema violations are first returned to the model for correction, up to the profile's `output_retries` (default `1`); a step that is still invalid fails with `invalid_output`, and its error's `reason`, `location` and `constraint` say what was wrong ([output retries](../integration/errors.md#output-retries)). A response the provider stopped at its output token limit fails with `output_limit_reached`, and a refusal or content filter with `output_refused` ([stop reasons](../integration/errors.md#model-stop-reasons)); neither is corrected. Timeouts and limits fail with their own codes ([error codes](../integration/errors.md#canonical-error-codes)); a failure is never a review outcome. This step requests `needs_review` only if an attached MCP tool asks for caller input. Test extraction accuracy against known messages using [task scoring](../evaluation/task-types.md). For model-selected lookups, continue with [agent loops](agent-loops.md); for prose, see [text response](text.md).

## Set the output budget and reasoning effort

`max_tokens` (default `32768`, sized for reasoning models) bounds everything the model generates for one request. For a reasoning model this includes its reasoning tokens: a model that reasons at length can spend the whole budget before it writes the answer, and the step then fails with `output_limit_reached`. Its step `usage` shows `reasoning_output_tokens` equal to `output_tokens`. Nothing in the configuration tells Foliqant whether a served model reasons, so size the budget from evaluation runs, where `failures_by_code` counts such failures and `failures_by_reason` separates `reasoning_consumed_budget` from `answer_exceeded_budget` ([results](../evaluation/results.md)). A model whose maximum output is below `max_tokens` rejects the request with `request_rejected`, and an OpenAI-compatible server that counts `max_tokens` against the context window fails with `context_limit_exceeded`; lower `max_tokens` for such a model.

Set defaults on the profile in `config/settings.yaml`:

```yaml
models:
  local:
    provider: openai_compatible
    model: $MODEL_ID
    base_url: $MODEL_BASE_URL
    output_mode: native
    options:
      max_tokens: 8192
      reasoning_effort: low
```

Override them for one step with a profile override; unspecified options keep the profile value:

```yaml
# config/support_triage/triage/extract.step.yaml
type: llm
model:
  profile: local
  options:
    max_tokens: 2048
    reasoning_effort: none
input:
  message:
    pointer: /payload/message
instructions: Extract the invoice reference stated in the email. Use null if absent.
output:
  schema: invoice.schema.json
```

The same `model` override works on a [decision](decision.md) step; `defaults.model` of a workflow or flow names a profile only, so put shared defaults on a profile and step-specific values on the step. `reasoning_effort` applies to the OpenAI family (`openai`, `azure_openai`, `openai_compatible`); for Anthropic use `thinking`, `effort` or `thinking_budget`, which stays below `max_tokens`. `reasoning_effort: null` in an override clears the profile value so the provider default applies. Whether a compatible server honours `reasoning_effort` depends on the server and model; confirm the effect on evaluation cases. See [generation options](../configuration/models.md#tune-generation-deliberately) for every option.
