# Read evaluation reports

Read agreement with gold separately from execution health. A completed workflow
can disagree with gold, while an intended `needs_review` result can agree with
gold.

## Start with the denominators

| Measure | Meaning |
| --- | --- |
| Check pass rate | Passed assertions divided by every declared assertion |
| Check coverage | Assertions that produced an observable pass or fail value |
| Case pass rate | Attempts in which every assertion passed |
| Failure rate | Attempts that failed operationally |
| Review rate | Attempts ending in `needs_review` |
| `source_case_count` | Distinct authored cases |
| `attempt_count` | Executions after applying `repeat` |

Missing, skipped, and error checks remain in the total. Do not remove them to
improve a rate. Repeated attempts keep the original case ID and add a one-based
`repetition` value.

Each check outcome has a specific meaning:

| Outcome | Interpretation |
| --- | --- |
| `passed` | Observed value agrees with gold under the configured comparison |
| `failed` | Observed value exists but disagrees with gold |
| `missing` | The expected public path is absent |
| `skipped` | The flow or step owning the path did not execute |
| `error` | Execution, result validation, or custom scoring prevented comparison |

Inspect the case's safe `error_code`, workflow status, transitions, then its flow
and step records. Reports never retain raw exception text.

## Follow the requested and observed scope

`target_workflow`, `target_flow`, and `target_step` identify the requested suite
scope. A step target is always paired with its flow.

Flow and step summaries describe runtime observations, including nested callable
flows:

- `observed_invocations`, `skipped_invocations`, `failed_invocations`, and
  `review_invocations` count records.
- Step identity is the pair `(flow, name)`, so equal local step names in
  different flows do not collide.
- `invocation_path` identifies an exact nested collection item. Repeated calls
  to one callable flow remain separate observations.
- Decision summaries also report `model_selected_invocations`,
  `fallback_selected_invocations`, and `fallback_rate`.

Workflow, flow, and step usage are overlapping views of the same calls. Never
add them together. Root execution usage is the complete run total.

## Read classification reports

A classification confusion matrix uses expected labels as rows and predicted
labels as columns, in the declared catalog order. Per-label counts and rates use
only valid observed predictions:

| Measure | Formula or scope |
| --- | --- |
| Accuracy | Correct observations divided by all supported attempts |
| Coverage | Valid observed predictions divided by all supported attempts |
| Precision | `TP / (TP + FP)` when defined |
| Recall | `TP / (TP + FN)` when defined |
| F1 | `2TP / (2TP + FP + FN)` when defined |
| Micro | Pool TP, FP, and FN across labels first |
| Macro | Average each rate over the entire declared catalog; undefined if any label's rate is undefined |

`support` counts attempts with matching gold; `excluded` counts attempts without
gold at that metric path. Reports separately count `observed`, `abstained`,
`missing`, `skipped`, `errors`, and `invalid`. A null prediction is a valid label
only if the classification catalog explicitly declares `null`.

Read accuracy with coverage. One correct observed prediction and nine missing
predictions gives 10% accuracy and 10% coverage, not perfect classification.

## Read multilabel reports

Multilabel accuracy requires the entire predicted set to match. Per-label
TP/FP/FN/TN and micro/macro rates cover valid observed sets. Missing and error
attempts remain in support but do not become fabricated negative labels.

For imbalanced data, inspect per-label recall and source support before relying
on one aggregate. Macro treats every declared label equally; micro weights every
observed label decision.

## Keep native and fallback measurements separate

For a decision fallback, score at least these distinct claims:

```text
/flows/triage/steps/classify/result/answerability/status
/flows/triage/steps/classify/result/answer/optionId
/flows/triage/steps/classify/selection/category/id
/flows/triage/steps/classify/selection/origin
```

A fallback keeps the native answer unresolved. It may be correct application
policy without being a correct model classification. `fallback_rate` describes
policy use; it is not an accuracy metric.

## Inspect latency and usage

Latency summaries report observed count, unavailable count, minimum, median,
p95, and maximum seconds. P95 uses nearest rank. Suite `elapsed_seconds`
includes scheduling and scoring and is not provider latency.

Usage summaries preserve known and unknown observations separately. A complete
total is `null` when any contributing value is unknown; a known subtotal remains
available for diagnosis. Unknown is never rewritten as zero. Cache-token counts
are provider-reported accounting and do not by themselves prove lower cost or
latency.

Replay retains original case timing and usage. Replay wall time measures
rescoring and cannot support a provider-latency comparison.

## Compare and diagnose

Offline comparison reports per-attempt improvements, regressions, mixed changes,
and unchanged results, plus aggregate deltas. It verifies compatible inputs,
gold, targets, scorers, metrics, and attempts before comparing. A successful
comparison command means the comparison was produced, not that a candidate met
a release threshold or statistical test.

Use this diagnosis order:

1. Separate operational errors from valid reviews and gold disagreement.
2. Compare workflow results with matching flow and step suites.
3. If isolation passes but the workflow fails, inspect input bindings,
   projections, and routes.
4. If isolation fails, inspect direct input, instructions, schemas, and provider
   behavior.
5. Change one controlled variable on development cases.
6. Confirm the selected variant once on an untouched holdout.

Detailed reports contain complete inputs, expectations, and public results.
Keep them private, access-controlled, and out of ordinary CI artifacts.
