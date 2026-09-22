# Set up golden workflow evaluation

Use this reference when a user asks for evaluation setup, datasets, result
inspection, replay, or workflow quality measurement. The
[public cookbook](../../../docs/guides/testing-and-evaluation.md) contains the
complete JSON format and a runnable model-free example.

## Contents

- [Author the dataset](#choose-evidence-and-author-the-dataset)
- [Run the right check](#run-the-right-check)
- [Interpret results](#deliver-and-interpret)
- [Compare and group observations](#compare-and-group-saved-observations)

## Choose evidence and author the dataset

1. Inspect the configured workflow and its public results. Identify the business
   outcomes the user wants checked, including ambiguous/abstained/review outcomes.
2. Use supplied gold as authority. Preserve stable case IDs, source meaning, and
   reviewed labels. Ask a focused question when an expected outcome or label
   vocabulary is missing; continue independent setup. Do not invent acceptance
   thresholds or rewrite gold to agree with current outputs.
3. Store strict JSON under an ignored private directory, normally
   `.foliqant/evaluation/gold.json`. Add `evaluation.dataset` to `foliqant.yaml`;
   the path resolves from the configuration directory. No dataset is opened or
   statted during normal startup/validate/doctor, and this metadata does not
   change the runtime configuration digest.
4. A dataset has version `1`, `name`, `revision`, and `suites`. Each suite has
   `name`, configured `workflow`, optional `step`, optional `metrics`, and
   `cases`. Each case has `id`, envelope `input`, and named `expectations` with
   `path`, authored `expected`, and optional `comparison: exact|set|source_span`.
5. Use RFC 6901 pointers over public results: `/payload/...`,
   `/execution/status`, `/decisions/<step>/result/...`. Workflow bindings use
   `/steps/...`; those are not evaluation result paths. Exact preserves types
   and ordering. Set comparison ignores only top-level array order/duplicates.

For extractive fields, `source_span` gold contains `input_path`, `required`, and
`allowed`. Independently annotate nonempty Unicode code-point ranges with
exclusive ends. Required must lie inside allowed; the candidate must be an exact
source occurrence containing required and contained in allowed. Do not derive
permitted spans from predictions or use this as a semantic paraphrase judge.

Keep cases inline for a small suite, or use a string reference such as
`"cases": "classify.json"` in the main JSON manifest. Both forms can coexist in
one dataset, with separate classification, extraction, and pipeline files when
useful. Referenced files contain arrays of the same case objects; targets and
metric catalogs remain in the manifest. Relative paths resolve from the main
dataset directory; absolute paths are accepted. There are no nested references.
Each dataset/case file is limited to 64 MiB.
Only explicit evaluate/check/replay reads these files, never normal startup,
validate, or doctor. Do not impose splitting when an inline dataset is clearer.

For an isolated `step` suite, input payload keys are that step's resolved
input/source/argument names. No upstream steps or downstream routes run. A
pipeline suite instead observes actual intermediate results under upstream
conditions. Both use normal runtime validation and execution limits.

Metrics are optional named entries with `path`, `kind: classification|multilabel`,
and explicit ordered `labels`. Match the path to authored case expectations.
Classification uses string labels and may explicitly declare JSON null as a
label; multilabel uses string-only labels and arrays. Gold must satisfy the
catalog. For `evidence_strength`, use `["limited", "strong", null]` and independent
gold for all outcomes. Declared null is observed in the confusion matrix;
otherwise actual null remains an abstention and null gold is invalid. Missing
predictions, invalid predictions and execution failures remain distinct and are
never converted into null labels. Accuracy uses all cases with matching
gold; confusion/per-label counts cover valid observed predictions. Read coverage
and support alongside quality counts. Metrics add no numeric threshold policy.
Multilabel accuracy compares sets independently of assertion comparison: an exact
array assertion may fail on order while the metric counts the same set as correct.
Use an explicit set assertion when order is irrelevant to the user's contract.

Per-label precision/recall/F1 and micro/macro summaries use valid observed
predictions. Undefined denominators yield null. Macro uses the whole declared
catalog; do not drop unsupported labels to produce a better number. Read these
scores alongside all-gold accuracy, coverage, and unavailable counts. Prefer
small explicit checks for actions, review outcomes, evidence strength, and preserved
metadata over one aggregate label score. Exact spans validate extractive outputs;
they cannot establish that a free paraphrase is faithful.

## Run the right check

- `foliqant evaluate --check`: validate strict JSON, configured targets, structural
  pointers and gold label catalogs offline. It cannot prove dynamic output fields
  exist or that business gold is correct.
- `foliqant evaluate --replay REPORT`: score full saved results without SDK/client
  initialization or model/tool calls. This does not execute changed prompts.
- `foliqant evaluate`: execute the configured application. Model-backed workflows
  call the selected endpoint; the CLI does not substitute fake responses. Respect
  the user's existing authorization and avoid competing with active local runs.

Defaults are sequential suites, `--max-concurrency 1`, and `--timeout 300` per
case. Runtime/model/tool limits continue to apply. Do not introduce hidden
retries, endpoint discovery, judge calls, or automatic prompt optimization.

Use explicit `--repeat N` (or Python `repeat=N`) for variability measurements.
It makes additional calls, preserving source IDs and one-based repetition
indices. `case_count` counts authored sources; `attempt_count` counts executions.
Every source receives the same repeat count and failures remain in denominators.
Keep related examples and translations together when selecting a holdout.
Custom `RegisteredScorer` functions remain an explicit Python API; configuration
cannot import code. No heavy evaluation framework is required.

## Deliver and interpret

Reports default to unique `.foliqant/evaluations/report-TIMESTAMP.json` files
under the config directory. `--output` selects a new destination; no overwrites.
Stdout is content-free. Artifacts contain full input, gold, and public execution
results, including public reason/evidence strength, but no private model reasoning.
Keep artifacts ignored and private; do not publish their business values. Report
writing and replay share a 256 MiB limit. Replay validates saved workflow/step
targets even for invocation failures and preserves input numeric types (`1` and
`1.0` differ); it permits revised gold without permitting changed execution.

Exit codes: `0` passing checks, `1` gold mismatch, `2` invalid input/configuration,
`3` missing dependencies, `4` runtime failure (even when expected by gold). Explain failed case IDs/paths using
the permitted local artifact. Separate run failures and intended reviews from
assertion disagreement. Unknown usage is not zero.

For examples, default to a model-free workflow or scripted `FunctionModel` and
label the evidence as wiring only. Include an independently authored expectation
and a changed-gold negative control. A small synthetic suite is not model quality
evidence. For quality comparisons, use the same development gold across explicit
variants and confirm the selected variant on a separate untouched holdout.

## Compare and group saved observations

Use `foliqant evaluate --compare CANDIDATE --baseline BASELINE` for offline
comparison without clients or configuration loading. Inputs, gold, target,
scorers, catalogs and attempt identities must match; configuration revisions may
differ. Preserve mixed improvements/regressions. An intended review is not an
operational failure. Comparison is descriptive, without inferred thresholds;
exit zero means a valid comparison was produced, not that no regressions exist.
Replay rescoring preserves original case timings and step usage but its suite
wall time is not execution latency. Each saved repeated attempt is rescored once.

`group_report(report, input_pointer="/metadata/language")` groups a detailed
Python report by an explicit scalar input value without rerunning work. Missing
and null differ; no inferred language or hidden scorer calls. This API does not
load saved artifact paths. Example evaluators persist full private reports by
default; their console summaries are not the complete result.

## Check fallback without inflating classification quality

Keep native-answer gold separate from effective `selection/category/id` and
`selection/origin` gold under `/decisions/<step>`. Assert issue codes and intended
unresolved routes. A fallback leaves the native answer unresolved and is never
counted as model correctness. Step summaries count model/fallback selections;
`fallback_rate` divides by all observed step records, including skipped/error
records. Include unclear requests, known out-of-catalog requests, contradictory
facts and multiple valid options; do not merge their diagnoses into `misc`.
