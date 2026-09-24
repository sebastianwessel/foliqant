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

It excludes payloads, prompts, model outputs, condition operands, tenant and
principal IDs, credentials, and raw exception text. Labels come from the
resolved configuration: a model profile written as `model: $MODEL_ID` is
labelled with the resolved model ID. A configured value that cannot be exported
safely (for example a model ID with characters outside the label alphabet) is
omitted and reported once with the `telemetry_labels_dropped` log event; it
never fails activation or disables the remaining telemetry. These restrictions
do not configure third-party SDK logs or your HTTP server; review their log
settings separately.

Usage can be unknown when a provider did not report it. `null` means unknown,
not zero. Workflow, flow, and step totals overlap; use the workflow total for
the complete call instead of adding every level. The result also splits usage
by provider model and, with [configured pricing](../configuration/models.md#estimate-cost-with-pricing),
estimates its cost; see [usage by model](results.md#read-usage-by-model-and-cost).

## Span names

Spans are named by what they are, using configuration IDs and fixed words only,
so a trace reads like the workflow configuration and names stay low-cardinality:

| Span | Name | Example |
| --- | --- | --- |
| Run | `workflow <workflow id>` | `workflow support_multi` |
| Flow run | `flow <flow id>` | `flow assess` |
| Repeat attempt 2 onwards, or a later retry-flow run | `flow <flow id> #<attempt>` | `flow lookup_fund #2` |
| Callable flow inside a collection | `flow <flow id> [item <index>]` | `flow billing_task [item 0]` |
| Step | `step <step id> (<type>)` | `step extract (llm)`, `step lookup (mcp)`, `step classify (decision)` |
| Model request | `chat <model>` | `chat gpt-5.6-terra` |
| Model tool call or direct MCP call | `execute_tool <tool>` | `execute_tool lookup_account` |

The step type is `decision`, `llm`, `mcp`, `handler` or `flow_collection`. The
first attempt carries no suffix; a collection item with a repeated flow reads
`flow billing_task [item 0] #2`. A name that is not an exportable label is left
out rather than replaced by a runtime value (the span is then just `step (llm)`).
Collection item IDs are business data and never appear; the item index does.

This is a trace of the tutorial's multi-request workflow: a decision, a planning
handler, a collection of two callable flows that each call an MCP tool, and a
final handler.

```text
workflow support_multi
  flow assess
    step identify (decision)
      chat qwen3-30b-a3b
  flow plan
    step plan (handler)
  flow process
    step requests (flow_collection)
      flow billing_task [item 0]
        step lookup (mcp)
          execute_tool lookup_account
        step prepare (handler)
      flow cancellation_task [item 1]
        step lookup (mcp)
          execute_tool lookup_account
        step prepare (handler)
  flow finalize
    step disposition (handler)
```

Queries by attribute keep working: every name is also available as a
`foliqant.*` attribute below, and the instrumentation scopes remain
`foliqant.workflow`, `foliqant.flow`, `foliqant.step` and `foliqant.tool`.

## Spans, attributes and events

| Span or event | Content |
| --- | --- |
| Workflow span | `foliqant.workflow.name`, `foliqant.execution.id`, outcome; event `route.selected` for the start |
| Flow span | `foliqant.flow.name`, `foliqant.flow.role` (`routed`, `callable`, `retry`), `foliqant.flow.attempt` and `foliqant.flow.max_attempts` for repeated and retry flows, `foliqant.collection.index` inside collections |
| Step span | `foliqant.step.name`, `foliqant.step.kind` (the step type); a skipped step has `foliqant.step.skipped = true`, outcome `skipped` and a `step.skipped` event |
| Model span (`chat <model>`) | [OTel GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/) `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model` (the configured model or a dated snapshot of it), `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and where the provider reports them `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens` and `gen_ai.usage.reasoning.output_tokens`; `foliqant.usage.cost` (the request's estimate, rounded to six decimals) when the profile configures pricing and every count the estimate needs was reported |
| Tool span (`execute_tool <tool>`) | `gen_ai.operation.name`, `gen_ai.tool.name`, `foliqant.request.attempt` |
| `route.selected` event (flow span) | `kind` (`direct`, `cases`, `route`, `review`), `index`, `case`, `target` |
| `repeat.stopped` event (last attempt) | `stopped_by` |
| `step.skipped` event | `condition`: the condensed condition, pointers and operators only |
| `handler.review` event | `issues` reported by a handler |
| `condition.type_mismatch` event | `location`, `operator` and `reason` (`incompatible_type`, or `value_too_long` for a `matches` value over 1024 characters) of a comparison evaluated as false |
| `condition.evaluated` event (opt-in) | `location` and boolean `result` of every evaluated condition |

Enable the debug-level `condition.evaluated` events with:

```yaml
telemetry:
  service_name: support_assistant
  traces_endpoint: $OTLP_TRACES_ENDPOINT
  conditions: true
```

A retry flow's span is a child of the attempt it follows, so one trace shows
`flow lookup_fund`, `flow correct`, `flow lookup_fund #2`. Every span carries
the run's `foliqant.execution.id`, the same ID as `execution.id` in the result.

## Propagate the trace across your system

- **Into the run**: pass the inbound carrier with
  `app.run(..., transport_trace=TraceContext(traceparent, tracestate))`.
- **Out of the run**: `ExecutionResult.execution.trace` returns `trace_id` and
  `span_id` of the run span; log them with your own records.
- **Into handlers**: `StepContext.trace` is the W3C carrier of the current step
  span. Put it on the handler's own outbound requests.
- **Into MCP servers**: every call to a streamable-HTTP MCP server carries the
  `traceparent` and `tracestate` of its own tool-call span, in the MCP request
  `_meta` and as HTTP headers of that same request, so a remote server can join
  the trace. The carrier is captured per call: concurrent calls on one session
  never exchange trace context, and initialization or discovery requests carry
  none. Stdio servers receive the carrier in `_meta` only.

## Log events

`foliqant.adapters.telemetry.logging.configure_logging` installs a bounded JSON
log sink that adds the active `trace_id` and `span_id` to each event. Lifecycle
events (`run_*`, `flow_*`, `step_*`) and the routing events `route_selected`,
`step_skipped`, `repeat_stopped`, `handler_review` and
`condition_type_mismatch` carry `workflow`, `flow`, `step`, `attempt` and
`execution_id` where known, plus fixed fields such as `route_kind`, `target`,
`stopped_by`, `operator`, `reason` and `issues`. Logs never contain payloads or operand
values. Alert on `run_failed`, on unexpected `repeat_stopped` with
`stopped_by: exhausted` rates, and on `condition_type_mismatch`, which usually
means a schema and a condition disagree.

## Review the graph

`foliqant explain --format mermaid` (or `dot`) renders every flow as a box
with its steps in order, shaped and colored by step type, conditional steps
dashed with their `when` on the incoming edge, routes between flows solid,
review edges dashed, collection and retry calls dotted into the grouped
callable flows, and repeats in the flow title (`lookup  ·  repeat ≤ 2 until
status equals found`), followed by the compiler diagnostics. Route labels show
the authored condition with its operands (`0: status equals found`; pointers
into the source flow's own result are shortened), so a reviewer can read the
decision rules from the diagram; span events and logs keep the condensed form
with pointers and operators only. `--legend` adds a key of step shapes; the
[CLI reference](../reference/runtime-configuration.md#explain-and-generated-documentation)
shows a rendered example.

Commit the rendered graphs with the configuration and check them in CI, so a
changed route cannot go unreviewed:

```sh
foliqant validate --strict
foliqant explain --format mermaid --all --output docs/workflows.md --check
```

The document starts with a legend and has one section per workflow with its
start, output projection, diagram and diagnostics; regenerate it without `--check`. Fix every warning
before release. Span and log names in production (`route.selected` with
`kind`, `index` and `case`) match the edge labels of the diagram, so an
observed route can be found in the reviewed graph. See [what the compiler
guarantees](../configuration/validation.md) for every check behind it.

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

The recommended integration for a host that already runs OpenTelemetry is to
hand its tracer provider to the runtime:

```python
from opentelemetry.sdk.trace import TracerProvider

from foliqant import RuntimePlugins, open_application

provider = TracerProvider()  # the host's provider, with the host's exporters
async with open_application(
    prepared,
    environment=os.environ,
    plugins=RuntimePlugins(tracer_provider=provider),
) as app:
    ...
```

Runtime spans then join the host's provider and its current trace: a run
started inside a host request span is a child of it. The runtime creates no
provider or exporter of its own, ignores the `telemetry` export settings (it
still honors `telemetry.conditions`), exports no metrics, never installs
process globals and never shuts the host provider down. Combining the plugin
with `install_global_telemetry=True` fails with `invalid_configuration`.

The host's processors see runtime spans as they are created. Workflow, flow,
step and tool spans and their events carry only configured names, fixed codes
and counts, so they look the same as through the runtime's own exporter. Model
spans come from PydanticAI with content capture disabled; they may include
provider metadata such as the model name and server address, which the
runtime's own exporter would drop. Filter those in your pipeline if needed.

Without the plugin, application embedding does not replace the process-global
OpenTelemetry provider by default. A host may explicitly pass
`install_global_telemetry=True` to `open_application` when it owns that
decision. Do not overwrite an existing host telemetry installation
unintentionally.

Use observability to find where time or failures accumulate. Use
[evaluation](../evaluation/index.md) to measure whether decisions match reviewed
business expectations: a fast successful API request can still produce the wrong
classification.
