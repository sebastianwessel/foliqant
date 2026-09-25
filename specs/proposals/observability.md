# Observability: routes, conditions, repeats, host propagation, logs

## Goal

A run is traceable end to end — from the host's inbound message through every flow attempt,
step, model and tool call — with labels that are safe (ids, statuses, operators; never bound
values, prompts or customer text), and a host can correlate its own logs and spans with the
runtime's.

## Spans (OpenTelemetry, `telemetry` extra)

Existing: `workflow.run`, `flow`, `step`, model and tool spans. Additions:

| Span / event | Attributes |
|---|---|
| `flow` | `foliqant.flow.attempt`, `foliqant.flow.max_attempts` (repeat), `foliqant.flow.role` = `routed` / `callable` / `retry`, `foliqant.collection.item` |
| `step` | `foliqant.step.skipped = true` with event `step.skipped` (`condition` = condensed condition text: pointers and operators only) |
| event `route.selected` on the flow span | `kind` = `direct` / `cases` / `route` / `review`, `case`, `index`, `target` |
| event `repeat.stopped` | `stopped_by` |
| event `condition.evaluated` (debug level, opt-in via `telemetry.conditions: true`) | `location`, `result` |

## Host propagation

`WorkflowApplication.run(..., transport_trace=...)` accepts a W3C carrier (exists). Additionally
`ExecutionResult.execution.trace` returns `{trace_id, span_id}` of the run span so a host can
log the ids and link its outbound messages. `StepContext.trace` (handlers.md) carries the
current carrier into handlers.

## Structured logging

`foliqant.adapters.telemetry.logging.configure_logging` (exists) is documented as the way to
emit JSON logs with `trace_id` / `span_id`; every runtime log event carries `workflow`, `flow`,
`step`, `attempt` and `execution_id` labels where known. Events added: `route.selected`,
`step.skipped`, `repeat.stopped`, `handler.review` (with issues), `condition.type_mismatch`
(a comparison operator met an incompatible runtime type — evaluated to false).

## Fault tolerance (unchanged model, documented)

* Provider and MCP calls: configured retry policies with backoff; per-request timeouts;
  admission limits. `repeat` is not a retry policy for technical failures — those fail the run
  so the host's transport redelivers.
* A run that fails leaves partial results (`flows`, `attempts`, `partial_result`) for
  diagnosis.

## Documentation

* `docs/integration/observability.md`: spans, events, attributes table, host propagation,
  logging setup, what to alert on.
* `skills/foliqant/references/deployment-http.md`: telemetry checklist.

## Implementation notes (normative text now in `../runtime.md`)

Implemented as specified, with these refinements:

* Collection items are traced by `foliqant.collection.index`, not their ID:
  item IDs are business data and `../collections.md` forbids them in telemetry.
  Handlers still receive the ID as `StepContext.collection_item`.
* Every span carries `foliqant.execution.id`, the same value as
  `execution.id` in the result; metrics keep low-cardinality labels only.
* The start selection is a `route.selected` event on the workflow span.
* Log event names are snake case (`route_selected`, `step_skipped`,
  `repeat_stopped`, `handler_review`, `condition_type_mismatch`, debug
  `condition_evaluated`), matching the existing event vocabulary.
* Telemetry labels come from the resolved configuration (`$MODEL_ID` resolves
  before labels are built); configuration values that cannot be exported safely
  are omitted with one `telemetry_labels_dropped` warning instead of failing
  activation.
* Streamable-HTTP MCP requests carry the current `traceparent`/`tracestate` as
  HTTP headers, so a remote server can join the trace. The carrier is captured
  when each tool call is made and placed in that request's `_meta`; the HTTP
  transport derives the headers from the request's own `_meta`, so concurrent
  calls on one session never exchange trace context (no session-level holder).
* Host provider (review decision): `RuntimePlugins(tracer_provider=...)` makes
  runtime spans join a host-owned OpenTelemetry provider; no runtime provider,
  exporter or metric pipeline is created, `install_global_telemetry=True` is
  rejected with `invalid_configuration`, and the host provider is never shut
  down. Span names (`workflow <id>`, `flow <id>`, `step <id> (<type>)`,
  `execute_tool <tool>`; see [usage-and-pricing.md](usage-and-pricing.md)) and
  event attributes are sanitized when recorded, so host processors receive the
  same content as the runtime exporter; model spans keep PydanticAI's
  content-free attributes, which only the runtime exporter narrows further.
* `condition.type_mismatch` carries `reason`: `incompatible_type` or
  `value_too_long` (a `matches` value over 1024 characters).
