# Observe a running workflow

First inspect the result returned to your application: its execution status,
run ID, duration, and measured usage. For production visibility across requests,
enable optional OpenTelemetry export to your existing collector.

## Enable traces and metrics

Install the `telemetry` extra, then add this fragment to `config/settings.yaml`:

```yaml
telemetry:
  service_name: support_assistant
  traces_endpoint: $OTLP_TRACES_ENDPOINT
  metrics_endpoint: $OTLP_METRICS_ENDPOINT
```

Use full OTLP/HTTP signal URLs supplied by your collector, typically ending in
`/v1/traces` and `/v1/metrics`. A missing endpoint or the exact empty string
disables that signal. Omitting `telemetry` disables the integration entirely.

For a collector requiring authorization, add environment-backed headers:

```yaml
telemetry:
  service_name: support_assistant
  traces_endpoint: $OTLP_TRACES_ENDPOINT
  traces_headers:
    authorization: $OTLP_TRACES_AUTH
```

Do not put the token directly in YAML. Endpoints use HTTPS by default; set
`allow_insecure_http: true` only for an intended HTTP collector, such as one on
localhost. This config does not create or deploy a collector.

## What you can observe

The integration records configured workflow, flow, and step names, timings,
statuses, safe error codes, model/tool attempts, retries, and provider-reported
token usage. W3C trace context is propagated without baggage. A host can supply
transport trace context when embedding the application.

It excludes payloads, prompts, model outputs, tenant/principal IDs, credentials,
and raw exception text. These restrictions do not configure third-party SDK logs
or your HTTP server; review their log settings separately.

Usage can be unknown when a provider did not report it. `null` means unknown,
not zero. Workflow, flow, and step totals overlap; use the workflow total for
the complete call instead of adding every level.

## Export and shutdown defaults

| Setting | Default | Meaning |
| --- | ---: | --- |
| `service_name` | Required | Stable application name, not a customer ID |
| `traces_endpoint` / `metrics_endpoint` | Disabled | Optional full OTLP/HTTP URLs |
| `traces_headers` / `metrics_headers` | Empty | Protected header mappings |
| `allow_insecure_http` | `false` | Explicit HTTP opt-in |
| `span_queue_capacity` | `2048` | Bounded in-memory trace buffer |
| `span_batch_size` | `512` | Spans per export, no greater than queue capacity |
| `span_schedule_delay` | `5` seconds | Trace export interval |
| `metric_export_interval` | `60` seconds | Metric export interval |
| `metric_export_batch_size` | `512` | Bounded metric batch size |
| `export_timeout` | `10` seconds | Export timeout |
| `shutdown_timeout` | `10` seconds | Bounded telemetry drain |

Queues are finite; telemetry is not a durable audit log. An incomplete drain does
not prove an exporter socket worker has stopped. Allow graceful shutdown, and keep
durable business audit requirements in your host application.

## Integrate with host telemetry

Application embedding does not replace the process-global OpenTelemetry provider
by default. A host may explicitly pass `install_global_telemetry=True` to
`open_application` when it owns that decision. Do not overwrite an existing host
telemetry installation unintentionally.

Use observability to find where time or failures accumulate. Use
[evaluation](../evaluation/index.md) to measure whether decisions match reviewed
business expectations: a fast successful API request can still produce the wrong
classification.
