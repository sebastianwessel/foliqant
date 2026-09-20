# Generated-data quality repair review

Review date: 2026-09-20. Decision: **QD-01 through QD-05 accepted for the
bounded generated-data pipeline repair**. Independent review accepts the
authored contracts, bounded rewrite and canonical-target path, deterministic
duplicate/filler guard, publication matrix and final sampled artifact. This is
not human adjudication of projected labels, full-recipe coverage, model-quality
qualification, training readiness, native MLX training or production approval.

## Resolved findings

**QD-03 — deterministic duplicate and unavailable-source handling.**
`decision_runner._seeds_and_families` now compares an exact task identity and a
bounded answer-bearing identity that preserves the full question contract and
every source referenced by any `allowedSourceIds`. Sources unavailable to all
questions are the only omitted content. Authored repeats fail; equal projected
tasks are excluded before the per-source cap with separate exact and
unreachable-source-only counts; conflicting targets fail closed. Allowed
metadata remains identity-bearing, raw rows stay immutable in `source-corpus`,
and the implementation makes no claim to recognize semantic equivalence between
allowed prose.

**QD-01/QD-02 — pilot-discovered conditional and mutability gaps.** Every
returned null request category now requires `no_matching_option`; conditionally
stated units remain `status=conditional` after predicate evaluation; exact
subjects use quoted source anchors. Each seed carries validated
`rewriteSourceIds`: annotate seeds expose none, ordinary authored seeds expose
all non-metadata sources, and adequacy seeds expose only `original-state`,
keeping `task-contract` and `proposed-answer` byte-exact. The selection is bound
to recipe and request identity and revalidated during canonical replay and
publication.

## Authored catalog evidence

The final English catalog contains 33 scenarios with four code-authored cases
per scenario (132 task/oracle pairs). I manually traced all 24 research cases,
all 16 whole-answer adequacy cases, and all 92 remaining core cases from source
facts and question criteria through answerability, typed answers, issues,
request units and relations, subjects, citations, and explanations.

- Research cases correctly distinguish explicit absence from unknown, preserve
  the known/missing ratio counterfactual pairs, use correct ratio arithmetic,
  require the stated measurement period, and leave missing applicability or
  irrelevant-evidence questions unknown.
- Adequacy cases preserve every known field in the correct-unknown response and
  reject omitted requests or unsupported evidence across all four task domains.
- Core cases distinguish missing referents, no matching options, genuinely
  multiple supported options, and criteria-resolved priority. Predicate,
  ordinal, request-status, conditional, dependency, and prompt-injection
  outputs agree with their sources and caller-defined criteria.
- Exact request subjects are literal source spans. Withdrawal applies only to
  the identified unit; order alone creates `precedes`; explicit prerequisites
  create `requires`; conditional alternatives are mutually exclusive and cover
  true, false, and unresolved predicate outcomes.
- Counterfactuals share group plus template variant for predicate, ordinal,
  conditional, request-status, ratio, and adequacy clusters. The four
  `choice-ambiguous` cases now use different missing-evidence structures, and
  the four `requests-same` cases use different request categories and target
  anchors rather than paraphrase or value-only repetition.

The review found copied canonical prose in ambiguous-choice, no-match, ordinal,
partial-request, and conditional variants, plus prose-only/value-only diversity
in `choice-ambiguous` and `requests-same`. Those findings were repaired before
this decision. A second reviewer independently traced the 92 core cases and
reported the same clean result after repair. Re-review found no remaining
blocking authored-oracle defect.

Verification run from the repository root:

```text
uv run --project model pytest -q \
  model/tests/test_decision_seeds.py \
  model/tests/test_decision_research_cases.py \
  model/tests/test_decision_adequacy_cases.py
22 passed in 0.48s

uv run --project model ruff check \
  model/src/foliqant_model/curation/decision_seeds.py \
  model/src/foliqant_model/curation/decision_research_cases.py \
  model/src/foliqant_model/curation/decision_adequacy_cases.py \
  model/tests/test_decision_seeds.py \
  model/tests/test_decision_research_cases.py \
  model/tests/test_decision_adequacy_cases.py
All checks passed!
```

## QD-02 rewrite and canonical-target evidence

The generator freezes the complete question contract, source IDs and kinds,
metadata bytes, normalized supported dates, currency/amount associations,
remaining numbers, identifiers, units, negations, comparisons, and quoted
anchors. Request graph validation canonicalizes symmetric alternatives and
rejects duplicate relations, combined directed cycles, contradictory conditions,
and direct or transitive prerequisite paths between mutually exclusive units.
Exact request subjects remain part of the semantic signature and must occur in
their own evidence.

Blind solver acceptance compares the full frozen semantic signature. Rewrite
publication then discards solver prose and deep-copies the corrected oracle,
remapping every explanation and request-unit citation to the rewritten source.
It keeps an exact retained quote or uses the complete rewritten source only
within the recipe-bound 4,096-character limit, and otherwise fails closed.
Annotate acceptance returns the exact parent with no generation provenance.
Cached records are revalidated against canonical reference prose and the typed
rewrite guards before publication. The narrower per-seed source-mutability
contract is implemented and independently reviewed, so QD-02 is accepted. The
checker still does not claim free-prose entailment, matching the specification's
stated limit.

The stopped QD-05 run exposed a guard false rejection when `EUR 20 per month`
became `a monthly charge of EUR 20`. The repaired bounded alias canonicalizes
only `per month` and `monthly` as `month-rate`; bare `month`/`months` remain
duration tokens, so duration/rate swaps and daily or annual substitutions still
fail. This is lexical unit preservation, not a general source-equivalence claim.

A later catalog audit corrected all four answerable ratio summaries so each
names the quotient rather than its numerator. The 132-case delta proof preserves
every task, semantic signature, family, group and mutability rule; only those
four summary fields and derived parent/recipe identities change. The final
published artifact contains the corrected current-ratio held-out seed. The
other three corrected ratio cases were reviewed in the authored catalog but are
not claimed as final-artifact coverage.

## QD-03 family and coverage evidence

Family identity is independent of run seed and recipe wording while job/run
identity changes with the recipe. Counterfactual authored cases share group plus
template variant; generated records inherit the parent's family and group keys;
source projections retain their frozen source family. Preparation rejects
cross-family equivalent tasks, conflicting targets, and group keys that cross
families. Jobs and required coverage cells are built only from eligible train
families, so held-out-only cells do not create shortages. Diversity reports
authored examples by scenario, unique inputs, template variants, connected
families, and split-level unique-input counts separately. The bounded
answer-bearing projection and regressions described above close the prior
duplicate/filler gap, so QD-03 is accepted. A separate read-only analysis of
1,132 prepared seeds found no source unavailable to every question,
answer-bearing duplicate, cross-family equivalent or conflicting target.

## QD-04 publication evidence

`native-seeds` retains all prepared diagnostic inputs. Final publication starts
with every unchanged held-out native seed. An accepted rewrite adds its parent
for lineage and its derivative; an accepted annotation adds the exact parent
once. Quarantined and unattempted train parents are absent. Coverage separates
generated derivatives, verified projections, lineage parents, held-out rows,
and total published rows. With zero accepted jobs the run retains diagnostics
and coverage, raises `OUTPUT_INVALID`, and creates no `native-decisions`
artifact. Cached outcomes pass the same provenance, semantic, canonical-prose,
and typed-rewrite validation. With QD-01 through QD-03 accepted, QD-04 is also
accepted from code and publication-matrix review.

Additional focused verification run while the pilot runtime remained frozen:

```text
uv run --project model pytest -q \
  model/tests/test_decision_contracts.py \
  model/tests/test_decision_generation.py \
  model/tests/test_decision_runner.py
56 passed in 5.56s

uv run --project model ruff check \
  model/src/foliqant_model/curation/decision_contracts.py \
  model/src/foliqant_model/curation/decision_generation.py \
  model/src/foliqant_model/curation/decision_runner.py \
  model/tests/test_decision_contracts.py \
  model/tests/test_decision_generation.py \
  model/tests/test_decision_runner.py
All checks passed!
```

An independent combined run of the six decision contract, seed, generation,
adequacy, research and runner modules passed 84 tests. A subsequent focused
owner run passed 56 tests after adding direct regressions for duplicate,
unknown, metadata, empty-rewrite and annotate-nonempty source selections. The
all-seed invariant check rebuilt 132 authored seeds with no semantic validator
errors: 16 adequacy seeds rewrite only `original-state`; all other authored
seeds select every non-metadata source; and all 12 conditional outputs preserve
their conditional units, exact quoted subjects and required null-category issue.

## QD-05 final bounded evidence

Run `native-quality-repair-ratio-final-v1-5cf4d705241f` completed all 35 jobs:
21 authored derivatives and one source projection were accepted, and 13 jobs
were quarantined. Independent manual review inspected all 22 accepted inputs,
answers and canonical explanations and found no remaining defect. The 13
quarantines comprise five conservative lexical false rejections, seven model
output or contract errors and one ambiguous source projection. Conservative
rejection is a safe filter result, not proof that the model or paraphrase was
semantically wrong.

The final `native-decisions` artifact has ID
`2961b70744fe1c0bd0be4b611e9c6f4190ff5ce501a353e17efbd69c16930018`
and contains 416 rows: 373 unchanged held-out diagnostics and 43 train rows.
The split counts are 94 calibration, 179 test, 100 validation and 43 train.
Exact membership checks found no cross-family row or unexpected parent. Public
verification passed for the source corpus, native seeds and native decisions;
all 52 reviewed runtime hashes matched. The unchanged resume preserved all 35
outcomes, 63 cache files and 134 total cache/data files.

## Remaining limits

The accepted evidence covers one bounded English recipe and one configured
model/runtime. It is not a population accuracy estimate or evidence for German,
the full recipe, other model/runtime combinations, calibrated confidence,
financial or legal correctness, or production fitness. Source projections and
generated rows remain unreviewed research data. No native MLX training or
checkpoint-quality evaluation was performed or approved. Earlier run artifacts
remain diagnostic and are not promoted by this decision.
