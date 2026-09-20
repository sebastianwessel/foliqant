# Offline source-projection extension

Status: **offline implementation complete; live execution deferred**.

Observed offline counts and verification evidence are recorded in
[the implementation review](reviews/source-projections.md).

This plan extends native decision-data projection for three already pinned
sources without changing ordinary generation. It records implementation scope
and the intended 32-task pilot. It does not claim that the pilot, a model call,
or acceptance review has run.

## Baseline and work policy

- Original baseline commit: `301516d`.
- Implement and review in an independent worktree. Do not modify the original
  checkout while its generator is active. The parent has now completed; the
  owner authorized committing and merging the verified work into `main`.
- Reuse only the exact frozen source snapshots, source-family assignments and
  splits from the selected native run. Do not download, replace or resplit data.
- During implementation and offline verification, make no LLM endpoint request,
  including `/v1/models` discovery. Default tests must remain offline.
- Plan preparation may run while native generation is active after the source
  snapshots, family assignments and splits are frozen. Do not execute the
  planned projection pilot until its intended parent is complete.

## Public interface

Prepare a new immutable projection plan:

```text
./scripts/prepare-source-projections --from-run /absolute/path/to/run [--pilot]
  [--output /absolute/path/to/new-directory]
foliqant-model prepare-source-projections --from-run /absolute/path/to/run [--pilot]
  [--output /absolute/path/to/new-directory]
```

Preparation is offline by contract. It reads verified frozen inputs, emits
selected/projected/excluded counts and plan identity, and creates no endpoint
client. Missing inputs fail closed; preparation never downloads them. The source
run remains unchanged.

`--pilot` produces exactly 32 training tasks: eight MultiDoGO, eight TAT-QA and
16 typed-decisions tasks, with four typed records from each workflow. Omitting
`--pilot` produces a separate full plan from the same parent inputs.

Apply the plan through a new immutable child:

```text
./scripts/generate-data
  --extend-projections-from /absolute/path/to/COMPLETED-parent
  --projection-plan /absolute/path/to/projection-plan-directory
  --progress always
```

`foliqant-model curate` accepts the same two extension options. They are required
as a pair and are mutually exclusive with `--continue-from` and `--repair-from`.
The plan may be prepared before parent completion from already frozen inputs,
but the parent must be complete before an extension is executed. Extension
`--prepare-only` is allowed after parent completion and must not perform model
discovery or inference. A full extension creates only new projection
blind-verification jobs. It preserves all old outcomes byte-for-byte and never
advances the parent. Repeating the exact command resumes the same child.

The plan is the capability switch. Normal `generate-data`, pilot, continuation
and repair commands retain the legacy projection behavior. There is no automatic
migration of an old run and no implicit projection from the new source types.

## Projection rules

### MultiDoGO finance

- Build one native `multiselect` question over the fixed 18-intent catalog.
- Use only the declared intent set as the proposed native target.
- Retain the raw redacted turn and token-aligned slot labels in the raw source
  record and provenance. Do not project slot values or slot types.
- Preserve source family, split, rights and original-record identity.

### typed-decisions

- Project the entire five-question task for every eligible selected record.
- Map source `choice`, `noul` and `score` to native `choice`, `predicate` and
  `ordinal` respectively.
- Use each question's explicit source `label`. Never infer a target with argmax
  over a teacher distribution.
- Preserve every raw teacher distribution in source data. Do not expose a
  probability or agreement score as native confidence.
- Treat each source label as a proposed answer. Publication requires an
  independent blind solve that verifies both answer and answerability.
- Preserve all four workflows. The pilot allocation is four records per
  workflow, 16 typed-decisions tasks total.

### TAT-QA

- Emit at most one deterministic native predicate per source context.
- Select only a unique table row under adjacent, unique, explicit year headers
  from 1900 through 2099.
- Require both displayed cells to parse as strict signed decimals and to use
  matching unit markers: both unmarked, the same currency on both, or percent on both.
- Compare the displayed values directly. Do not infer hidden scale, apply a
  financial derivation, or generalize this into full financial question
  answering.
- Do not inspect or use the original TAT-QA gold answer when selecting or
  producing the proposed target.

## Shared invariants

- Resolve all new candidates sharing an answer-bearing task identity before any
  source cap or pilot quota. If their targets conflict, exclude every new member
  of that group. The currently frozen inputs produce 24 such MultiDoGO
  exclusions; this count is offline preparation evidence, not label-quality
  acceptance.
- Exclude every new member of an answer-bearing alias group that crosses frozen
  splits or frozen source families. For a same-target group within one family
  and split, retain the lexicographically smallest source record ID as the
  deterministic representative and count the rest as duplicate exclusions.
- Existing baseline native tasks are immutable. A matching or conflicting
  projection is excluded without deleting, rewriting or relabeling its baseline
  counterpart.
- Every selected record either projects deterministically or receives one stable
  exclusion reason. Reports include strict selected, projected and excluded
  counts by source and reason.
- All raw English source records and annotations remain in `source-corpus`,
  including typed distributions and MultiDoGO slot labels.
- New native tasks stay English. No translation or relabeling creates additional
  tasks.
- Every projection inherits the exact frozen family and split. No repartitioning
  or cross-split family movement is allowed.
- Projected targets remain proposed, unreviewed research labels. Only a blind
  verification job may admit a training projection, and acceptance creates no
  prose augmentation duplicate.
- Rights, source identity and original-record provenance remain attached.

## Planned pilot and sequencing

The bounded pilot contains 32 new verification tasks:

| Source | Planned tasks | Selection shape |
|---|---:|---|
| MultiDoGO finance | 8 | intent-only multiselect |
| TAT-QA | 8 | one eligible table comparison per selected context |
| typed-decisions | 16 | four records from each of four workflows; all five questions per record |

This allocation is a plan, not an observed quality result. It may be prepared
while active native generation continues because preparation reads only the
already frozen inputs. Apply the pilot plan with the normal extension command
and `--progress always` only after the parent completes. Prepare the full plan
separately from the same parent; it does not promise reuse of pilot model calls
or outcomes. Model validation remains deferred and the pilot has not run. Record
any later execution evidence separately under `plans/reviews/`; do not retrofit
it into this approval plan.

## Completed offline implementation track

1. Added strict plan contracts, hashing, safe path handling and immutable output.
2. Implemented the three deterministic converters, group-wide identity handling
   and stable exclusion reasons before selection caps.
3. Added `prepare-source-projections` to the package CLI and repository wrapper,
   with no endpoint/client construction and no downloader path.
4. Added the paired extension options to `curate` and `generate-data`, enforcing
   completed-parent and mutual-exclusion rules.
5. Created an immutable child from verified parent plus projection-plan inputs;
   retain old outcomes and schedule only the new train verification jobs.
6. Covered projection semantics, counts, frozen split/family reuse, lineage,
   offline behavior, prepare-only behavior and immutable resume with offline
   tests.
7. Updated schemas, examples, public docs and the repository skill together.

## Offline verification before any pilot

- CLI help exposes exactly the approved interfaces and rejects partial or
  conflicting flag combinations.
- Preparation and extension `--prepare-only` succeed with network and endpoint
  access forbidden and make zero discovery/inference requests.
- Missing cached sources fail instead of downloading.
- Golden fixtures prove the MultiDoGO 18-catalog mapping, all five typed question
  mappings with explicit-label selection, and TAT-QA's strict eligibility rules.
- Negative fixtures prove stable exclusion counts for ambiguous catalogs,
  invalid labels, non-unique rows/headers, non-adjacent or out-of-range years,
  malformed signed decimals and mismatched currency/percentage units.
- Tests prove raw records and annotations remain present, no translation is
  created, source families/splits are unchanged, and old outcomes are
  byte-for-byte identical in the child lineage.
- Default repository checks and documentation/skill validation pass without
  model execution or downloads.

Real endpoint execution, review of the 32 planned results, publication checks
and any generated-data quality acceptance claim are intentionally deferred until
the parent generation has completed. Offline implementation completion does not
establish model or dataset quality.
