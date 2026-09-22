# Deployment and CLI reference

Start with [Configuration and folder layout](../configuration/index.md) for
the authoring sequence. The focused guides explain [model profiles](../configuration/models.md)
and [MCP connections](../configuration/mcp.md). This page collects deployment
settings, limits, retry behavior, telemetry, and commands for lookup.

For the data passed to a workflow and returned by execution, see
[Inputs, results, and errors](inputs-and-results.md). That reference also
separates decision issues from operational error codes and CLI exit codes.

The default configuration is `config/settings.yaml`. It is strict YAML. With
the conventional layout it can contain only shared settings:

```yaml
models: {}
```

When `workflows` is omitted, preparation discovers immediate nonhidden
`config/*/workflow.yaml` files. Each directory name is its workflow name. Use
an explicit map for customized names or locations:

```yaml
workflows:
  public_name: internal_directory
```

Add `models`, `mcp`, `execution`, `telemetry`, or `evaluation` only
when needed. Explicit paths are relative to the configuration file and must
remain below its directory. An explicit mapping key must equal the compiled
workflow name.

## Installed schemas

The public Python boundary models are the runtime authority. The wheel also
ships generated JSON Schema files under `foliqant/schemas/` for editors,
validators, and code-generation tools; the runtime does not read those copies.
Access a packaged file without assuming a checkout layout:

```python
from importlib.resources import files

deployment_schema = files("foliqant").joinpath("schemas", "deployment.schema.json")
```

`foliqant.contracts.schemas.runtime_schemas()` and `decision_schemas()`
generate the same public schemas from the installed Python models. Do not edit
the packaged JSON by hand.

## Execution limits

| Setting | Default | Meaning |
| --- | ---: | --- |
| `concurrency` | `4` | Runs admitted at once per process |
| `queue_limit` | `16` | Additional runs allowed to wait |
| `run_timeout` | `300` | Total seconds for one workflow call |
| `model_timeout` | `60` | Total seconds for one model attempt |
| `tool_timeout` | `30` | Total seconds for one tool attempt |
| `max_steps` | `32` | Maximum operations executed in one call |
| `model_requests_per_step` | `4` | Model requests started by one operation |
| `tool_calls_per_step` | `3` | Tool calls started by one operation |

These bounds are per process. Admission does not create jobs or persistence.
Cancellation does not prove that an already-started remote or blocking operation
stopped.

## Model profiles

A model profile states the provider, model, output mode, capabilities, admission,
timeout, and generation options. There is no implicit endpoint or model
discovery.

```yaml
models:
  local:
    provider: openai_compatible
    model: $MODEL_ID
    base_url: $MODEL_BASE_URL
    allow_insecure_http: true
    output_mode: native
    supports_text: true
    supports_json_schema: true
    supports_tools: false
    concurrency: 1
    queue_limit: 0
    request_timeout: 300
    retry:
      max_attempts: 1
      initial_delay_seconds: 0.25
      max_delay_seconds: 5
    options:
      max_tokens: 4096
      temperature: 0.1
      reasoning_effort: low
```

Supported providers are `openai`, `openai_compatible`, `azure_openai`,
and `anthropic`. Structural fields are validated offline; some provider-specific
option combinations are checked when the application opens, before any request.
A workflow can set `defaults.model`; an individual decision or LLM operation
can select a profile, apply an explicit model/options override, or provide a
complete compatible profile.

`output_mode: native` uses the provider's native structured-output feature.
`output_mode: tool` uses a generated output tool and therefore requires tool
support. The configured capabilities must cover every operation that selects
the profile.

### Provider retries

Model and MCP profiles accept the same optional `retry` object:

| Setting | Default | Bounds | Meaning |
| --- | ---: | ---: | --- |
| `max_attempts` | `1` | 1–8 | Total attempts, including the initial request |
| `initial_delay_seconds` | `0.25` | 0–60 | Initial exponential-backoff cap |
| `max_delay_seconds` | `5` | 0–300 | Maximum backoff cap; at least the initial delay |

The default makes one request and performs no retry. When enabled, the runtime
retries only a safely observed completed HTTP response with status 429, 500,
502, 503, or 529. It uses capped exponential full jitter. A valid
`Retry-After` delta or date is a minimum delay, still bounded by the configured
maximum and the remaining logical deadline. If that delay cannot fit, the final
transient failure is returned.

Timeouts, 408/504 responses, connection or stream interruption, cancellation,
authentication failure, invalid schema/output, and other dependency errors are
terminal. Provider SDK retries remain disabled. Each attempt consumes the
operation's model/tool request budget, and usage for a failed attempt is unknown.
Model request admission is released and reacquired between attempts. An MCP
server's admitted authenticated session remains held across tool-call backoff.
All attempts share the original operation deadline; the runtime does not retry a
whole step, flow, or workflow.

## Environment references

Only fields marked as deployment environment fields expand `$NAME`. Examples
include model IDs, endpoints, credentials, telemetry headers, and MCP process
settings. `$$` is a literal dollar. Prompts, instructions, bindings, schemas,
documents, and customer input stay literal.

The application loads `.env` beside the selected settings file and then
overlays the environment passed by the host; process values win. Preparation
and the offline CLI commands do not read environment values.

Use environment references for model API keys; inline step profiles require them.
Named deployment profiles also accept literals. Keep secrets out of YAML, source
control, logs, telemetry, errors, and evaluation artifacts.

## MCP profiles

An MCP profile declares one Streamable HTTP or stdio transport plus an
operator-reviewed tool catalog:

```yaml
mcp:
  records:
    transport:
      type: streamable_http
      endpoint: $MCP_ENDPOINT
    auth: oauth
    identity_meta_key: example.com/runtime/identity
    catalog:
      tools:
        lookup:
          effect: read
          input_schema:
            type: object
            properties:
              reference:
                type: string
            required:
              - reference
            additionalProperties: false
          output_schema:
            type: object
            properties:
              status:
                type: string
            required:
              - status
            additionalProperties: false
```

Current runtime access is read-only. Each MCP operation names exactly one
declared server and tool; an LLM operation with tools separately allowlists its
server and tool names. The runtime validates arguments and declared results.

`auth` is a name, not a credential. It must match a trusted
`McpCredentialProvider` supplied as the same key in
`RuntimePlugins(mcp_credentials={...})`. It is supported only for Streamable
HTTP. The provider creates fresh authorization for a scope containing the server
alias, endpoint, auth name, and current caller identity. Missing named providers
fail before a session opens.

`SdkOAuthCredentialProvider` delegates OAuth discovery, PKCE, state, refresh,
and resource handling to the MCP SDK. The host supplies client metadata,
identity-partitioned token storage, an allowlist of HTTPS authorization-server
origins, and optional operator UI callbacks. Foliqant does not store tokens or
launch an authorization UI.

`identity_meta_key` optionally sends non-null tenant/principal identifiers in
MCP request metadata under an explicit reverse-DNS key. This conveys context to
the remote tool; it does not authenticate the caller.

The default tool authorizer enforces the compiled allowlist. A host can inject a
stricter resource-aware `ToolAuthorizer`. The embedding service still owns
caller authentication and application permission.

## Telemetry

`telemetry` can export traces and metrics over OTLP/HTTP:

```yaml
telemetry:
  service_name: support_runtime
  traces_endpoint: $OTLP_TRACES_ENDPOINT
  metrics_endpoint: $OTLP_METRICS_ENDPOINT
  traces_headers:
    authorization: $OTLP_TRACES_AUTH
  metrics_headers:
    authorization: $OTLP_METRICS_AUTH
  allow_insecure_http: false
  span_queue_capacity: 2048
  span_batch_size: 512
  span_schedule_delay: 5
  metric_export_interval: 60
  metric_export_batch_size: 512
  export_timeout: 10
  shutdown_timeout: 10
```

`service_name` is required. Endpoints and protected headers are optional
environment fields; an empty endpoint disables that signal. Batch sizes and
capacities are positive integers, and `span_batch_size` cannot exceed
`span_queue_capacity`. Schedule and metric intervals are positive and at most
3600 seconds. Export and shutdown timeouts are positive and at most 30 seconds.
HTTP endpoints require `allow_insecure_http: true`; use HTTPS in deployed
environments.

Telemetry records allowlisted configured component labels, trace identifiers,
timings, statuses, retry attempt numbers, and usage. It does not record
tenant/principal identity, payloads, metadata, prompts, model output,
credentials, or raw exceptions. Application embedding does not replace the
process-global OpenTelemetry provider unless the host explicitly passes
`install_global_telemetry=True`. Foliqant also does not replace host logging;
configure a reviewed log sink and filters for SDK and third-party logs.

Shutdown stops intake and waits only for the configured bounded telemetry drain.
It can report an incomplete drain while an exporter socket worker is still
finishing; it does not claim to terminate that worker or a remote operation.

## Evaluation dataset

The conventional dataset is `evaluation/dataset.json` beside `config/`.
`foliqant evaluate` selects it only when evaluation is explicitly invoked.
Use a configured path for a custom location:

```yaml
evaluation:
  dataset: ../../reviewed-gold/support.json
```

The path is relative to `config/settings.yaml`. Normal preparation, startup,
`validate`, `doctor`, and `run` do not open or inspect conventional or
configured gold. Only evaluation commands load it, and the selection does not
affect the compiled runtime configuration digest.

Use [evaluation](../evaluation/index.md) for the dataset
shape and pipeline, flow, and operation scopes.

## CLI reference

```text
foliqant init DEST
foliqant validate [--config PATH]
foliqant explain [--config PATH] [--workflow NAME]
foliqant doctor [--config PATH]
foliqant run [--config PATH] --workflow NAME --input PATH|-
foliqant evaluate [--config PATH] [--check | --replay REPORT]
foliqant evaluate --compare CANDIDATE --baseline BASELINE [--output PATH]
```

Commands that use runtime configuration default to
`./config/settings.yaml`; they do not search parent directories.

`run` accepts one bounded envelope and prints one safe JSON result.
`evaluate --check` validates gold, targets, and structural pointers without
opening providers. `evaluate --replay` rescores saved public results without
inference. A normal evaluation executes its configured scopes. Use
`--max-concurrency`, `--timeout`, and `--repeat` explicitly when changing
their conservative defaults.

Reports default to unique files under `.foliqant/evaluations/` beside the
settings file. `--output` selects a new path and never overwrites. These
artifacts may contain complete inputs, expectations, and public results; keep
them private and out of Git.

Exit codes are `0` for success, `1` for a gold mismatch, `2` for invalid
input or configuration, `3` for a missing optional dependency, `4` for a
runtime failure, and `130` for interruption.

## Runtime boundary

The library has no storage, worker, job lookup, migration, or application-login
subsystem. Each call runs in the foreground and returns one
`ExecutionResult`. A remote host owns transport, authentication, rate control,
idempotency, and persistence.
