# Understand evaluation results

Start with [testing and evaluation](testing-and-evaluation.md) to author reviewed
gold. A report records suite and configuration identities, target scope, every
attempt, every declared check, complete public results when details are enabled,
and aggregate latency and usage.

## Read agreement and execution separately

| Measure | Meaning |
| --- | --- |
| Check pass rate | Assertions equal authored gold |
| Check coverage | Assertions with an observable pass/fail value |
| Case pass rate | Attempts whose assertions all passed |
| Failure rate | Invocations that failed operationally |
| Review rate | Invocations ending in `needs_review` |
| Latency | Measured invocation time, with count and percentiles |
| Usage | Known model/tool request and token totals |

A review can match intended gold. A completed run can disagree with gold.
Unknown usage is not zero. Read these measures together before changing a prompt
or route.

`source_case_count` counts authored cases. `cases` and `attempt_count` count
attempts after `repeat`. Every failed or unavailable observation remains in its
denominator.

## Follow flow and operation identity

`target_workflow`, `target_flow`, and `target_step` identify the requested
scope. A step target is unambiguous only together with its flow.

Each case contains flow observations and operation observations. Aggregate
`flows` summaries retain status counts, latency, and usage for each flow.
`steps` summaries use the pair `flow` plus `name`, so repeated step names in
different flows remain distinct.

Nested callable-flow observations also contain `invocation_path`. This path
identifies the collection item that invoked a flow or step, so repeated calls to
the same definition remain separate while grouped summaries include them all.
For a collection step, completed and review ledgers are under `result.items`;
failed ledgers are under `partial_result.items`.

Flow and step summary fields count runtime observations:
`observed_invocations`, `skipped_invocations`, `failed_invocations`, and
`review_invocations`. Decision-step summaries additionally count
`model_selected_invocations` and `fallback_selected_invocations`. Repeated child
calls increase these counters; they do not increase `source_case_count`.

Workflow usage is already the run total. Flow and operation usage summaries are
diagnostic views of the same execution and must not be added to the workflow
total.

## Diagnose unavailable checks

- `failed`: the observed value exists and disagrees with gold.
- `missing`: the expected public path is absent.
- `skipped`: the relevant flow or operation did not execute.
- `error`: execution or result validation prevented a valid observation.

Inspect the case's safe reason code, workflow status, transition records, then
the scoped flow and operation statuses. Do not discard an unavailable assertion
to improve a rate.

## Interpret classification metrics

A classification metric records a confusion matrix over its declared ordered
catalog. Precision asks how often predictions of a label were correct; recall
asks how much gold for that label was found. Macro F1 weights labels equally;
micro F1 weights individual observations.

Do not merge `null`, unresolved answers, skipped operations, or errors into a
catch-all label unless that is the reviewed business contract. The metric report
tracks missing and invalid observations separately.

For a decision fallback, score native answerability and effective selection as
different claims:

```text
/flows/triage/steps/classify/result/answer/optionId
/flows/triage/steps/classify/selection/category/id
/flows/triage/steps/classify/selection/origin
```

A fallback keeps the native answer unresolved and therefore is not model
classification correctness. Step summaries report model and fallback selection
counts separately.

## Interpret multilabel metrics

Exact-set accuracy requires the entire predicted set to match. Micro scores count
label decisions across cases; macro scores average per-label behavior. Read
per-label support and missing counts before trusting a high aggregate on an
imbalanced dataset.

## Inspect latency and usage

Latency summaries report count, minimum, median, p95, maximum, and mean for
observed calls. Suite `elapsed_seconds` includes scheduling and scoring, so it
is not provider latency. Replay preserves saved invocation timing; its own wall
time is not a new execution measurement.

Usage summaries track known and unknown observations. Cache-read and cache-write
input token fields describe provider-reported accounting when available. They do
not by themselves prove lower latency or cost, and evaluation does not reorder
requests to manufacture cache hits.

## Compare runs

Offline comparison verifies that candidate and baseline describe the same cases,
gold, scope, scorer revisions, metrics, and repetitions. It then reports changed
checks and aggregate deltas. A valid comparison may contain regressions; CLI
success means the comparison was produced, not that a release threshold passed.

Changed prompts, model endpoints, or workflows require new executions. Replay
can only rescore the saved outputs already present.

## Diagnose before tuning

1. Separate operational failures from valid reviews and gold disagreement.
2. Compare the full pipeline with its flow and operation suites.
3. Inspect bindings, projections, and transitions when isolated output is right
   but pipeline output is wrong.
4. Inspect direct input, authored instructions, schemas, and provider behavior
   when the isolated operation is wrong.
5. Change one controlled variable on development cases.
6. Confirm the selected variant on a separate untouched holdout.

Do not tune against the final test set or treat a synthetic example as population
evidence. Keep configuration revisions, suite revisions, and content
fingerprints with every reported result.
