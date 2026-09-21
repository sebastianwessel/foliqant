# Runtime configuration and CLI

The deployment file is strict YAML with `version: 1`. The smallest valid file
only maps a public workflow name to its bundle directory:

```yaml
version: 1
workflows:
  support_triage: workflows/support_triage
```

Add `models`, `mcp`, `execution`, or `telemetry` only when the workflow needs
them. An optional `evaluation` section points to local golden cases. Execution settings have safe defaults:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `concurrency` | `4` | Runs admitted at once per process |
| `queue_limit` | `16` | Additional runs allowed to wait |
| `run_timeout` | `300` | Total seconds for one run |
| `model_timeout` | `60` | Total seconds for one model attempt |
| `tool_timeout` | `30` | Total seconds for one tool attempt |
| `max_steps` | `32` | Maximum executed steps per run |
| `model_requests_per_step` | `4` | Maximum started model requests per step |
| `tool_calls_per_step` | `3` | Maximum started tool calls per step |

Limits are per process. They are not a distributed rate limiter or retry policy.
Every started request consumes an attempt even if it fails. Unavailable token
counts remain unknown.

## Model profiles

Profiles require an explicit provider and model ID. Foliqant does not discover a
model and reserves no alias such as `primary`.

```yaml
models:
  local_qwen:
    provider: openai_compatible
    api: chat
    model: incoai/Qwen3.8-27B-Splash
    base_url: http://127.0.0.1:8000/v1
    allow_insecure_http: true
    api_key: null
    output_mode: native
    supports_text: true
    supports_json_schema: true
    supports_tools: false
    concurrency: 1
    queue_limit: 0
    request_timeout: 300
    options:
      max_tokens: 8192
      temperature: 0.1
      reasoning_effort: low
```

Supported providers are `openai_compatible`, `openai`, `azure_openai`, and
`anthropic`. Provider/API combinations and structured-output modes are validated
before inference. The host validates model output against the original schema;
there is no automatic mode switch or output repair.

The remaining run time and `execution.model_timeout` bound the total model
attempt. A profile's `request_timeout` configures the provider SDK's network
timeout. Setting `request_timeout: 300` does not override the default 60-second
model-attempt limit; raise `execution.model_timeout` explicitly when a local
model needs more time.

## Select a model for one step

A `decision` or `llm` step inherits `defaults.model` from its workflow. Set
`model: local_qwen` on the step to select another declared profile. To adjust
one step without copying the profile, use:

```yaml
model:
  profile: local_qwen
  options:
    max_tokens: 4096
    temperature: 0.1
```

The override can also set `model` to a different provider model ID. Unspecified
options inherit the profile; explicit `null` clears an optional setting. The
merged settings are validated against the original provider's supported fields
and constraints. Derived steps share their source profile's admission limits,
so creating several variants does not multiply its allowed concurrency.

For a different provider or an independent connection, a step may contain a
complete provider configuration using the same fields as a deployment profile:

```yaml
model:
  provider: openai_compatible
  model: '$EXTRACTION_MODEL'
  base_url: '$EXTRACTION_URL'
  api_key: '$EXTRACTION_KEY'
  output_mode: native
  options:
    temperature: 0.1
```

Inline configurations do not inherit another provider's settings. Use environment
references for inline credentials; literal API keys in workflow files are
rejected. Only designated deployment fields accept references; prompts, binding
values and customer content are never expanded. Preparation validates the
configuration offline and opening resolves its environment once. No per-step
model discovery, request retry, or automatic provider fallback is added.

## Environment references

Use a complete `$VARIABLE` value in supported deployment fields:

```yaml
models:
  assistant:
    provider: openai_compatible
    model: '$MODEL_NAME'
    base_url: '$MODEL_URL'
    api_key: '$MODEL_KEY'
    output_mode: native
```

The reference-capable fields are:

| Configuration | Fields |
| --- | --- |
| Model profile | `model`, `api_key`, compatible `base_url`, Azure `endpoint` and `api_version` |
| MCP HTTP transport | `endpoint` |
| MCP stdio transport | `command`, each `args` entry, `cwd`, each `env` value |
| Telemetry | `traces_endpoint`, `metrics_endpoint`, each `traces_headers` and `metrics_headers` value |

Other configuration, workflow instructions, literal bindings, schemas, and
customer input remain literal. Provider names, flags, numeric limits, workflow
paths, and registration IDs do not accept references.

Variable names follow `[A-Za-z_][A-Za-z0-9_]*`. Only the full value can be a
reference: `${NAME}`, `$NAME/suffix`, `prefix$NAME`, and shell expressions are
rejected. `$$` escapes a literal dollar: `$$NAME` resolves to `$NAME`, and
`prefix$$NAME` resolves to `prefix$NAME`.
Resolved values are never expanded again. Nothing executes a shell or performs
interpolation.

`open_application(prepared, environment=os.environ)` reads `.env` beside the
prepared deployment once when opening, then lets the supplied process mapping
override file values. `.env` interpolation is disabled, and opening does not
change the process environment. `load_environment(config_path, environment)`
remains available when a host explicitly needs the merged mapping; it is not a
required step before opening an application.

`prepare_application`, `validate`, `doctor`, and `explain` validate reference
syntax and compile local workflows without reading environment values, resolving
credentials, or constructing SDK clients. Opening requires every declared
reference to be present and nonblank, even a header on a disabled telemetry
signal. It validates resolved URLs, Azure API flavor, absolute stdio working
directories, header values, and other field constraints before constructing
clients. These are local configuration checks; they do not probe endpoints or
verify tokens.

`openai`, `anthropic`, and `azure_openai` default `api_key` to
`$OPENAI_API_KEY`, `$ANTHROPIC_API_KEY`, and `$AZURE_OPENAI_API_KEY`, respectively.
An unauthenticated compatible endpoint may use `api_key: null`. Prefer references
for credentials. API keys, telemetry headers, and stdio environment values use
protected secret values: authored references remain visible in JSON exports,
while secret literals and resolved secrets are redacted in exports and `repr`.
Resolved secrets do not enter configuration revisions, diagnostics, or logs.
Revisions depend on authored references and nonsecret configuration, not the
current values of environment variables.

## MCP profiles

An MCP profile fixes its transport and an operator-reviewed tool catalog. Each
tool has exact input/output schemas and an effect. Current workflow execution is
read-only; write tools are rejected.

Discovery must match every declared tool and schema before use. Extra discovered
tools receive no permission. Each workflow step also allowlists its tool. A host
authorizer still decides whether the current trusted caller may access the
specific resource.

Streamable HTTP endpoints must be explicit. Authentication hooks are registered
by the embedding application and partition credentials by the complete caller
scope. Stdio commands, arguments, working directory, and environment are trusted
startup configuration; request data cannot select them.

See the [local public-request example](https://github.com/sebastianwessel/foliqant/blob/main/examples/public_request_mcp/README.md)
for a complete stdio profile and authorizer.

## Telemetry

Telemetry is optional and uses explicit OTLP/HTTP trace and metric endpoints.
When a signal endpoint is absent, Foliqant creates no exporter for that signal.
Ambient OTLP endpoints and headers are not discovered. Configure header values
with the same reference syntax:

```yaml
telemetry:
  service_name: support-triage
  traces_endpoint: '$OTLP_TRACES_ENDPOINT'
  traces_headers:
    authorization: '$OTLP_AUTHORIZATION'
```

The variable must contain the complete header value, including any required
scheme such as `Bearer `. Use `metrics_headers` for metric exporter headers.

Observations use bounded configured labels and fixed error codes. Business
payloads, arbitrary metadata, credentials, prompts, model output, and exception
text must not enter logs or telemetry. Only protected W3C `traceparent` and
`tracestate` values are propagated; baggage is excluded.

## Evaluation dataset

```yaml
evaluation:
  dataset: .foliqant/evaluation/gold.json
```

The dataset is a strict JSON file resolved relative to the deployment file.
`prepare_application`, application startup, `validate`, and `doctor` do not open
or inspect it. Evaluation metadata is excluded from the runtime configuration
digest. Only the explicit evaluation commands load gold or saved reports.

Use [testing and evaluation](../guides/testing-and-evaluation.md) for a complete
dataset, isolated-step checks, metric definitions, and replay. Keep gold and full
result artifacts in an ignored private directory. No thresholds, model judges,
or executable scorer imports are accepted in this configuration.

## CLI

```text
foliqant init DEST
foliqant validate [--config PATH]
foliqant doctor [--config PATH]
foliqant explain [--config PATH] [--workflow NAME]
foliqant run [--config PATH] --workflow NAME --input PATH|-
             [--tenant-id ID] [--principal-id ID] [--debug]
foliqant evaluate [--config PATH] [--check | --replay REPORT]
                  [--output PATH] [--max-concurrency 1] [--timeout 300] [--repeat N]
foliqant evaluate --compare CANDIDATE --baseline BASELINE [--output PATH]
```

`init` refuses to overwrite an existing destination. The other commands default
to `foliqant.yaml` in the current directory; they do not search parent
directories. `validate`, `doctor`, and `explain` are offline. `run` reads one
strict JSON envelope from a regular file or stdin and prints one result. It has
no `serve`, worker, migration, or job status command.

`evaluate --check` validates gold, targets, and structural result paths offline.
`evaluate` runs dataset suites sequentially against the configured application;
one case runs at a time by default. `--timeout` is a per-case deadline in seconds.
`evaluate --replay REPORT` rescores saved full results without running workflows
or opening model/tool clients. A normal or replay run writes a new report under
`.foliqant/evaluations/` beside the configuration, unless `--output` selects a
new path. Existing report files are never overwritten. Each dataset/case file is
limited to 64 MiB; report writing and replay share a 256 MiB limit. Stdout contains only a
content-free summary; the artifact contains sensitive inputs, gold, and public
results. Exit codes are `0` for passing checks, `1` for gold mismatch, `2` for
invalid input/configuration, `3` for missing dependencies, and `4` for runtime
failure. Runtime failure takes precedence even when an assertion expected it.

`--repeat N` requests additional attempts per source case; it does not create
new independent gold. Replayed attempts retain their original repetition
identities. `--compare` compares two compatible saved reports fully offline and
does not load application configuration. See
[evaluation results](../guides/evaluation-results.md) for interpreting metrics,
repeatability, and per-case changes without confusing coverage with accuracy.

Success is one JSON object on stdout. Failures use a fixed safe error object and
nonzero exit status. `--debug` changes approved diagnostics only; it does not
print customer content or raw dependency exceptions.

## Runtime ownership

`open_application` owns model and MCP clients for its async context. Keep it open
for the application's serving lifetime and close it after intake stops. A call
to `application.run` remains in memory until it returns. Process termination
loses unfinished runs.

Handlers are registered Python callables with explicit schemas. Configuration
never imports arbitrary code. Blocking-only integrations must use the bounded
blocking executor and an SDK-level timeout; cancellation cannot forcibly stop a
running Python thread or prove whether an external side effect occurred.
