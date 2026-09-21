# Setup and data operations

## Contents

- [Choose the operation](#choose-the-operation)
- [Native data and recovery](#native-data-and-recovery)
- [Source projections and migration](#source-projections-and-migration)
- [Decision semantics](#decision-semantics)
- [Category catalogs](#category-catalogs)
- [Rights and verification](#rights-and-verification)

## Choose the operation

Read the relevant public guide and current command help before constructing a run.
Use the selected recipe and effective environment; never hard-code a model, port,
sample count or generation limit from an earlier review.

| Intent | Existing entry point | Boundary |
| --- | --- | --- |
| Initial assets and environment | `./scripts/setup-model` | Pinned setup assets outside Git; no training |
| Auxiliary curation | `./scripts/curate-data` | Source preparation and configured bounded generation |
| Native decisions | `./scripts/generate-data` | `--pilot` selects its own recipe; `--prepare-only` makes no inference calls |
| Frozen source-projection plan | `./scripts/prepare-source-projections --from-run RUN --output NEW` | Entirely offline; existing frozen inputs required |
| Native V1 contract upgrade | `foliqant-model upgrade-decision-data --from-dataset DATASET --output NEW` | Entirely offline; strict V1 boundary, immutable V2 dataset |
| Completed-run migration | `./scripts/migrate-data --from-run RUN --output NEW` | Entirely offline; new immutable descendant |
| Pending migrated tasks | `./scripts/rerun-migrated-data --from-migration DIR` | Inference for saved pending train tasks only |

Public procedures: [setup](../../../docs/getting-started/setup.md),
[auxiliary curation](../../../docs/guides/automated-curation.md),
[native generation](../../../docs/guides/native-decision-data.md), and
[run maintenance](../../../docs/guides/decision-run-maintenance.md).

## Native data and recovery

Raw source tasks stay in `source-corpus`; all diagnostic parents stay in
`native-seeds`. Published `native-decisions` includes held-out diagnostic seeds,
accepted source verifications and accepted rewrites with only required train
parents. Unattempted and quarantined train parents do not enter it. Count
accepted candidate jobs separately from published rows and attempts.

Only train families enter generation. Connected originals, translations,
paraphrases and variants retain frozen family/split identity. English source
records remain English; German records retain German prose and exact citations.
Machine IDs/enums stay stable. Language metadata alone does not detect language.

Resume by rerunning the same command with identical recipe, effective environment,
workspace and model identity. No `--resume` flag exists. `--progress` controls
stderr display without changing identity or final JSON stdout. First Ctrl+C
persists at the safe candidate/preparation boundary; another interrupt may cause
the current request to repeat. A timeout does not prove the remote server stopped.

Use supported `--continue-from` only for compatible native runs and missing
jobs; it carries accepted and quarantined outcomes unchanged. Supported
`--repair-from` requires a parent that finished its jobs (coverage failure is
allowed), compatible identities and recoverable rejections. It cannot repair a
paused run or semantic disagreement by repeated label-chasing. Never combine
continuation, repair or projection extension options.

Inspect the source, retained response and typed failure together. Preserve a
validated rewrite for solver-only repair; rewrite defects go back to rewriting.
Valid disagreement with a reference requires reference review. Missing historical
response text cannot be recovered by inventing it. Do not edit caches, bypass
validation or move outcomes between changed tasks. A changed prompt, projection,
validator or request format needs its defined versioned recipe/new run path.

Generation uses bounded standard SSE and host validation. Reasoning text is
discarded. The 1,024-character unquoted-whitespace guard retains partial final
content only for bounded recovery, never as an accepted answer. Malformed streams,
truncations and timeouts do not become unknown answers. Missing token usage remains
unknown. Do not silently change reasoning, timeout, schema or output limits to
make a run pass.

## Source projections and migration

Projection preparation requires verified frozen source snapshots, families and
splits, makes zero downloads or model/discovery calls, and may occur while parent
generation is active. Execution waits for the completed parent and requires both
`--extend-projections-from` and `--projection-plan`. The plan binds that exact
parent; after repair, prepare a new plan from the completed repair child.

BANKING77 and WANLI are default mappings; typed-decisions, MultiDoGO and TAT-QA
extensions are explicit opt-ins. Resolve complete answer-bearing groups before
caps: exclude conflicting targets and cross-family/split aliases; retain the
smallest record ID within same-family, same-target duplicates. Preserve baseline
tasks. Projection `--pilot` selects its fixed 32-task plan; a later full plan is
separate and does not reuse pilot calls. Do not claim live validation from planning.

Migration deterministically reprojects a completed snapshot and records per-row
source, rights, rule and ancestry. It is not continuation or model verification.
Keep original authored/generated records immutable. Same-state question variants
retain language, evidence, family and split; never invent a primary category.
Only the migration's frozen pending train queue may be rerun with its saved model
identity. `--limit` and repeated `--job-id` select a bounded subset; review items
and held-out families cannot be submitted. Exact reruns reuse immutable outcomes.

Native contract upgrades are separate from source reprojection. Use
`upgrade-decision-data` for recognized published V1 native datasets with frozen
splits and the native three-message layout; unknown instruction revisions fail
for review. Never regenerate all rows
or rewrite a parent. It maps the old missing/no-match issues to
`no_supported_answer`, deduplicates, updates native versions/instructions and
preserves answers, explanations, source content, languages, review status,
rights and frozen partitions. Verify the new dataset and keep its parent
available. Run-local quarantines and review items are outside the dataset upgrade and
remain untouched in the source run; this operation does not promote them or
update trained weights. Current prepare,
train and evaluation paths reject legacy native records; generic chat remains
supported. See the [upgrade procedure](../../../docs/guides/decision-run-maintenance.md#upgrade-native-contract-version-1).

## Decision semantics

Use the existing validator and [decision contract guide](../../../docs/guides/decision-contracts.md).
Do not embed domain phrases, record IDs or expected answers in generic validators.

- Explicit evidence of absence may support answerable false; absent evidence is
  unknown. Missing referents and requests outside the catalog use `no_supported_answer`;
  explain the particular obstacle without a second machine-readable split.
- `multiple_valid_options` requires multiple positively supported catalog options
  exceeding cardinality. Preserve every applicable issue, including
  `conflicting_information` and `no_supported_answer`.
- Return every request unit; a null category adds `no_supported_answer`. Keep
  distinct same-category requests separate. Conditional requests stay conditional;
  extraction records their gate rather than executing it.
- A subject is a discriminating literal reference in that unit's own evidence,
  not a generic document/product/action. Use null when no identifier exists.
- Keep one grounded explanation summary (target 160 characters, hard maximum 400).
  Do not truncate it; exact citations and missing facts have separate bounds.
- Blind verification never receives expected answers or scenario labels.
  Agreement, lexical guards and schema validity do not prove semantic truth.
  Published targets retain unreviewed status.

## Category catalogs

New catalogs use `CategoryCatalog` from `foliqant.decisions.category_catalog`:
required descriptions, deterministic snake_case IDs, collision rejection and
`decision_options()` for existing question types. `resolve_id` normalizes then
checks exact membership; never fuzzy-match or invent labels. Preserve historical
V1 artifact labels and recipe identity. Descriptions define inclusion/exclusion;
priority, request kind, topic and policy-derived due dates are separate concepts.

## Rights and verification

For custom data use explicit source declarations and
[prepare data](../../../docs/guides/prepare-data.md). Preserve attribution,
commercial-use assessment, restrictions and shared-training permission through
ancestry. Source annotations and teacher distributions are proposed references,
not human-gold or calibrated confidence. Private hosting does not waive terms.

Verify immutable artifacts with the actual CLI and inspect coverage/rejection
reports. Regression replays establish validator behavior; separately authorized
fresh pilots establish only their sampled generation behavior. Neither establishes
full-recipe coverage, training readiness, population accuracy or production fitness.

## Workspace and environment

Repository wrappers default to ignored `.foliqant/` in the checkout. Installed
CLI commands default to `.foliqant/` in their current directory; `--workspace`
selects another location. The shared workspace helper rejects unignored or
already tracked data directories. Never commit datasets, weights or reports.
The Python curation CLI reuses the environment loader (process over local `.env`,
no interpolation) and forwards only curation settings to the existing typed
recipe override function. Unrelated secrets are not passed into that path.

When moving existing data, verify no owner process is active and preserve all
bytes/ancestry. Historic records may contain absolute paths; keep those references
reachable instead of editing completed records and regenerating their hashes.
