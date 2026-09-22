# Understand evaluation results

Start with [evaluation setup](testing-and-evaluation.md) to define your ground
truth. This guide explains how to use saved observations to improve a workflow
without confusing a schema check, an assertion score, and business accuracy.

## Keep the evidence

An evaluation report contains the input, authored expectations, complete public
result, and each check's actual value and outcome. It retains public reasons
and evidence-strength assessments, not the provider's private reasoning. Suite
fingerprints and configuration revisions identify what was measured.

Reports are private files, not application persistence. Keep them in an ignored
directory and do not publish them as ordinary CI artifacts. Evaluation writes a
new report instead of replacing an earlier run. Normal application startup and
workflow execution do not read gold or write these reports.

## Read separate measurements

| Measurement | Question it answers |
| --- | --- |
| Execution failure rate | Could the workflow execute and return a valid result? |
| Check pass rate | How many declared expectations matched? |
| Case pass rate | How many cases satisfied every expectation? |
| Classification accuracy and coverage | How often was the label correct, and how often was a valid label observed? |
| Confusion matrix | Which expected labels were confused with which predictions? |
| Per-label precision, recall, and F1 | Which labels produce false positives or false negatives? |
| Review rate | How often did the configured process request review? |
| Latency and token usage | What did the measured execution require? |

An intended review can pass every assertion. An execution that returned valid
JSON can fail every business assertion. A high review rate alone is neither good
nor bad: compare it with the reviewed expected behavior.

Precision, recall, and F1 describe valid observed predictions. Read them with
coverage and the counts of abstained, invalid, missing, skipped, and failed
outputs. Undefined scores are `null`, not invented zeroes or perfect scores.
Do not interpret an unsupported label's score as evidence of quality.

A classification catalog can explicitly include `null`, for example
`["limited", "strong", null]` for `evidence_strength`. Then null is an observed
answer category in the confusion matrix, not an abstention. With string-only
labels, a null prediction retains its abstention meaning. Missing, skipped and
failed observations always stay separate. Assess reason correctness with an
explicit reviewed rubric; strength labels alone do not score the reason.

Per-label `precision` is `TP / (TP + FP)`, `recall` is `TP / (TP + FN)`, and
`f1` is `2TP / (2TP + FP + FN)`. Each is null when its denominator is zero.
The metric's `micro` rates pool counts across labels. `macro` averages the entire
declared catalog separately for each rate; that rate is null if any label's
value is undefined, rather than silently excluding an unmeasured category.

Recorded durations depend on the endpoint, input, model settings, and concurrency.
A percentile from a handful of cases is descriptive, not a capacity estimate.
Unknown token usage is not zero. Replayed wall time measures rescoring, not a
new model execution; retain the original report for performance comparisons.

The `latency` summary contains `count`, `unavailable`, `minimum`, `median`,
`p95`, and `maximum` in seconds. P95 uses the nearest rank in the sorted sample;
with fewer than 20 measurements it is the maximum. An even-sized sample's
median averages its middle two values. Usage fields each contain `observed`,
`unknown`, `known_total`, and `total`: a partial known total is not a complete
total, and a completely unobserved field has no numeric total.

## Measure model choices and fallback policy separately

For a single-choice step, score its model answer at
`/decisions/classify/result/answer/optionId`. Independently score the effective
category at `/decisions/classify/selection/category/id` and its origin at
`/decisions/classify/selection/origin`. Supply reviewed expected values for each
metric. Include the issue codes and intended route in gold so that a convenient
`misc` category does not conceal a wrong diagnosis.

Step summaries include `model_selected_cases`, `fallback_selected_cases`, and
`fallback_rate`. The rate divides fallback selections by all observed records
for that step, including skipped/error records; no records produces `null`.
Repeated attempts count separately. These describe policy usage, not correctness.
Raw-answer metrics retain null abstentions and missing predictions even when an
effective fallback category is present. The support-triage example demonstrates
separate confusion matrices for native queues and effective selections.

## Measure repeatability

Run the same authored cases three times explicitly:

```sh
foliqant evaluate --config foliqant.yaml --repeat 3
```

This performs real additional model/tool calls for a model/tool workflow; it
does not reuse a cached result. Calls remain sequential unless you select
`--max-concurrency`. Keep local model evaluation separate from data generation.
There is no hidden retry or automatic judge.

`case_count` and `source_case_count` count authored cases. `attempt_count`
counts executions. Each saved attempt keeps its original `id` and a one-based
`repetition`; attempts are ordered by case, then repetition. Every case gets
the same repeat count, so no source gains extra weight merely because it ran
successfully. Failures count too. `case_pass_rate` describes attempts whose
assertions all pass, not independent-case population accuracy.

The Python APIs accept the same option. For an existing suite and variant:

```python
report = await evaluate(suite, variant, repeat=3, include_details=True)
agreement_by_case = {
    source.id: [attempt.passed for attempt in report.cases if attempt.id == source.id]
    for source in suite.cases
}
```

Inspect both agreement and the returned decisions. Two attempts can both fail
an assertion for different reasons, or pass selected fields while differing in
an unscored reason. Preserve the full reports to investigate those cases.

## Inspect language or other input groups

For a detailed Python report, group existing observations by explicit metadata:

```python
from foliqant.evaluation import group_report

language_groups = group_report(report, input_pointer="/metadata/language")
for group in language_groups:
    print(group.value, group.source_case_count, group.checks.pass_rate)
```

This does not call the model or infer a language. The pointer must select a
JSON scalar. Missing and explicit null form different groups, and scalar types
remain distinct. Each group includes checks, classification metrics, latency,
and usage with the same denominators as the original report. Use
`include_details=True` when evaluating; grouping needs the original inputs.
This API accepts an in-memory `EvaluationReport`, not a saved report filename.
Keep group values private when metadata contains business information.

## Score verbatim extraction

Exact equality is too restrictive when the contract permits surrounding context.
For an extractive field, independently annotate the required text and the allowed
surrounding clause. For source text `Cancel my subscription today.`, this gold
requires `Cancel my subscription` and permits the remaining clause:

```json
{
  "path": "/payload/action",
  "comparison": "source_span",
  "expected": {
    "input_path": "/payload/message",
    "required": [0, 22],
    "allowed": [0, 29]
  }
}
```

Offsets are zero-based Unicode code points with an exclusive end, not bytes or
JavaScript UTF-16 offsets. The input pointer resolves against the original case
input. Required and allowed ranges must be nonempty, in bounds, and nested.
The actual value must be a nonempty, exact source substring whose occurrence
contains the required range and stays inside the allowed range. There is no
case folding, whitespace repair, paraphrase matching, or model judge. Repeated
text must match at a permitted location, not merely elsewhere in the document.

Author these ranges from the intended extraction contract before inspecting
predictions. This checks a verbatim action or evidence span; it cannot establish
whether a free-form reason is semantically correct.

## Compare saved experiments offline

Keep a baseline and candidate report measured against the same gold. Compare
them without credentials, configuration loading, or another model/tool call:

```sh
foliqant evaluate --compare .foliqant/evaluations/candidate.json \
  --baseline .foliqant/evaluations/baseline.json \
  --output .foliqant/evaluations/comparison.json
```

The comparison checks that source inputs, authored expectations, target
workflow/step, scorer revisions, metric catalogs, and attempt identities agree.
Configuration and variant revisions may differ: measuring those deliberate
changes is the purpose of the comparison. Incompatible reports are rejected,
not merged into a misleading score. Existing artifacts remain untouched.

The comparison command exits successfully when it produces a valid comparison,
even when regressions exist; it does not invent a CI acceptance threshold. Its
summary reports improved, regressed, mixed, and unchanged attempt counts.

Inspect individual improvements and regressions as well as aggregate deltas.
A case with both kinds of change is `mixed`; a fixed field must not conceal
another field's regression. A missing prediction becoming an observed but wrong
prediction improves coverage, not correctness. Unknown usage cannot support a
numeric cost reduction. Neither a higher score nor a faster run establishes
statistical significance on a small dataset.

Replay is different: it applies current gold to saved observations of the same
execution configuration. It can check a justified gold correction without new
inference; it cannot measure a changed prompt. Preserve the original report
alongside the rescored one and identify which gold revision each uses.

## Diagnose before changing a prompt

1. Check whether the gold agrees with the intended contract. Preserve distinctions
   such as a deadline versus a date, an invoice versus an account, or a known
   lack of facts versus an inability to assess answerability. A genuine gold
   correction needs a recorded reason and a new dataset revision; do not copy
   predictions into gold to improve the score.
2. Compare the isolated step with the full pipeline. Correct isolated output but
   incorrect pipeline output points to upstream data, bindings, metadata, or
   routing. Test the transport separately when it carries the same envelope.
3. Inspect the complete result, including requested actions, reasons, evidence
   strength, and skipped steps. Compare the reason and strength with the supplied
   facts; a valid response shape does not establish semantic correctness.
4. Test one deliberate change against unchanged development gold. Keep all
   regressions visible, including a faster result that makes more mistakes.
5. Confirm the selected change against a separate untouched holdout. Repeated
   tuning on a holdout turns it into development data.

Use exact comparison for contractual values and `set` for unordered labels.
For free-form summaries, define an explicit reviewed rubric or versioned scorer
in host Python code. Do not substitute keyword matching for semantic correctness
or silently call a judge. Extractive outputs can instead specify verbatim source
spans when preserving wording is the actual product requirement.

## Build useful coverage

Choose examples from the process you need to support:

- Clear requests in each category, and nearby requests that should not match it.
- Multiple simultaneous intents. A single-choice task may require review when
  there is no unique answer; a multilabel task can legitimately select several.
  Assert the appropriate workflow outcome as well as the labels.
- Missing facts, conflicting instructions, and no applicable category.
- Email threads whose newest explicit instruction changes an earlier request,
  including unresolved contradictions rather than assuming that recency always wins.
- Required languages, absent values, misleading identifiers, and source evidence.

Keep example families together when separating development and holdout data.
Translations, paraphrases, and repeated runs are related observations, not fresh
independent evidence. Synthetic examples exercise these boundaries but do not
establish a population accuracy or calibrated confidence score.

The package does not infer business thresholds, calibrate confidence, generate
new prompts, or select a winning model. Those decisions need representative
reviewed data and the cost of a wrong action in your process.
