# Bounded repetition (`repeat`)

## Motivation

The graph is acyclic and a flow instance runs once, which is what makes "no dead end"
provable. But a bounded retry — "look the fund up; if it is not found, let a model propose a
corrected identifier and look it up once more" — is a legitimate, terminating pattern that today
must be unrolled by hand into distinct instances (`lookup_fund`, `correct_identifier`,
`lookup_corrected_fund`), each with its own bindings and optional defaults. `repeat` expresses
the pattern on one flow instance while the static graph stays a DAG: repetition is an attribute
of a node, not an edge.

## Configuration

```yaml
flows:
  lookup_fund:
    definition: ../shared/lookup_fund/flow.yaml
    input:
      identifier: {pointer: /flows/identify_missing_fields/result/fund/identifier}
      identifier_type: {pointer: /flows/identify_missing_fields/result/fund/identifier_type}
    repeat:
      max_attempts: 2                       # 2..64 total attempts (first run + retries)
      until:                                # stop when true after an attempt (evaluated on the latest attempt)
        binding: {pointer: /flows/lookup_fund/result/status}
        equals: found
      retry:                                # optional: work between two attempts
        flow: correct_identifier            # a callable flow declared under `flows:`
        input:
          request: {pointer: /flows/identify_missing_fields/result/fund}
          lookup: {pointer: /flows/lookup_fund/result}          # the failed attempt
          conversation: {pointer: /payload/conversation}
        continue_when:                      # optional: stop repeating when false after the retry flow
          binding: {pointer: /flows/correct_identifier/result/status}
          equals: accepted
      retry_input:                          # optional: input overrides for attempts ≥ 2
        identifier: {pointer: /flows/correct_identifier/result/identifier}
        identifier_type: {pointer: /flows/correct_identifier/result/identifier_type}
    transition:
      route:
        - when: {binding: {pointer: /flows/lookup_fund/result/status}, equals: found}
          flow: enrich
        - flow: enrich
    on_unresolved: {flow: enrich}
  correct_identifier:
    callable: true
    definition: ../shared/correct_identifier/flow.yaml
```

| Field | Shape | Rule |
|---|---|---|
| `max_attempts` | integer 2..64 | total attempts including the first |
| `until` | condition | evaluated after every attempt in workflow scope with the attempt's result at `/flows/<self>/result`; true → stop |
| `retry.flow` | callable flow id | runs before every retry (attempts 2..n); may not be the repeated flow, a routed flow or a flow used by a collection with a different input schema |
| `retry.input` | binding map | resolved in workflow scope plus `/flows/<self>/result` and `/flows/<self>/attempts` |
| `retry.continue_when` | condition | evaluated after the retry flow; false → stop repeating, keep the last attempt's result |
| `retry_input` | binding map | overrides `input` keys for attempts ≥ 2; may reference the retry flow's result |

`repeat` without `retry` repeats the flow with the same input (useful with `until` on a result
that depends on external state — the same MCP read may succeed later). Only `mcp` and
`handler` steps are retried this way meaningfully; the compiler warns (`repeat_without_retry`)
when the repeated flow contains a model step and `retry` is absent, because a model call with
identical input is not a retry strategy.

## Semantics

```
attempt = 1
run flow with input
loop:
  if flow.status == needs_review or failed: break        # review → on_unresolved; failure → run fails
  if until(result): break
  if attempt == max_attempts: break                        # exhausted: continue with the last result
  if retry: run retry flow with retry.input
            if retry.status == needs_review: mark repeated flow needs_review; break
            if retry.status == failed: run fails
            if continue_when and not continue_when(): break
  attempt += 1
  run flow with input ∪ retry_input (per-key override)
```

After the loop the repeated flow's **public result is the last attempt**, and its transition
(or `on_unresolved`) is followed exactly as for a non-repeated flow. Exhaustion is not a review:
the configuration decides what an unsatisfied `until` means by routing on the result.

### Results

`FlowResult` gains:

```json
"attempts": [ {"attempt": 1, "status": "completed", "steps": {...}, "result": {...}, "usage": {...}, "elapsed_seconds": 0.4},
              {"attempt": 2, ...} ],
"attempt_count": 2,
"repeat": {"stopped_by": "until" | "exhausted" | "continue_when" | "review" | "failure"}
```

Top-level `status`, `steps`, `result`, `usage` and `elapsed_seconds` describe the **last**
attempt (`usage` and `elapsed_seconds` additionally sum over attempts in `attempts_usage`,
`attempts_elapsed_seconds`). The retry flow's records are exposed under `/flows/<retry flow id>`
like any flow (last run; `attempts` for all runs). Non-repeated flows have `attempt_count: 1`
and no `attempts`.

Boundary bindings may read `/flows/<id>/result` (last attempt) and `/flows/<id>/attempts`
(the list) of a repeated flow, and the retry flow's `/flows/<retry>/result` — the latter only
with a `default`, because the retry flow runs only when at least one retry happened.

### Budgets

Every attempt and every retry-flow run consumes steps from `execution.max_steps` and time from
the run deadline. The compiler estimates the worst case
(`attempts × steps(flow) + (attempts − 1) × steps(retry)`) and fails with `repeat_budget`
when it alone exceeds `max_steps`.

## Static validation

| Diagnostic | Level | When |
|---|---|---|
| `invalid_repeat` | error | `max_attempts` out of range; `retry_input` key not in `input`; `retry.flow` not callable; `retry.flow` equals the repeated flow; `retry_input` without `retry` referencing the retry flow |
| `repeat_without_retry` | warning | repeated flow has a model step and no `retry` |
| `repeat_budget` | error | worst case exceeds `execution.max_steps` |
| graph checks | — | the retry flow takes part in the cycle and reachability checks like a collection's callable flows (a callable flow may be used by one repeat and/or collections; its `input_schema`, when declared, validates `retry.input`) |

## Observability

Each attempt is a `flow` span with attributes `foliqant.flow.attempt` and
`foliqant.flow.max_attempts`; the retry flow's span has `foliqant.flow.role = retry`. A span
event `repeat.stopped` carries `stopped_by`. See `observability.md`.

## Documentation

* `docs/configuration/repeat.md` (new), linked from `workflows.md` and `steps/index.md`
  ("retry a lookup").
* `docs/integration/results.md`: `attempts`, `attempt_count`, `repeat.stopped_by`.
* `skills/foliqant/references/process-composition.md`: "bounded retry" section replacing the
  unrolling advice.
* Example `examples/conditional_intake` covers `repeat` with a retry flow (MCP lookup + scripted
  correction).

## Implementation notes (normative text now in `../runtime.md`)

Implemented as specified, with these refinements:

* `until` is required: a repeat without a stop condition would only run
  `max_attempts` times regardless of the result.
* `attempt_count` is the number of executions: `0` for a skipped flow, `1` for a
  flow that ran once. `attempts` is present for repeated flows and for retry
  flows (one entry per retry run); `repeat.stopped_by` only for repeated flows.
* The retry flow runs inside the span of the attempt it follows, so the final
  attempt's span always carries `repeat.stopped` and `route.selected`.
* A retry review marks the repeated flow `needs_review` and selects the review
  route with the retry flow's issues. A retry failure fails the run with the
  retry flow's error; the repeated flow keeps its last attempt's status with
  `stopped_by: failure`. A step-budget exhaustion inside an attempt records the
  failed attempt. A `retry.input` that cannot be bound or validated is a
  failed retry run; a next-attempt input that cannot be bound or validated, or
  a deadline reached between attempts, fails the run before the attempt starts.
  Both keep the last attempt with `stopped_by: failure`.
* Bindable attempt entries (`/flows/<id>/attempts/<n>`) hold `attempt`,
  `status`, `result` and `error`; step records, usage and timing appear only in
  the public result, so pointers to them are `dangling_pointer` errors.
* "A flow used by a collection with a different input schema" is enforced as:
  `retry.input` is validated statically and at run time against the callable
  flow's `input_schema`, exactly like collection items.
* `repeat_budget` counts each flow's own steps; a collection step counts as one
  and its child work is covered by the `collection_budget` warning.
* `run_flow` executes one attempt; repeat belongs to the workflow graph.
