# Generated decision-data quality review

Date: 2026-09-19. Disposition: **not ready for training or quality evaluation**.
This is an agent review of a fixed, partial-run snapshot, not independent human
adjudication, an accuracy estimate, or a review of every subsequently generated
record. The generation process, implementation, prompts, configuration and run
artifacts were left untouched. No inference or external API calls were made.

This disposition remains final for the named old artifact. Its findings were
repaired and re-verified under new recipe identities; see
[`generated-data-quality-repair-review.md`](generated-data-quality-repair-review.md)
and [`generated-data-quality-repair-pilot.md`](generated-data-quality-repair-pilot.md).
The old rows were not relabelled or promoted.

## Evidence and scope

Run: `native-financial-decisions-en-v1-5736dad23445`.
Private evidence is outside Git under
`~/.local/share/foliqant/checks/quality-review/`: `snapshot.json`,
`review-bundle.json`, `build_review.py`, and `seed-overlap.json`.
The bundle records original inputs, oracles, cached attempts, accepted records,
validation results and semantic-signature differences. This report intentionally
contains no full dataset records.

- Snapshot: 100 completed candidates, 66 accepted and 34 quarantined, across 34
  scenario/source cells. Accepted examples cover only 25 cells.
- Reconstructed all 146 completed attempts from existing caches; none missing.
- All 66 accepted records pass the current structural/citation validator and
  oracle-signature comparison. They retain teacher origin, unreviewed status
  and training-only family assignment.
- Inspected accepted inputs, answers and explanation summaries, with detailed
  original/rewrite comparisons across scenario representatives. Inspected both
  attempts and differences for quarantined records; numeric rejections were
  checked against original source text.
- Inspected all 4,200 native seed inputs for exact input overlap after removing
  only generated `case-context` metadata and its allowed-source references.

Passing the current validator proves format and reference agreement, not that
references are correct or explanations follow logically from cited evidence.
The generator deliberately excludes explanation prose from semantic matching.

## Blocking findings

### 1. Reference answers sometimes reject correct answers

`mixed-sufficiency`: all three sampled candidates correctly answer false when
asked whether a reference is present and the source explicitly says it is
missing. The oracle expects unknown. See jobs `656711c3b7`, `9050fec359`,
`ce7e9c4488`.

`conditional-true` / `conditional-unresolved`: the question asks for an explicit
short target in `subject`, but oracles use null despite an explicit charge
amount. Three final attempts differ only in this subject
(`ab646b14d6`, `affcde1e9e`, `53f9f42189`). Other candidates in these same cells
also contain genuine relation errors; do not accept the entire category blindly.

`choice-ambiguous`: a missing referent is labeled multiple_valid_options, while
the model reasonably returns missing_information. These meanings should be
distinguished by explicit examples and consistent reference labels.

Implementation evidence: `model/src/foliqant_model/curation/decision_seeds.py`,
especially mixed-sufficiency and choice-ambiguous branches and request-unit
subject construction.

### 2. The number-preservation check mistakes formatting for changed facts

All eight final `rewrite-numbers-changed` outcomes changed the synthetic
metadata date from ISO notation to an equivalent written-month date; the dates,
account suffixes and business numbers retain their meanings. This is a false
rewrite rejection, not proof that the downstream answer would have been valid.
One of these jobs had a separate issue-enum mismatch in its earlier attempt.
Across all 146 attempts, 24 ended at the numeric check; the count of eight above
refers only to final quarantine reasons.

`decision_generation.py:188` compares counters of numeric tokens. Changing
`2026-04-28` to `April 28, 2026` removes the token `04` and triggers rejection.
Keep immutable metadata out of rewriting or validate equivalent typed values;
do not remove numeric preservation checks indiscriminately.

### 3. Vague tasks and incomplete labeling rules create inconsistent supervision

- Whole-answer adequacy asks whether an answer is “contract-consistent” without
  supplying an original task/output contract. Some responses interpret this as
  a legal contract, others as task requirements. “Correct unknown” cases also
  omit a separate refund intent while the question broadly asks for completeness.
- Ordinal priority uses vague “routine stated need” criteria, making the expected
  unknown for a request to review a case contestable.
- Request dependency examples conflate explicit ordering with a necessary
  prerequisite. Cancellation-as-status versus cancellation-as-separate-request
  is not sufficiently specified. Metadata can supply an explicit account
  subject although the oracle expects null.
- Exact issue-list matching rejects an additional, arguably justified
  missing_information issue alongside conflicting_information (`ca65a07be6`).
  Specify whether to enumerate all supported issues or a primary issue; preserve
  the concise existing enum rather than adding more codes.
- Both sampled rejected BANKING77 projections need adjudication: category names
  alone do not resolve overlapping intents. In `df958c7cfc`, the source asks
  about an extra withdrawal charge, while the reference names a wrong exchange
  rate. Preserve source provenance but do not treat inherited labels as
  unquestionable truth for a newly defined task.

### 4. Accepted explanations can add unsupported requirements

`141a7c79e7` concludes contract consistency from the absence of a contract.
`a66abcb49f` adds a requirement to mention the account suffix and provide actual
statement/fulfillment content although that requirement was not specified.
The latter's false answer is supported by an omitted request, but its additional
reasoning is unsuitable as clean teaching data. Fix the task definition and
review explanation entailment, not just labels and exact citation occurrence.

Most inspected simple classifications, explicit true/false/unknown predicates,
request extraction and defined-ratio arithmetic were sensible. That does not
establish a numerical correctness rate for this non-random, repetitive sample.

### 5. Apparent dataset size overstates diversity; splits share task content

The 4,200 parents contain 3,200 authored cases and 1,000 source projections
(500 BANKING77, 500 WANLI). Removing only synthetic case metadata leaves 1,230
unique full inputs: 30 groups of 100 identical inputs plus 1,200 singletons.
All 30 repeated groups are authored scenarios, not source projections.

Every one of those 30 groups spans train and held-out splits. Affected records:
2,394 train, 323 validation, 282 calibration and one test. The test overlap is
an authored prompt-injection case; this does not imply all external test rows
are contaminated. Across all native validation/calibration rows, 605 of 806
share an input with training after metadata removal.

`decision_seeds.py:1065` intentionally adds non-answer-bearing case facts;
`build_authored_seeds` includes the repetition index in family identity.
Grouping by that index protects selected counterfactual siblings, but does not
keep the same underlying task template out of other splits. The remaining 200
authored singleton inputs vary charge amounts; singleton status does not prove
independent semantic diversity.

This is content overlap that risks optimistic evaluation, not observed model
memorization. Group equivalent cases and paraphrases before splitting, and add
separate, genuinely novel evaluation cases. Broad benchmark categories may
appear across splits; near-duplicate business states should not serve as proof
of generalization.

## Genuine model errors also occurred

Conditional requests sometimes acquire unsupported or mutually contradictory
`requires` relations, including cycles. Withdrawal handling sometimes reverses
request ordering. One request subject lacks the required quote support. A WANLI
response correctly returns unknown but incorrectly marks it answerable.
These should remain rejected. The solution is not simply to accept every model
answer that differs from the reference, nor to relax all comparisons.

Final quarantine reasons:

| Reason | Count |
| --- | ---: |
| Solver/reference semantic mismatch | 24 |
| Numeric rewrite guard | 8 |
| Request subject absent from its evidence | 1 |
| Unknown predicate marked answerable | 1 |

These are the last failed attempts, not a mutually exclusive root-cause taxonomy
for whole candidates; an earlier attempt can have a different failure.

## Recommended next work

1. Pause bulk generation normally with Ctrl+C; this review did not stop it.
   Retain current caches and outputs unchanged as diagnostic evidence.
2. Correct and adjudicate references; explicitly define adequacy, priority,
   request subjects/relations and issue-list policy. Add focused regressions
   based on the observed failures.
3. Eliminate meaningless metadata rewrites and repair split grouping. Add real
   variation in business facts, outcomes and document structure before scaling.
4. Version changed seed/prompt/validator recipes. Do not silently reuse prior
   accepted labels under new rules or overwrite the existing run.
5. Run a small balanced pilot covering every scenario and independently inspect
   answers and explanations before another bulk run. Keep unresolved cases out
   of training and held-out evaluation until adjudicated.

A larger hosted model or batch API could improve some generation errors, but it
cannot fix wrong reference labels, underspecified tasks or contaminated splits.
Do not pay to scale this recipe until those issues are addressed.
