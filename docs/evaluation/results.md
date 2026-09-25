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

A case whose run failed is an execution error: its checks have the outcome
`error`, it counts in the failure rate, and it is never a gold mismatch, even
when gold expected the failure. Inspect the case's safe `error_code` and
`error_reason`, workflow status, transitions, then its flow and step records
(each step record also has `error_code` and `error_reason`). Reports never
retain raw exception text.

## Break failures down by code

`failures_by_code` counts the failed, cancelled and error attempts behind the
failure rate by their safe error code, most frequent first, and
`failures_by_reason` counts those with a content-free
[failure reason](../integration/errors.md#failure-reasons):

```json
{
  "failure_rate": 0.72,
  "failures_by_code": {"output_limit_reached": 68, "invalid_output": 10, "request_timeout": 5},
  "failures_by_reason": {"reasoning_consumed_budget": 68, "schema_violation": 10}
}
```

`failures_by_code` also appears on every flow and step summary (counting failed
and cancelled records, with each record's `error_code`), on each
[group](#group-attempts), in the content-free summary that `foliqant evaluate`
prints, and as `baseline_failures_by_code` and `candidate_failures_by_code` in
a comparison's `execution` block. `failures_by_reason` appears on the report,
on every step summary and in the CLI summary. The code names the fix:
`output_limit_reached` means the model used its whole output budget, and its
reason says whether reasoning consumed it (`reasoning_consumed_budget`: raise
`max_tokens` or lower the reasoning effort) or the answer itself was too long
(`answer_exceeded_budget`)
([output budget](../steps/llm.md#set-the-output-budget-and-reasoning-effort));
`request_timeout` and `run_timeout` point at deadlines; the limit codes
(`step_limit_reached`, `model_request_limit_reached`, `iteration_limit_reached`,
`tool_call_limit_reached`) at execution limits; `invalid_output` at
instructions, schemas or the model's structured-output support. See
[error codes](../integration/errors.md#canonical-error-codes).

Step summaries also report `output_retries`, the model requests that asked for
a corrected invalid output, and `output_retry_recoveries`, the invocations that
succeeded after at least one such correction. Many recoveries mean the model
often needs a second answer: clarify instructions or simplify the schema before
relying on [output retries](../integration/errors.md#output-retries).

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

## Read field reports

A `fields` metric reports, per field and pooled over fields, the counts of
`correct_value`, `correct_null`, `hallucinated`, `missed`, `wrong_value` and
`unavailable`, with `value_support` (value gold) and `null_support` (null gold):

| Measure | Formula |
| --- | --- |
| Field accuracy | (correct values + correct nulls) / support |
| Hallucination rate | hallucinated / null support |
| Miss rate | missed / value support |
| `field_accuracy` | pooled field accuracy over every scored field |
| `macro_field_accuracy` | mean accuracy of the fields with support |
| `accuracy` | attempts with every gold field correct / supporting attempts |

A correct null is a valid result: the text did not state the value and the
workflow returned none. Unavailable fields (skipped or failed owners, failed
runs) remain in support and are never correct.

## Group attempts

`group_report(report, input_pointer=...)` summarizes a detailed report by an
authored input value, for example `/metadata/split` or `/metadata/language`.
Scalar values partition the attempts; a missing pointer and a present null are
separate groups. An array value such as `/metadata/tags` puts each attempt into
the group of every distinct element (`member: true`), so member groups overlap
and an empty array joins no group. Each group has the same check, metric,
latency and usage summaries as the report, plus its case pass and failure rates
and `failures_by_code`.

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
available for diagnosis. Unknown is never rewritten as zero. The `cost` summary
sums the configured cost estimates the same way: an attempt without a model
request costs zero, while an unpriced profile, an incomplete estimate or an
execution error leaves the attempt's cost unknown. Cache-token counts
are provider-reported accounting and do not by themselves prove lower cost or
latency.

Replay retains original case timing and usage. Replay wall time measures
rescoring and cannot support a provider-latency comparison.

## Compare and diagnose

Offline comparison reports per-attempt improvements, regressions, mixed changes,
and unchanged results, plus aggregate deltas. It verifies compatible inputs,
gold, targets, scorers, metrics, and attempts before comparing. A successful
comparison command means the comparison was produced, not that a candidate met
a release threshold.

Every rate delta (`case_pass.rate_delta`, `checks.pass_rate_delta`, each
metric's `accuracy_delta` and, for field metrics, `field_accuracy_delta`)
carries a 95% paired percentile bootstrap interval (`..._interval`: `low`,
`high`, `level`, `resamples`, `excludes_zero`). It resamples the authored source
cases with replacement (repeats stay with their source) for both reports at
once, with a fixed seed. An interval that includes zero means the authored cases
do not distinguish the variants; one that excludes zero still says nothing about
cases the dataset does not cover or about label errors. Usage deltas include
the cost summary.

Use this diagnosis order:

1. Separate operational errors from valid reviews and gold disagreement, and
   read `failures_by_code` and `failures_by_reason` before changing prompts.
2. Compare workflow results with matching flow and step suites.
3. If isolation passes but the workflow fails, inspect input bindings,
   projections, and routes.
4. If isolation fails, inspect direct input, instructions, schemas, and provider
   behavior.
5. Change one controlled variable on development cases.
6. Confirm the selected variant once on an untouched holdout.

Detailed reports contain complete inputs, expectations, and public results.
Keep them private, access-controlled, and out of ordinary CI artifacts.
