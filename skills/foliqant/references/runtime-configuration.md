# Configure the runtime and adapters

Use this field index for setup and customization. Confirm the installed release
with current CLI help, the public `foliqant.contracts` modules, and
`foliqant.contracts.schemas.runtime_schemas()` or `decision_schemas()`.
For editor tooling, installed JSON files are available through
`importlib.resources.files("foliqant").joinpath("schemas", NAME)`. Python
boundary models remain authoritative; runtime execution does not read the
generated files.

## Contents

- [Set up a downstream project](#set-up-a-downstream-project)
- [Deployment root](#deployment-root)
- [Default handling and precedence](#default-handling-and-precedence)
- [Common model fields](#common-model-fields)
- [Execution limits](#execution-limits)
- [Provider retry policy](#provider-retry-policy)
- [Environment handling](#environment-handling)
- [MCP](#mcp)
- [Trusted handlers](#trusted-handlers)
- [Telemetry](#telemetry)
- [Application lifecycle and context](#application-lifecycle-and-context)
- [Validate the configuration](#validate-the-configuration)

## Set up a downstream project

Use CPython 3.12 and install the reviewed Foliqant artifact with only the
adapters the application needs:

| Extra | Capability |
| --- | --- |
| `openai` | OpenAI and OpenAI-compatible model clients |
| `azure` | Azure OpenAI client |
| `anthropic` | Anthropic client |
| `google` | Native Gemini Developer API client |
| `bedrock` | Native AWS Bedrock Converse client |
| `mcp` | MCP Streamable HTTP/stdio and OAuth support |
| `telemetry` | OTLP/HTTP traces and metrics |

Run `foliqant init DEST` for a conventional starter, or create
`config/settings.yaml` and one `config/<workflow>/workflow.yaml`. The starter
includes `config/.env.example`; copy it to `config/.env` and set `MODEL_ID`
and `MODEL_BASE_URL` only when using its local model profile. For a manually
created project, supply only the environment values its profiles reference.

## Deployment root

`config/settings.yaml` accepts:

| Field | Shape |
| --- | --- |
| `workflows` | optional map of workflow ID to relative directory |
| `models` | map of model profile ID to provider profile |
| `mcp` | map of server ID to MCP profile |
| `execution` | process/run limits |
| `telemetry` | optional OTLP/HTTP profile |
| `evaluation.dataset` | optional custom gold dataset path |

Without `workflows`, discover immediate nonhidden
`config/*/workflow.yaml`. Workflow and definition paths remain under the
settings directory. The optional evaluation dataset can live outside that tree;
its relative path is resolved from the settings file.

## Default handling and precedence

Omit fields whose package default fits the application. Values in examples such
as `temperature: 0.1`, `reasoning_effort: low`, and per-step `max_tokens: 800`
are explicit choices, not package defaults. Reject unknown fields; there is no
implicit inheritance from surrounding folders or merged environment-specific
YAML files.

| Setting | Default when omitted |
| --- | --- |
| `models`, `mcp`, `handlers` | Empty registries; declare only dependencies the workflow uses. |
| `execution` or individual execution fields | The execution values listed below. |
| `telemetry` | Disabled; `conditions` (debug condition events) defaults to `false`. |
| `evaluation` | No startup access to gold; explicit evaluation discovers `evaluation/dataset.json` beside `config/`. |
| Model `provider`, `model`, `output_mode` | Required; no default provider, model ID, or output mode. |
| OpenAI `api`; Azure `api` and `api_flavor` | Required; choose explicitly. Compatible `api` defaults to `chat`. |
| Model `options.max_tokens` | `4096`. |
| `temperature`, `top_p`, `seed`, reasoning/thinking options | Unset; the provider/model decides when no value is configured. |
| Model capabilities | `supports_text`, `supports_json_schema`, and `supports_tools` are all `true`; configure the selected model's actual capabilities. |
| Model admission and timeout | `concurrency: 4`, `queue_limit: 16`, `request_timeout: 60` seconds. |
| Compatible `api_key` | Absent; OpenAI, Azure, Anthropic, and Google use their documented credential environment references. Bedrock uses the host credential chain. |
| Compatible `allow_insecure_http`, `max_tokens_field` | `false`, `max_tokens`. |

Model selection is explicit step `model` first, then the flow definition's
`defaults.model`, then workflow `defaults.model`.
A sole model profile is not selected automatically. A profile override retains
the profile's provider, credentials, capabilities, timeout, retries, and admission
group. It replaces only an explicitly supplied model ID and individual supplied
option keys; omitted options retain the profile value. A complete inline profile
is independent and does not inherit the workflow profile's settings.

For nullable generation options, an explicit `null` in an override clears the
inherited value, leaving provider behavior in effect; it differs from omission.
`model` and `max_tokens` cannot be cleared. The merged options must still satisfy
the chosen provider's constraints. An override's `pricing` replaces the
profile's pricing and `pricing: null` removes it; an override with a different
`model` does not inherit the profile's pricing.

## Common model fields

Every model profile has:

- `provider`, `model`, and `output_mode: native|tool`;
- `supports_text` (default true), `supports_json_schema` (true), and
  `supports_tools` (true);
- `concurrency` (4), `queue_limit` (16), and `request_timeout` (60);
- `retry` (one attempt by default);
- optional `pricing` (see [Pricing](#pricing));
- provider-specific `options`.

`output_mode: tool` requires tool support. At least text or JSON Schema output
must be supported. `model` is nonblank. `concurrency` is 1–1024,
`queue_limit` is 0–10,000, and `request_timeout` is positive and at most
3600 seconds.

Common generation options are `max_tokens` (default 4096, 1–1,048,576),
optional `temperature` (0–2), and optional `top_p` (greater than 0 and at
most 1). OpenAI-family profiles also accept an optional strict-integer `seed`
and `reasoning_effort: none|minimal|low|medium|high|xhigh`.

### Pricing

`pricing` estimates cost from reported usage; it is configuration, not a price
list, and must be updated when prices change. Example with GPT-5.6 Terra list
prices:

```yaml
pricing:
  currency: USD                    # required, only USD
  input_per_million: 2.00          # required
  cached_input_per_million: 0.20   # optional; defaults to the input price
  output_per_million: 12.00        # required
  reasoning_billed_as: output      # output (default) | input
  long_context:                    # optional tier for large requests
    threshold_input_tokens: 272000 # applies when input tokens exceed it
    input_per_million: 4.00
    cached_input_per_million: 0.40
    output_per_million: 18.00
  reference_model: gpt-5.6-terra   # optional: whose prices are borrowed
```

Per request: (input − cached) × input price + cached × cached price + output ×
output price; reasoning tokens are part of output and use the input price with
`reasoning_billed_as: input`. The long-context tier replaces all prices of a
request above the threshold. Arithmetic is exact decimal; results round to six
decimals. A needed count that was not reported gives `cost: null` with
`cost_complete: false`. Prices are numbers from 0 to 1,000,000; strings,
other currencies and unknown fields are rejected. Usage objects then carry
`cost`, `cost_complete`, `currency`, optional `reference_model`, and
`by_model` (keyed by provider model ID: `requests`, `input_tokens`,
`cached_input_tokens`, `output_tokens`, `reasoning_tokens`, and the cost
fields). `foliqant explain` shows `model_selection.pricing`; `foliqant doctor`
lists `models.<profile>.pricing`.

### OpenAI

```yaml
provider: openai
api: chat                    # or responses
model: $MODEL_ID
api_key: $OPENAI_API_KEY     # default reference
output_mode: native
options:
  max_tokens: 4096
  temperature: 0.1           # optional 0..2
  top_p: 0.9                 # optional (0,1]
  seed: 7                    # optional
  reasoning_effort: low      # none|minimal|low|medium|high|xhigh
```

### OpenAI-compatible

```yaml
provider: openai_compatible
api: chat
model: $MODEL_ID
base_url: $MODEL_BASE_URL
api_key: $MODEL_API_KEY      # optional reference
allow_insecure_http: false
max_tokens_field: max_tokens # or max_completion_tokens
output_mode: native
options:
  max_tokens: 4096
```

Plain HTTP requires explicit `allow_insecure_http: true`; prefer HTTPS outside
local development.

### Azure OpenAI

```yaml
provider: azure_openai
api: responses               # or chat
api_flavor: versioned        # or v1
endpoint: $AZURE_OPENAI_ENDPOINT
api_version: $AZURE_OPENAI_API_VERSION
model: $MODEL_ID
api_key: $AZURE_OPENAI_API_KEY
output_mode: native
```

`versioned` uses a resource-root endpoint plus `api_version`; `v1` uses an
`/openai/v1` endpoint without `api_version`.

### Anthropic

```yaml
provider: anthropic
model: $MODEL_ID
api_key: $ANTHROPIC_API_KEY
output_mode: native
options:
  max_tokens: 4096
  thinking: adaptive         # disabled|adaptive, optional
  effort: high               # low|medium|high|xhigh|max; adaptive only
```

Alternatively set `thinking_budget` (at least 1024 and below `max_tokens`);
do not combine a fixed budget with `thinking`, `temperature`, or `top_p`.

### Google Gemini Developer API

```yaml
provider: google
model: $GOOGLE_MODEL_ID
api_key: $GOOGLE_API_KEY
output_mode: native
options:
  max_tokens: 4096
```

`api_key` defaults to `$GOOGLE_API_KEY`. This uses Google's native Gemini
API, not Vertex AI or the OpenAI-compatible endpoint. The supported options
are the common `max_tokens`, `temperature`, and `top_p` fields. The adapter
turns SDK automatic retries off; configured transient-response retries remain
within the normal model attempt budget. Choose a model with the text, JSON
Schema, and tool capabilities that the selected steps require.

### AWS Bedrock Converse

```yaml
provider: bedrock
region: $AWS_REGION
model: $BEDROCK_MODEL_ID
output_mode: tool
options:
  max_tokens: 4096
```

`region` is required and may be a literal or an environment reference. The
model ID is explicit and may be a reviewed inference-profile ID. Boto3 uses
the host's default IAM credential chain; the `environment` mapping passed to
`open_application` resolves marked Foliqant fields but does not inject AWS
credentials into that chain. No AWS key fields are accepted in this profile.

Use `output_mode: native` only when the selected Bedrock model profile
positively supports native JSON Schema output. `output_mode: tool` can support
schema steps on a model without native JSON Schema output, provided the model
positively supports required tool choice. The adapter disables botocore
automatic retries and runs its blocking SDK work off the event loop. Started
Converse requests retain model admission until they actually finish, including
after caller cancellation or a Foliqant deadline. Common generation options
are `max_tokens`, `temperature`, and `top_p`; provider-specific thinking
options are not exposed.

An operation may select a profile ID, supply a profile override, or supply a
complete provider profile. A profile override has this shape:

```yaml
model:
  profile: local
  model: alternate-model
  options:
    max_tokens: 800
```

The `model` and `options` overrides are optional. Option overrides are
`max_tokens`, `temperature`, `top_p`, `seed`, `reasoning_effort`,
`thinking`, `effort`, and `thinking_budget`; the compiler rejects options
unsupported by the selected provider.

## Execution limits

These are the package defaults; all timeout values are seconds. Setting only one
field keeps the remaining defaults.

```yaml
execution:
  concurrency: 4
  queue_limit: 16
  run_timeout: 300
  model_timeout: 60
  tool_timeout: 30
  max_steps: 32
  model_requests_per_step: 4
  tool_calls_per_step: 3
```

`concurrency` is 1–1024 and `queue_limit` is 0–65,536. Durations are
positive and at most 3600 seconds. `max_steps`,
`model_requests_per_step`, and `tool_calls_per_step` are each 1–1024.
Admission is per process and does not provide durability.

Limits compose: the remaining run deadline still bounds each model/tool call.
`model_timeout` bounds a logical model request while the profile's
`request_timeout` configures its SDK/network timeout. For MCP, the effective
tool timeout also respects the server's `request_timeout`. Raising a provider
timeout alone does not raise the run deadline. LLM `max_iterations` limits logical
turns independently of `model_requests_per_step`, which counts retry attempts.

## Provider retry policy

Model and MCP profiles accept:

```yaml
retry:
  max_attempts: 1
  initial_delay_seconds: 0.25
  max_delay_seconds: 5
```

`max_attempts` is a strict integer from 1 through 8 and includes the initial
request. Both delays are finite; the initial delay is 0–60 seconds, the maximum
is 0–300 seconds, and the maximum cannot be lower than the initial value.
Defaults therefore make exactly one request.

When more attempts are enabled, the runtime retries only safely observed
completed HTTP responses with status 429, 500, 502, 503, or 529. Backoff is
capped exponential full jitter. A valid `Retry-After` delta or date is a
minimum delay bounded by the configured maximum and remaining absolute deadline.
If it cannot fit, return the final transient failure.

Never retry a connection or stream interruption, timeout including 408/504,
cancellation, authentication failure, schema/output failure, or whole
step/flow/workflow. Provider SDK retries remain zero. Every request reserves and
accounts against `model_requests_per_step` or `tool_calls_per_step`; failed
attempt token usage remains unknown. Release and reacquire model request
admission during backoff. Keep an MCP server's admitted authenticated session
held across tool-call backoff, and keep every attempt within the original
logical deadline.

## Environment handling

Only marked deployment fields accept complete `$NAME` references. `$$` is a
literal dollar. Model IDs/endpoints/credentials, telemetry endpoints/headers,
and MCP process/endpoint values are marked. Prompts, schemas, bindings,
documents, and input are never expanded.

`prepare_application` does not read environment values.
`open_application` loads `.env` beside the chosen settings file, then
overlays its required `environment` mapping. Pass `environment=os.environ` to
use process values; a custom mapping supplies exactly those overrides instead.
Supplied values win over `.env`. Missing or blank referenced values fail before
clients open; `.env.example` is never read. Resolution happens once when the
application opens, not per request, and resolved values are not expanded again.

## MCP

Each profile contains `transport`, optional `auth`, optional
`identity_meta_key`, a nonempty `catalog.tools`, `concurrency` (4),
`queue_limit` (16), `request_timeout` (30), and
`retry` (one attempt), and `output_limit_bytes` (1 MiB).
`concurrency` is 1–1024, `queue_limit` is 0–10,000,
`request_timeout` is positive and at most 3600 seconds, and
`output_limit_bytes` is 1 byte–64 MiB.

Streamable HTTP:

```yaml
transport:
  type: streamable_http
  endpoint: $MCP_ENDPOINT
  allow_insecure_http: false
```

Stdio:

```yaml
transport:
  type: stdio
  command: $MCP_COMMAND
  args:
    - --serve
  cwd: $MCP_CWD             # optional absolute path
  env:
    TOKEN: $MCP_TOKEN
```

Stdio cannot use an HTTP auth hook. Each tool declaration has
`input_schema`, optional `output_schema`, and `effect: read|write`; current
runtime execution admits only read effects.

`auth` is a provider name. For example, `auth: oauth` requires the host to
pass `RuntimePlugins(mcp_credentials={"oauth": provider})`. The mapping key
must match exactly; it is checked before a session opens. For every HTTP session,
the selected `McpCredentialProvider.create_auth` receives
`McpCredentialScope(server_alias, endpoint, auth_reference, identity)` and the
current `StepContext`, and returns `McpHttpAuthorization` with a fresh
`httpx2.Auth` object and allowed HTTPS origins. Credential providers are
trusted Python composition hooks, not imports or secrets from YAML.

`SdkOAuthCredentialProvider` accepts MCP `OAuthClientMetadata`, an
`OAuthTokenStorageFactory` that partitions storage by every scope field,
nonempty allowed authorization-server HTTPS origins, and optional
`OAuthOperatorInteraction` plus `client_metadata_url`. It leaves discovery,
PKCE, state, refresh, and protected-resource processing to the SDK. Operator
callbacks are the explicit UI boundary; Foliqant does not launch a browser.

`identity_meta_key` names an explicit reverse-DNS request-metadata key. When
present, the runtime sends non-null `tenant_id` and `principal_id` values to
the MCP server under that key. It conveys already validated context and does not
authenticate a caller.

## Trusted handlers

Declare every handler's contract in `settings.yaml`; schemas are inline objects
or JSON/YAML files relative to the settings file, self-contained and confined
to its directory:

```yaml
handlers:
  normalize:
    input_schema: contracts/normalize.input.json
    output_schema: contracts/normalize.output.json
    effect: read
```

Register the trusted callable when preparing the application:

```python
from foliqant import prepare_application
from foliqant.adapters.handlers import HandlerRegistration

handlers = {
    # async (FrozenObject, StepContext) -> StepOutcome
    "normalize": HandlerRegistration(normalize),
}
prepared = prepare_application(config_path, handlers=handlers)
```

`HandlerRegistration(handler, input_schema=None, output_schema=None,
effect="read")`: optional schemas (for example generated from Pydantic models)
must equal the declaration after canonicalisation, else
`handler_contract_mismatch` names the handler and the first differing path.
A registration for an undeclared handler is `unknown_handler`; a declared
handler without registration compiles (offline commands work) and fails at
`open_application` with `missing_handler_registration`. Configuration cannot
import code. Declared schemas drive compile-time binding and route-coverage
checks and runtime validation. Exceptions become safe service errors.

A handler returns `StepOutcome(result, needs_review=False, selection=None,
unresolved_issues=())`. Issues require `needs_review` and select issue-specific
review routes; a selection's origin is `fallback` exactly when the step needs
review. `StepContext` adds `trace` (W3C carrier of the step span), `attempt`,
`collection_item` and `flow_role` (`routed`, `callable`, `retry`).

## Telemetry

`telemetry` fields are:

- required `service_name`;
- optional `traces_endpoint`, `metrics_endpoint`;
- `traces_headers`, `metrics_headers` (protected environment values);
- `allow_insecure_http` (false);
- `span_queue_capacity` (2048), `span_batch_size` (512),
  `span_schedule_delay` (5);
- `metric_export_interval` (60), `metric_export_batch_size` (512);
- `export_timeout` (10), `shutdown_timeout` (10);
- `conditions` (false): add debug `condition.evaluated` span events.

Spans are named from configuration IDs: `workflow <id>`, `flow <id>` (with
` [item <index>]` inside a collection and ` #<attempt>` from the second attempt),
`step <id> (<type>)`, `chat <model>` and `execute_tool <tool>`. Attributes keep
`foliqant.workflow.name`, `foliqant.flow.name`, `foliqant.step.name`,
`foliqant.step.kind`, `foliqant.execution.id`, `foliqant.flow.attempt` and
`foliqant.flow.role`; model spans add OTel GenAI `gen_ai.*` usage attributes
and `foliqant.usage.cost` when pricing is configured.

An empty endpoint disables that signal. Telemetry exports allowlisted configured
component labels (resolved values, never `$NAME` references), trace identifiers,
statuses, timings, attempt numbers, routes, repeat stops, skipped steps and
usage, never tenant/principal identity, payloads, prompts, model output,
condition operands, credentials, or raw exceptions. Labels that cannot be
exported safely are dropped with one `telemetry_labels_dropped` log event.
`ExecutionResult.execution.trace` holds the run span's `trace_id`/`span_id`;
streamable-HTTP MCP calls carry `traceparent`/`tracestate` headers. `open_application(...,
install_global_telemetry=True)` is an explicit host choice.
Foliqant does not replace host logging; configure a reviewed safe sink and
filters for SDK and third-party logs.

Queue capacity is 1–65,536; span batch size is 1–4096 and cannot exceed that
capacity. Metric batch size is 1–65,536. Schedule delay and metric interval are
positive and at most 3600 seconds. Export and shutdown timeouts are positive and
at most 30 seconds. Endpoints use HTTPS unless
`allow_insecure_http: true` is explicit.

## Application lifecycle and context

```python
prepared = prepare_application(Path("config/settings.yaml"), handlers=handlers)
async with open_application(
    prepared,
    environment=os.environ,
    plugins=RuntimePlugins(
        model_factory=my_factory,
        mcp_credentials=my_credentials,
        tool_authorizer=my_authorizer,
    ),
) as app:
    full = await app.run("intake", envelope, identity=identity)
    flow = await app.run_flow("intake", "triage", resolved_flow_envelope)
    step = await app.run_step("intake", "triage", "classify", resolved_step_envelope)
```

`Envelope` contains arbitrary JSON `payload` and open `metadata`.
`tenant_id`, `principal_id`, and W3C trace carrier fields are protected.
Caller-supplied `Identity` must match protected envelope values; this validates
context but does not authenticate it. Optional `transport_trace` is separate
from business metadata.

Keep the application context open while calls run. Shutdown stops new admission,
drains cooperative work, and cannot prove an already-started remote operation
stopped. Telemetry shutdown is also bounded and may report an incomplete drain
while an exporter socket worker is still finishing; it does not hard-kill that
worker.

## Validate the configuration

From the application root, run `foliqant validate --strict`,
`foliqant explain --format mermaid --all --output docs/workflows.md`, and
`foliqant doctor`. Handlers are declared in settings, so these commands compile
workflows with handlers offline; `prepare_application(config_path,
handlers=handlers, strict=True)` does the same in Python and raises one
`CompilationError` listing every warning in `error.problems`. `PreparedApplication.diagnostics` and `foliqant.explain(prepared,
workflow)` expose the diagnostics and graph model. Use `--config PATH` when
settings are not at `config/settings.yaml`. Open the application only after its
required marked environment values, adapter dependencies and handler
registrations are available. See the evaluation reference for gold and the
deployment reference for CLI and host behavior.
