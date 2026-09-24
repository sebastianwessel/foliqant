# Configure models

A model profile connects a name in your workflow to a provider and a specific
served model. Define profiles once in `config/settings.yaml`, select a default
in each workflow, and override it only where a step needs different behavior.
Profile names such as `local` or `extractor` are your names; none is reserved.

Choose a [provider](providers.md) first if you are connecting to OpenAI, Azure,
Anthropic, Google Gemini, or AWS Bedrock. The example below uses a local server.

## Start with an OpenAI-compatible endpoint

From the Foliqant source checkout, install the adapter into the locked environment:

```sh
uv sync --locked --extra openai
```

Add this profile to your application's `config/settings.yaml`:

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

In `config/.env`, set the model ID and the full base URL exposed by your server:

```dotenv
MODEL_ID=your-served-model-id
MODEL_BASE_URL=http://127.0.0.1:1234/v1
```

The local Qwen examples use this adapter. The package does not download, start,
or discover a model; start your inference server separately. This profile assumes
it supports native structured output. Generation options can be added after
checking your model's requirements. `allow_insecure_http: true` is an explicit
local-development choice; use HTTPS for remote deployments.

`request_timeout` is one model request's upper bound. It does not extend the
workflow deadline. A slow local model may also need larger `execution.run_timeout`
and `execution.model_timeout`; use the complete [slow local model configuration](limits.md#configure-a-slow-local-model)
to keep one request active at a time and give it a coherent timeout budget.

## Select the profile

Set the workflow default in `config/intake/workflow.yaml` (fragment):

```yaml
defaults:
  model: local
```

A `decision` or `llm` step inherits it. To select another declared profile, put
`model: extractor` on that step. There is no automatic choice of the first model.
Deterministic handlers and direct MCP steps do not need a model.

To reuse a profile while changing only generation settings, add this step fragment:

```yaml
model:
  profile: local
  options:
    max_tokens: 800
    temperature: 0.1
```

An override can also set `model` to a different served model ID. Unspecified
options retain the profile value; explicit `null` clears an optional option.
`max_tokens` cannot be cleared. Overrides retain the profile's provider,
credentials, capabilities, timeout, and shared admission limit. An override may
also set [`pricing`](#estimate-cost-with-pricing), which replaces the profile's
block; `pricing: null` removes it. An override that selects a different `model`
does not inherit the profile's pricing, because those prices describe the
profile's own model. A complete inline provider profile is also accepted, but
named profiles are easier to reuse and audit.

## Choose a provider

The table lists additional fields beyond the shared required `model` and
`output_mode`. Endpoint and model values may use environment references.

| `provider` | Installation extra | Provider-specific configuration |
| --- | --- | --- |
| `openai_compatible` | `openai` | Required `base_url`; `api` is `chat`; optional `api_key`; `max_tokens_field` defaults to `max_tokens` and can be `max_completion_tokens` |
| `openai` | `openai` | Required `api: chat` or `api: responses`; API key defaults to `$OPENAI_API_KEY` |
| `azure_openai` | `azure` | Required `api`, `api_flavor`, and HTTPS `endpoint`; key defaults to `$AZURE_OPENAI_API_KEY` |
| `anthropic` | `anthropic` | Key defaults to `$ANTHROPIC_API_KEY`; no `api` selector |
| `google` | `google` | Native Gemini Developer API; key defaults to `$GOOGLE_API_KEY` |
| `bedrock` | `bedrock` | Native Converse; required `region`; host AWS credential chain |

For example, this is a complete OpenAI profile inside `models`:

```yaml
cloud:
  provider: openai
  api: responses
  model: $MODEL_ID
  output_mode: native
  api_key: $OPENAI_API_KEY
```

For Azure, `api_flavor: versioned` requires a resource-root endpoint and an
`api_version`. `api_flavor: v1` requires an endpoint ending in `/openai/v1` and
forbids `api_version`. Choose the combination supported by your deployment.

For connection examples and the difference between native and compatible APIs,
see [choose a provider](providers.md). Google and Bedrock currently expose the
common generation options, not OpenAI- or Anthropic-specific option fields.

## Match output mode and capabilities

Capabilities are declarations checked by the compiler, not runtime probes.
They must describe what the selected provider/model actually supports.

| Setting | Default | Meaning |
| --- | --- | --- |
| `output_mode` | Required | `native` uses native structured output; `tool` uses a generated output tool |
| `supports_text` | `true` | The model may produce a text result |
| `supports_json_schema` | `true` | The model may produce a schema-constrained result |
| `supports_tools` | `true` | The model supports tool calls; required for `output_mode: tool` and MCP agent loops |

A generated output tool is a way to return the final structured answer. It is
separate from granting access to business tools. To let a model call MCP tools,
configure the step's explicit allowlist as shown in [bounded agent loops](../steps/agent-loops.md).

## Tune generation deliberately

| Option | Supported profiles | Behavior |
| --- | --- | --- |
| `max_tokens` | All | Output budget; defaults to `4096`, allowed range 1–1,048,576 |
| `temperature` | All | Optional sampling setting, 0–2 |
| `top_p` | All | Optional sampling setting, greater than 0 and at most 1 |
| `seed` | OpenAI family | Optional integer; provider support determines reproducibility |
| `reasoning_effort` | OpenAI family | `none`, `minimal`, `low`, `medium`, `high`, or `xhigh` |
| `thinking` | Anthropic | `disabled` or `adaptive` |
| `effort` | Anthropic | `low`, `medium`, `high`, `xhigh`, or `max`; requires adaptive thinking |
| `thinking_budget` | Anthropic | Fixed budget of at least 1,024, below `max_tokens`; cannot combine with `thinking`, `temperature`, or `top_p` |

Except for `max_tokens`, omitted generation settings defer to the provider.
Acceptance by the configuration validator does not prove that a particular
model supports the combination. Change one setting at a time and compare on
the same [reviewed evaluation cases](../evaluation/running.md).

## Estimate cost with pricing

Add an optional `pricing` block to a profile to have results and model spans
report an estimated cost. This profile uses the GPT-5.6 Terra list prices as an
example:

```yaml
models:
  terra:
    provider: azure_openai
    api: responses
    api_flavor: v1
    endpoint: $AZURE_OPENAI_ENDPOINT
    model: gpt-5.6-terra
    output_mode: native
    pricing:
      currency: USD
      input_per_million: 2.00
      cached_input_per_million: 0.20
      output_per_million: 12.00
      long_context:
        threshold_input_tokens: 272000
        input_per_million: 4.00
        cached_input_per_million: 0.40
        output_per_million: 18.00
```

Prices change, and they are configuration: copy the current prices of your
provider and contract into `pricing`, and review them like any other setting.
Foliqant ships no price list and never looks prices up.

| Field | Required | Meaning |
| --- | --- | --- |
| `currency` | Yes | `USD` |
| `input_per_million` | Yes | Price of one million uncached input tokens |
| `cached_input_per_million` | No | Price of one million cached input tokens; omitted, cached input is billed at the input price |
| `output_per_million` | Yes | Price of one million output tokens |
| `reasoning_billed_as` | No | `output` (default) or `input`: which price applies to reasoning tokens |
| `long_context` | No | `threshold_input_tokens` and the tier's `input_per_million`, optional `cached_input_per_million`, and `output_per_million` |
| `reference_model` | No | The model whose prices you used, when you estimate one model with another's prices, for example `gpt-5.6-terra` for a local model |

Each request is estimated on its own, with exact decimal arithmetic:

- uncached input (input minus cached tokens) × input price,
- plus cached input × cached price (the input price when none is set),
- plus output tokens × output price. Providers report reasoning tokens inside
  the output tokens; with `reasoning_billed_as: input` those tokens use the
  input price instead.

When a request's input tokens exceed `threshold_input_tokens`, every token of
that request uses the `long_context` prices; at or below the threshold the base
prices apply. Cache writes are billed as input. Request costs are summed per
model and across steps, flows, attempts and collection items, and rounded to six
decimals in the result.

The estimate is never guessed. When a count it needs was not reported (input or
output tokens, cached tokens when a cached price is set, or reasoning tokens
with `reasoning_billed_as: input`), that request's cost is unknown and the
result shows `cost: null` with `cost_complete: false`. Validation is strict:
prices are nonnegative numbers of at most 1,000,000, and unknown fields,
strings and other currencies are rejected offline. `foliqant explain` shows
each step's effective pricing under `model_selection.pricing`, and `foliqant
doctor` lists every profile's pricing under `models`. See
[usage by model and cost](../integration/results.md#read-usage-by-model-and-cost)
for the result fields.

## Keep credentials separate from business data

`$NAME` means a whole environment value; it is not string interpolation.
Use `base_url: $MODEL_BASE_URL`, not `https://$HOST/v1`. `$$` escapes a literal
dollar in supported environment fields. Always use environment references for
API keys. Inline step profiles require them; named deployment profiles also
accept literals, so keep those out of committed configuration.

The application loads `.env` beside the selected settings file when it opens.
The host's environment mapping overrides file values. Offline preparation does
not resolve secrets, read `.env`, or contact providers. Prompts, schema strings,
bindings, and customer inputs do not expand environment variables.

Neither `.env` nor `.env.example` is required when the host supplies the values.
An optional `.env.example` is a template for other developers; the runtime never
reads it. If you use a local `.env`, add it to `.gitignore` yourself. Do not put
tokens in committed files, prompts, evaluation gold, or logs.

## Bound capacity and retries

Each profile defaults to `concurrency: 4`, `queue_limit: 16`, and
`request_timeout: 60`. These are in-process admission limits, not a persistent
queue. Start with one concurrent request for a local model and measure before
increasing it.

Retries default to one attempt. An explicit profile `retry` can retry a limited
set of completed transient HTTP responses; ambiguous timeouts are not retried.
See the exact [retry policy](../reference/runtime-configuration.md#provider-retries).

The defaults cap short request processing; a local model or a longer agent loop
may need explicit overrides. [Limits](limits.md) explains how the deadlines,
iteration ceiling, and model/tool request counts interact.

## Check the configuration

```sh
foliqant validate
foliqant explain --workflow intake
foliqant doctor
```

These commands check configuration, not endpoint availability. After reviewing
the plan, run a deliberate live case or evaluation. Missing credentials fail
when the application opens. Structural and declared-capability errors fail
offline; some provider-specific option combinations are checked while opening
the application, before a model request is sent.

Continue with [decision steps](../steps/decision.md),
[extraction and text](../steps/llm.md), or [MCP setup](mcp.md).
