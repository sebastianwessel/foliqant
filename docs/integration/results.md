# Read a workflow result

`app.run(...)` returns one `ExecutionResult`. Read `execution.status` first,
then use `payload` for the business value and `flows` for detail. Do not infer
success from the presence of a payload: on failure it preserves the accepted
input rather than fabricating a successful output.

| Field | What it tells your application |
| --- | --- |
| `payload` | The workflow's configured output projection; without one, the accepted input payload. |
| `metadata` | Accepted caller context. |
| `flows` | Records by flow instance ID, with local `steps`, optional projected `result`, and `attempt_count`. |
| `start` | The first flow of a workflow run and the `start` form that selected it; omitted for `run_flow` and `run_step`. |
| `transitions` | Routes or terminal outcomes selected at flow boundaries, with the `route` form that selected each. |
| `execution` | Run ID, workflow, revision, status, measured usage (by model, with an optional cost estimate), safe error on failure, and `trace` IDs when telemetry is enabled. |

A completed step result is at `result.flows[flow_id].steps[step_id].result`.
Its `result` can be JSON `null`; an omitted `result` means something else. Flow
and step records can be `completed`, `needs_review`, `failed`, `cancelled`, or
`skipped`. A step whose [`when`](../configuration/flows.md#skip-a-step-with-when)
condition was false, or that did not run because the flow stopped earlier, is
`skipped` and has no `result`. The root execution cannot be `skipped`.

## See which route was taken

Each transition names the configuration form that selected its target:

```json
{
  "source": "lookup_fund",
  "reason": "completed",
  "flow": "enrich",
  "route": {"kind": "route", "index": 0}
}
```

`route.kind` is `direct`, `cases`, `route` or `review`. `index` is the selected
`route` entry; `case` is the matched `cases` key, or for `review` the issue that
selected an issue-specific target. Both are omitted when they do not apply, for
example when `cases` fell to `default`.

The first flow is recorded the same way in `start`:

```json
{"flow": "extract", "route": {"kind": "route", "index": 0}}
```

`start.route.kind` is `direct` for `start: <flow>` and `route` for a routed
[`start`](../configuration/workflows.md), with the selected entry's `index`. The
`route.selected` event on the workflow span carries the same values.

## Read repeated flows

`attempt_count` counts executions: `0` for a skipped flow, `1` normally. A flow
with [`repeat`](../configuration/repeat.md) and a retry flow additionally carry
`attempts`, one record per run; the top-level fields describe the last run, and
`attempts_usage` / `attempts_elapsed_seconds` sum every run. A repeated flow's
`repeat.stopped_by` is `until`, `exhausted`, `continue_when`, `review` or
`failure`. The root `execution.usage` counts every attempt and retry run once.

## Read usage by model and cost

`execution.usage` is the run total; it counts every step, flow attempt, retry
flow run and collection item once. Whenever a model was used, the usage object
also has `by_model`, keyed by the provider model ID the requests were sent to.
Flow and step usage carry the same split for their own work.

```json
{
  "model_requests": 1,
  "tool_calls": 2,
  "input_tokens": 1850,
  "output_tokens": 210,
  "cache_read_input_tokens": 1024,
  "cache_write_input_tokens": null,
  "reasoning_output_tokens": 64,
  "cost": 0.004377,
  "cost_complete": true,
  "currency": "USD",
  "reference_model": "gpt-5.6-terra",
  "by_model": {
    "qwen3-30b-a3b": {
      "requests": 1,
      "input_tokens": 1850,
      "cached_input_tokens": 1024,
      "output_tokens": 210,
      "reasoning_tokens": 64,
      "cost": 0.004377,
      "cost_complete": true,
      "currency": "USD",
      "reference_model": "gpt-5.6-terra"
    }
  }
}
```

Token counts keep the known/unknown rule: `null` means the provider did not
report the count, never zero. `cached_input_tokens` is part of `input_tokens`
and `reasoning_tokens` is part of `output_tokens`.

`cost`, `cost_complete` and `currency` appear only when the model profile
configures [`pricing`](../configuration/models.md#estimate-cost-with-pricing).
`cost` is an estimate rounded to six decimals, computed per request from the
configured prices, not a provider invoice. It is `null` with
`cost_complete: false` when a count the estimate needs was not reported, or
when a total mixes priced and unpriced requests. `reference_model` names the
model whose prices a profile borrows as a reference estimate.

## Correlate with traces

With telemetry enabled, `execution.trace` holds the `trace_id` and `span_id`
of the run span. Log them in the host to link your records with the runtime's
spans; see [observability](observability.md).

## Read a decision

A decision step with one configured `question` exposes one result object. A
step with `questions` exposes `{"results": [...]}` in question order. A single
choice can look like this:

```json
{
  "questionId": "request_kind",
  "type": "choice",
  "answerability": {"status": "answerable", "issues": []},
  "answer": {"optionId": "statement"},
  "reason": "The message asks for a statement.",
  "evidence_strength": "strong"
}
```

Your code should branch on `answerability.status`, stable `issues`, and the
typed `answer`. `reason` explains the assessment to a person; its wording is
not a route key. `evidence_strength` describes support for the *whole reported
assessment*, including answerability and any abstention. `strong` can therefore
describe a well-supported `not_answerable` result. `limited` is a weaker but
permissible interpretation; `null` means strength was not assessed. These are
not probabilities, confidence scores, or automatic action thresholds.

`answerable` has a substantive answer. `partially_answerable` is available for
supported subsets of `multiselect` and `request_units`. `not_answerable` and
`undetermined` have issue codes and no substantive answer, except that a
predicate uses `{"value": "unknown"}`. For a singular choice,
`selection.origin: fallback` may record an authored fallback category while
the native answer remains `null` and the step remains `needs_review`.

Foliqant validates the structure and correspondence of model output. It does
not prove the explanation or strength rating is correct. Use [reviewed evaluation
cases](../evaluation/ground-truth.md) for that question. The exact question and
answer shapes are in the [reference](../reference/inputs-and-results.md); see
[decision policy](../guides/decision-contracts.md) for criteria and fallback
design.
