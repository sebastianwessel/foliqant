# Runtime configuration and CLI

The deployment file is strict YAML with `version: 1`. It binds workflow names
to bundle directories and names the model, MCP, execution, and telemetry
profiles available at startup.

```yaml
version: 1
workflows:
  support_triage: workflows/support_triage
models: {}
mcp: {}
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

Limits are per process. They bound admitted runs, graph size, time, and attempts;
they are not a distributed rate limiter or retry policy. Every started request
consumes an attempt even if it fails. Unavailable token counts remain unknown.

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
    api_key_env: null
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

For authenticated providers, `api_key_env` names an environment variable. Put
the value in the process environment or the deployment directory's `.env`.
`load_environment(config_path, os.environ)` reads that file once without
interpolation, then lets process values take precedence. Never put secret values
in YAML.

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

See the [local public-request example](../../examples/public_request_mcp/README.md)
for a complete stdio profile and authorizer.

## Telemetry

Telemetry is optional and uses explicit OTLP/HTTP trace and metric endpoints.
When a signal endpoint is absent, Foliqant creates no exporter for that signal.
Ambient OTLP endpoints and headers are not discovered.

Observations use bounded configured labels and fixed error codes. Business
payloads, arbitrary metadata, credentials, prompts, model output, and exception
text must not enter logs or telemetry. Only protected W3C `traceparent` and
`tracestate` values are propagated; baggage is excluded.

## CLI

```text
foliqant init DEST
foliqant validate --config PATH
foliqant doctor --config PATH
foliqant explain --config PATH [--workflow NAME]
foliqant run --config PATH --workflow NAME --input PATH|-
             [--tenant-id ID] [--principal-id ID] [--debug]
```

`init` refuses to overwrite an existing destination. `validate`, `doctor`, and
`explain` are offline. `run` reads one strict JSON envelope from a regular file
or stdin and prints one result. It has no `serve`, worker, migration, or job
status command.

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
