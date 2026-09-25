# Evaluation extensions: extraction fields, projections, resume, intervals

Status: implemented. Normative text: `../evaluation.md`; user documentation:
`docs/evaluation/task-types.md`, `running.md`, `results.md`; skill reference
`skills/foliqant/references/evaluation.md`.

## Motivation

A client-reporting triage application evaluates five LLM workflows (intent split,
report type + field extraction with a check-and-repair pass, incident / task
details, clarification replies) on a few hundred reviewed e-mails against a local
model that serves one request at a time (5-60 s per call). The existing evaluator
covered execution, exact/set/span checks, classification and multilabel metrics,
replay and comparison. Measuring that application with JSON gold only (so that
`foliqant evaluate --check` validates it) exposed generic gaps; each feature below
is useful to any extraction or multi-intent workflow and none is specific to the
application.

| Gap | Feature |
| --- | --- |
| "Not stated in the text" is a valid extraction result, but an omitted field was a `missing` check, not a null | `absent_as_null` on expectations (only inside executed owners) |
| Extraction quality needs per-field outcomes: correct value, correct null, hallucinated, missed, wrong | metric kind `fields` with per-field / pooled counts and rates |
| Names copied from free text differ in case and spacing; generated descriptions must mention facts | comparisons `text` and `contains` (case folding + whitespace collapsing only) |
| Reviewers accept several report types for one request | comparison `one_of`; classification metrics count an accepted alternative as its own label |
| Results are arrays of objects (planned intents, request units); the label set is one member of each item | `each` projection on expectations and metrics; `expectation` selector on metrics |
| A run of hours is lost on an interruption | `EvaluationCheckpoint` / `--checkpoint` journal and resume |
| Long runs give no feedback | `progress` callback / `--progress` (content-free) |
| Deltas of small datasets are noise-dominated; a comparison gave no uncertainty | paired percentile bootstrap intervals on every rate delta of `compare_reports` |
| Tag breakdowns (reply vs. new e-mail) are arrays in metadata | array values form overlapping member groups in `group_report` |
| Cost estimates were per attempt only | `cost` in usage summaries and comparisons |
| Flow suites built from workflow cases duplicated the boundary bindings by hand | `flow_input(prepared, workflow, flow, envelope, flow_results=...)` |

## Design decisions

* **No new normalization knobs.** `text` / `contains` apply exactly Unicode case
  folding and whitespace collapsing. Anything more (punctuation, legal suffixes,
  synonyms) is domain policy and belongs in a registered custom scorer.
* **Absence is not null by default.** The distinction between a missing path and
  a present null stays the default; `absent_as_null` opts in per assertion and never
  applies to skipped, failed or nonexistent owners, so a failed run cannot produce
  correct nulls.
* **`fields` reuses assertions.** A field's gold and comparison are the ordinary
  expectation at `path/<field>`, so check outcomes and metric outcomes agree and
  replay/comparison need no extra gold format. Custom and source-span field
  assertions are rejected (the metric needs a synchronous built-in comparison).
* **Resume reuses the replay rule.** A recorded result is rescored against current
  gold; identity mismatches fail instead of mixing configurations. Only completed or
  reviewed attempts are recorded, so operational failures are retried.
* **Intervals are descriptive.** Seeded, reproducible, over source cases (repeats
  pooled), reported next to the point deltas; no threshold or significance verdict
  is derived.
* **Fingerprint stability.** New expectation options enter the suite fingerprint only
  when used; existing suites keep their fingerprints and saved artifacts stay
  comparable.

## Implementation notes

* `comparisons.py` holds `canonical`, `normalized_text`, `project` and `matches`, shared
  by checks (`runner._check`) and metrics (`metrics.observe_metrics`).
* `checkpoint.py` (`EvaluationCheckpoint`, `EvaluationProgress`,
  `CheckpointMismatchError`), `intervals.py` (`ratio_interval`,
  `paired_difference_interval`), `summaries.CostSummary`.
* `CaseReport.resumed`, `EvaluationReport.resumed_attempts`, `MetricReport.each`,
  `.expectation`, `.per_field`, `.field_totals`, `.field_accuracy`,
  `.macro_field_accuracy`, `EvaluationGroupReport.member`.
* `compare_reports` adds `case_pass`, `*_interval` entries, `field_accuracy_delta` and
  `usage.cost`; its `interpretation` is
  `descriptive_paired_bootstrap_intervals_no_release_threshold`.
