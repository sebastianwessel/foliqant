# Retry a flow with `repeat`

Some work legitimately needs a second, bounded try: look up a fund, and if it
is not found let a model propose a corrected identifier and look it up again.
`repeat` expresses this on one flow instance. Repetition is an attribute of the
node, not an edge, so the workflow graph stays acyclic and every run still
terminates.

## Configure a repeated flow

```yaml
flows:
  lookup_fund:
    input:
      identifier:
        pointer: /payload/identifier
    repeat:
      max_attempts: 2
      until:
        binding:
          pointer: /flows/lookup_fund/result/status
        equals: found
      retry:
        flow: correct_identifier
        input:
          lookup:
            pointer: /flows/lookup_fund/result
          message:
            pointer: /payload/message
        continue_when:
          binding:
            pointer: /flows/correct_identifier/result/status
          equals: accepted
      retry_input:
        identifier:
          pointer: /flows/correct_identifier/result/identifier
    transition:
      route:
        - when:
            binding:
              pointer: /flows/lookup_fund/result/status
            equals: found
          flow: enrich
        - flow: request_details
  correct_identifier:
    callable: true
```

| Field | Rule |
| --- | --- |
| `max_attempts` | total attempts including the first, 2 to 64 |
| `until` | a [condition](conditions.md) evaluated after every attempt; `/flows/<self>/result` is the latest attempt |
| `retry.flow` | a callable flow run before every further attempt; one callable flow serves at most one repeat |
| `retry.input` | bindings for the retry flow, which may read `/flows/<self>/result` and `/flows/<self>/attempts` |
| `retry.continue_when` | optional condition evaluated after the retry flow; false stops repeating |
| `retry_input` | overrides keys of the flow's `input` for attempts two and later; may read the retry flow's result |

`retry_input` keys must exist in `input`. The retry flow's input is checked
against its `input_schema`, like a routed flow's input. Without `retry`, the
flow repeats with the same input, which is useful when the result depends on
external state (a read that may succeed later). Repeating a model step with
identical input is not a retry strategy, so that combination reports the
`repeat_without_retry` warning.

## What happens at run time

1. Run the flow with `input`.
2. If the attempt needs review or fails, stop: review follows `on_unresolved`,
   failure fails the run.
3. If `until` holds, stop.
4. If this was attempt `max_attempts`, stop: the flow is **exhausted**.
5. Run the retry flow. If it needs review, the repeated flow becomes
   `needs_review` and follows its `on_unresolved` (with the retry flow's review
   issues). If it fails, including when its `retry.input` cannot be bound or
   validated, the run fails. If `continue_when` is false, stop.
6. Run the next attempt with `input` overridden by `retry_input`, then go to 2.
   If that input cannot be bound or validated, or the deadline has passed, the
   run fails before the attempt starts.

When a retry run or the next attempt's input fails, the flow keeps its last
attempt as its result and status, `repeat.stopped_by` is `failure`, and the
run's `execution.error` carries the failure.

After the loop the flow's **result is its last attempt**, and its `transition`
is followed exactly as for a flow without `repeat`. Exhaustion is not a review:
route on the result to decide what an unsatisfied `until` means, as the
`route` above does.

Every attempt and every retry run consumes steps from `execution.max_steps`
and time from the run deadline. Validation computes the worst case,
`max_attempts × steps(flow) + (max_attempts − 1) × steps(retry flow)`, and
fails with `repeat_budget` when it alone exceeds `max_steps`. The `run_budget`
warning adds up the whole path, so a repeated flow with a collection counts
every attempt's items. Binding a later attempt (`/flows/<id>/attempts/1`) or
the retry flow's result needs a `default`, because those may not exist; see
[what the compiler guarantees](validation.md#bindings-resolve-on-every-path).

## Read attempts in the result

The flow record describes the last attempt at the top level and keeps every
attempt:

```json
{
  "status": "completed",
  "result": {"status": "found", "fund": "LU0000000002"},
  "steps": {"lookup": {"status": "completed", "result": {"status": "found"}}},
  "attempt_count": 2,
  "attempts": [
    {"attempt": 1, "status": "completed", "result": {"status": "not_found"}, "steps": {}},
    {"attempt": 2, "status": "completed", "result": {"status": "found"}, "steps": {}}
  ],
  "attempts_usage": {"model_requests": 0, "tool_calls": 2},
  "repeat": {"stopped_by": "until"}
}
```

`repeat.stopped_by` is `until`, `exhausted`, `continue_when`, `review` or
`failure`. `usage` and `elapsed_seconds` describe the last attempt;
`attempts_usage` and `attempts_elapsed_seconds` sum all attempts. The retry
flow appears under `flows` like any flow: its top level is the last run and
`attempts` lists every run.

Later flows may bind `/flows/lookup_fund/result` (the last attempt) and
`/flows/lookup_fund/attempts`. They may read the retry flow's result only with
a `default`, because it runs only when a retry happened:

```yaml
correction:
  pointer: /flows/correct_identifier/result
  default: null
```

## Repeat every collection item

A callable flow may declare `repeat` as well. Every
[collection](../steps/flow-collection.md) item that invokes it then repeats on
its own, with the same rules:

```yaml
flows:
  lookup_item:
    callable: true
    definition: lookup_item/flow.yaml
    repeat:
      max_attempts: 2
      until:
        binding:
          pointer: /flows/lookup_item/result/status
        equals: found
      retry:
        flow: correct_item
        input:
          lookup:
            pointer: /flows/lookup_item/result
          identifier:
            pointer: /payload/identifier
      retry_input:
        identifier:
          pointer: /flows/correct_item/result/identifier
  correct_item:
    callable: true
    definition: correct_item/flow.yaml
```

Its pointers read the item scope: `/payload` is the item's `input`, plus
`/metadata`, `/flows/{self}/result` and `/attempts`, and the retry flow's
`/flows/{retry}/result` and `/attempts`. No workflow-level flow results are
visible, and no route outside the item can read these records. `retry.flow`
must be another callable flow without its own `repeat`. `retry_input` keys
override keys of the item input; with an `input_schema`, they must be declared
properties. The collection ledger entry carries `attempt_count`, `attempts` and
`repeat.stopped_by` like a routed flow, plus `retry` with the item's retry flow
runs. A retry or input failure fails the collection like a failing item. The
`collection_budget` warning counts the worst case of every item's attempts and
retry runs.

## Isolated runs and tracing

`WorkflowApplication.run_flow` executes one attempt of a repeated flow: repeat
belongs to the workflow graph and reads workflow-scope results. In traces each
attempt is a `flow` span with `foliqant.flow.attempt` and
`foliqant.flow.max_attempts`; the retry flow's span has
`foliqant.flow.role = retry` and is a child of the attempt it follows. The final
attempt carries a `repeat.stopped` event. See
[observability](../integration/observability.md).

The [retry tutorial](../tutorials/retry-lookup.md) builds this pattern step by
step with a real MCP lookup.
