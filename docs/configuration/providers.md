# Choose an AI provider

Choose the adapter for the service you actually connect to, then choose a model
that supports the output and tools your process needs. A model's brand and its
hosting platform are separate choices: a Claude model served by AWS uses the
Bedrock adapter, not the direct Anthropic adapter.

## Find your connection

| Where the model runs | `provider` | Installation extra | Connection and authentication |
| --- | --- | --- | --- |
| Your local server or a compatible gateway | `openai_compatible` | `openai` | Explicit `base_url`; optional API key |
| OpenAI's own API | `openai` | `openai` | Fixed OpenAI endpoint; `$OPENAI_API_KEY` |
| Azure OpenAI | `azure_openai` | `azure` | Explicit Azure endpoint/flavor; `$AZURE_OPENAI_API_KEY` |
| Anthropic's own API | `anthropic` | `anthropic` | Fixed Anthropic endpoint; `$ANTHROPIC_API_KEY` |
| Google Gemini Developer API | `google` | `google` | Native Google API; `$GOOGLE_API_KEY` |
| Amazon Bedrock | `bedrock` | `bedrock` | Native Converse API; explicit region and AWS IAM credentials |

From the source checkout, install only the adapters you need, for example:

```sh
uv sync --locked --extra google --extra bedrock
```

An installed application uses the same extras on its reviewed package artifact.
See [installation](../getting-started/runtime.md). The following snippets belong
under `models` in `config/settings.yaml`; their map keys are names you choose.

## OpenAI or OpenAI-compatible?

`openai` calls `https://api.openai.com/v1`. You explicitly choose `api: chat` or
`api: responses`; the adapter applies OpenAI-specific model compatibility checks.
It does not redirect to a custom `OPENAI_BASE_URL`.

```yaml
answering:
  provider: openai
  api: responses
  model: $MODEL_ID
  output_mode: native
```

`openai_compatible` uses the Chat Completions protocol at your explicit endpoint.
It is the right option for a local Qwen server, not a claim that the model was
trained by OpenAI. It does not support the Responses endpoint. Server support
for native schemas, tools, token-limit fields, and reasoning settings varies.

```yaml
local:
  provider: openai_compatible
  base_url: $MODEL_BASE_URL
  model: $MODEL_ID
  output_mode: native
  supports_tools: false
  allow_insecure_http: true
```

Set `MODEL_BASE_URL` to the API base, such as `http://127.0.0.1:1234/v1`, not
the full `/chat/completions` path. Add `api_key: $MODEL_API_KEY` if the endpoint
requires authentication. The token-limit field defaults to `max_tokens`; set
`max_tokens_field: max_completion_tokens` only for a server that requires it.
A server such as vLLM counts `max_tokens` (default `32768`) against the served
context window, so the input plus `max_tokens` must fit it; otherwise the
request fails with `context_limit_exceeded`. Lower `max_tokens` for a server
with a small context. Use HTTPS outside intended local development.

## Google Gemini

The native Google adapter uses the Gemini Developer API:

```yaml
answering:
  provider: google
  model: $GOOGLE_MODEL_ID
  output_mode: native
  api_key: $GOOGLE_API_KEY
```

The API key reference above is the default and can be omitted. Select a model
that supports your declared text, schema, and tool capabilities. The exposed
generation options are `max_tokens`, `temperature`, and `top_p`; unconfigured
thinking behavior follows the model. This profile is not a Vertex AI credential
or project/location configuration.

Google also publishes an OpenAI-compatible endpoint. Use that only when you
deliberately need the compatible protocol; the native adapter does not need it.
See [Google's compatibility documentation](https://ai.google.dev/gemini-api/docs/openai)
for that API's separate capabilities.

## AWS Bedrock

The native Bedrock adapter uses Converse and the region/model ID you specify:

```yaml
answering:
  provider: bedrock
  region: $AWS_REGION
  model: $BEDROCK_MODEL_ID
  output_mode: tool
```

Use the exact model or inference-profile ID available to your AWS deployment.
`output_mode: tool` returns structured values through an output tool; use
`native` only for a model supporting native structured output. Your chosen model
must support the requested tool-choice behavior, including agent-loop policies.

Credentials come from boto3's standard **host** credential chain: for example,
your configured AWS profile in development or an attached workload role in AWS.
The environment mapping passed to `open_application` resolves `$AWS_REGION` and
`$BEDROCK_MODEL_ID`; it does not configure boto3's IAM credential chain. Do not
put access keys in workflow YAML or assume `config/.env` changes AWS SDK credentials.
The host must have permission to invoke the selected model.
AWS credential discovery may contact your host's metadata or credential service;
it does not discover or test model endpoints.

The adapter owns client creation and cleanup, disables SDK retries (the
profile's `retry` applies instead), and keeps
blocking AWS SDK work off the event loop. It retains ownership of in-flight
work during cancellation. Generation options are the common `max_tokens`,
`temperature`, and `top_p` fields.
Some model profiles forbid sampling overrides. An explicitly configured option
that the adapter knows cannot be honored fails configuration rather than being
silently ignored.

AWS also provides compatible Chat Completions endpoints for selected deployments;
that is a different protocol from the native Converse adapter. See
[AWS's API documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions.html).

## Azure OpenAI

Choose an explicit API flavor. A v1 endpoint includes `/openai/v1`:

```yaml
answering:
  provider: azure_openai
  api: responses
  api_flavor: v1
  endpoint: $AZURE_OPENAI_ENDPOINT
  model: $AZURE_DEPLOYMENT_NAME
  output_mode: native
```

For `api_flavor: versioned`, use the resource-root endpoint and add
`api_version: $AZURE_OPENAI_API_VERSION`. Do not combine `api_version` with v1.
`api` is required and accepts `chat` or `responses`. The key defaults to
`$AZURE_OPENAI_API_KEY`.

## Anthropic

```yaml
answering:
  provider: anthropic
  model: $ANTHROPIC_MODEL_ID
  output_mode: native
```

The key defaults to `$ANTHROPIC_API_KEY`. There is no `api` selector. Optional
thinking settings are model-dependent; see the [generation option table](models.md#tune-generation-deliberately).

## Validate the connection you intend to use

All profiles share the [same model selection rules](models.md#select-the-profile),
[environment resolution](environment.md), and [runtime budgets](limits.md).
Provider SDK support does not mean every model supports every output mode.

1. Run `foliqant validate` offline for configuration and declared capabilities.
2. Open the application with the intended credentials and optional dependencies.
3. Run one controlled case for the required text, structured output, and tools.
4. Evaluate representative cases before choosing model/settings for production.

Configuration checks never download models, list a provider's models, or prove
that your account can invoke the configured model. Live verification is an
explicit operation with the provider you selected.
