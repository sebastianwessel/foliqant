# Generated decision-data quality repair plan

Final disposition (2026-09-20): **QD-01 through QD-05 accepted for the bounded
generated-data pipeline repair**. Run
`native-quality-repair-ratio-final-v1-5cf4d705241f` completed all 35 jobs with
22 accepted and 13 quarantined. All accepted outcomes passed independent manual
review; the published artifact, membership, lineage, split isolation, 52-file
runtime fingerprint, public verification and immutable resume passed. The final
artifact ID is
`2961b70744fe1c0bd0be4b611e9c6f4190ff5ce501a353e17efbd69c16930018`.
This accepts the sampled pipeline repair and artifact, not full-recipe coverage,
population accuracy, model quality, training readiness, native MLX training or
production use.
Canonical behavior is in
[`specs/09-native-decision-data.md`](../specs/09-native-decision-data.md); the
observed evidence is
[`plans/reviews/generated-data-quality-2026-09-19.md`](reviews/generated-data-quality-2026-09-19.md).
The implementation review is
[`plans/reviews/generated-data-quality-repair-review.md`](reviews/generated-data-quality-repair-review.md).
Existing run artifacts and caches remain immutable outside Git. Earlier runs
remain diagnostic and are not relabelled by this acceptance.

## QD-01 — Repair authored task and oracle semantics

**Review status:** accepted on 2026-09-19. All 132 authored cases pass the typed
semantic validator and independent invariant review, including null-category
issues, declarative conditional status and exact quoted subjects.

**Requires:** presence-predicate, issue, adequacy, ordinal and request-unit
contracts in spec 09. **Evidence:** audit findings 1, 3 and 4.

Change the authored recipe to use `examplesPerScenario` with no legacy alias.
Correct explicit-absence predicates, ambiguous-referent issues, exhaustive issue
lists, objective ordinal rubrics, whole-answer task contracts, exact request
subjects, withdrawal identity and relation semantics. Remove answer-neutral case
metadata. Give every example canonical task/oracle identity and every template a
seed-independent content-family key. Supply exactly four genuine initial cases
per scenario, treat `examplesPerScenario` as a cap over that finite catalog, and
accept English authored generation only until German task and
oracle localization has separate evidence. Conditional extraction retains
conditional unit status after predicate evaluation, and every returned null
category reports `no_matching_option`.

Acceptance: focused contract/oracle regressions cover each corrected rule;
changing run seed or recipe version does not change the underlying content-family
key; distinct canonical task/oracle pairs do not collide; old
`familiesPerScenario` configuration fails closed and changed recipes produce a
new run identity; a cap of 100 still selects the four available genuine cases
and reports requested 100, available 4 and actual 4 without filler rows.

## QD-02 — Enforce semantic rewrite and canonical target publication

**Review status:** accepted on 2026-09-19. Required per-seed
`rewriteSourceIds` now freezes adequacy task contracts and proposed answers and
is bound through generation, recipe/cache identity, canonical replay and
publication validation.

**Requires:** QD-01 and explanation/value-preservation contracts in spec 09.
**Evidence:** audit findings 2 and 4.

Keep metadata byte-identical, normalize supported ISO and written dates before
comparing remaining numeric facts, and continue rejecting changed values,
currency, units, identifiers, negation and comparisons. Treat only the English
rate phrases `per month` and `monthly` as the same `month-rate` unit while
keeping bare `month`/`months` as duration tokens. Strengthen request graph
validation and symmetric relation canonicalization. Add required unique
`rewriteSourceIds`: annotate seeds expose none, ordinary authored rewrite seeds
expose every non-metadata source, and adequacy seeds expose only
`original-state` while freezing `task-contract` and `proposed-answer`. After blind semantic
agreement, discard solver-written rationale: for rewrite mode build descriptions,
explanations, missing facts and citations from the corrected reference and remap
them to rewritten sources within the recipe-bound 4,096-character whole-source
citation limit; fail closed when an evidence role or exact subject
cannot be grounded. For annotate mode, return the exact verified parent with no
generated duplicate or `GenerationProvenance`.

Acceptance: equivalent date formatting and `per month`/`monthly` rate phrasing
pass; duration/rate swaps and changed facts fail; unsupported relations and
cycles fail; exact subjects remain signature-bearing; injected solver
requirements never enter a published record; remapping failure quarantines the
candidate.

## QD-03 — Isolate content families and prove meaningful diversity

**Review status:** accepted on 2026-09-19. The deterministic answer-bearing task
projection preserves questions and every source in the union of
`allowedSourceIds`, including allowed metadata, and ignores only sources
unavailable to every question. Duplicate projections are excluded before the
source cap and conflicting targets fail closed without an NLP-equivalence claim.

**Requires:** QD-01. **Evidence:** audit finding 5 and private
`seed-overlap.json` named by the audit.

Connect every counterfactual, value variant, paraphrase, translation and
generated derivative for one semantic template variant before split assignment.
Use genuinely different state/evidence structures for new template variants;
reject exact duplicate tasks and filler-only variation. Compute required coverage
from eligible train cells after the split. Report example, unique-task,
template-variant and connected-family counts separately.

Acceptance: no connected content family crosses train/validation/calibration/test;
the formerly repeated authored inputs cannot span splits; held-out templates are
absent from training; held-out-only cells create no false coverage shortage; a
fixture differing only through a source absent from every `allowedSourceIds`
set is excluded as one duplicate rather than independent diversity; allowed
metadata remains identity-bearing; and an equal answer-bearing task with a
different semantic target fails closed.

## QD-04 — Publish only accepted training lineage

**Review status:** accepted on 2026-09-19 from independent code and
publication-matrix test review, then confirmed by the final QD-05 artifact.

**Requires:** QD-01 through QD-03. **Evidence:** the previous runner published
all train parents in `native-decisions`, including unaccepted inputs.

Keep every prepared seed in diagnostic `native-seeds`. Build
`native-decisions` from all unchanged held-out native diagnostics, accepted rewrite
derivatives and only their validated lineage parents, plus exact projected
parents whose blind annotate verification was accepted. Exclude unattempted and
quarantined train parents. Do not publish `native-decisions` when zero jobs are
accepted. Separate accepted projection verifications, generated derivatives,
lineage parents, held-out rows and total published rows in coverage; retain the
existing `CurateResult.generatedAccepted` field as accepted-job count.

Acceptance: publication matrix tests cover authored/projected ×
accepted/quarantined/unattempted × train/held-out; lineage validation still
passes; projected rows remain `reviewed=false`; zero acceptance leaves caches,
outcomes, `native-seeds` and coverage intact but returns `OUTPUT_INVALID` without
a `native-decisions` artifact.

## QD-05 — Version, verify and re-audit a bounded pilot

**Review status:** accepted on 2026-09-20. The first two repair pilots remain
diagnostic evidence of the defects they exposed. After correcting the four
ratio summaries, a 132-case delta proof showed that only those explanation
fields and derived parent/recipe identities changed; every task, semantic
signature, family, group and mutability rule stayed fixed. The final balanced
run completed 35 jobs with 21 accepted derivatives, one accepted projection and
13 quarantines. Independent review found no defect in the 22 accepted outcomes.
Of the quarantines, five were conservative lexical false rejections, seven were
model output or contract errors and one was an ambiguous projected source.

**Requires:** QD-01 through QD-04. **Evidence:** the prior 100-candidate snapshot
is the regression baseline, not an accuracy baseline.

Regenerate reviewed schemas and recipes, update examples/guides/status text, and
run the offline tests, strict typing, lint, schema drift, docs/tracked-data and
Git diff checks required by `AGENTS.md`. Then run a new small balanced local
pilot without reusing old semantic outcomes. Independently inspect every
accepted answer and canonicalized explanation across available scenario cells;
record counts and failures in a new review without claiming a population
accuracy rate.

The final artifact contains 416 records: 373 unchanged held-out rows and 43
training rows. Its split counts are 94 calibration, 179 test, 100 validation and
43 train, with no cross-family or unexpected-parent membership. Public verify
passed for all three datasets. An unchanged resume preserved all 35 outcomes,
63 cache files and 134 total cache/data files. The final 52 source hashes match
the reviewed runtime fingerprint.

Acceptance: all repository checks pass; the pilot contains no training parent
without an accepted job, no cross-split content family, no unchecked solver
rationale, and no stale recipe/cache reuse. Any unresolved label or explanation
stays quarantined. These requirements pass for the final bounded artifact.
Separate source-rights review, full-recipe evidence and an explicit training
decision remain required before any native MLX training.
