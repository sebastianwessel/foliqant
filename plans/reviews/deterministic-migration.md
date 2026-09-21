# Deterministic migration execution

Executed 2026-09-21 against completed run
`native-financial-decisions-en-de-v1-continue-3beaee64b8c2`.
This is dataset preparation evidence, not training or production-quality acceptance.

## Published data

Migration: `~/.local/share/foliqant/migrations/66d38c5295341f88`.
Dataset artifact: `6cdfb1a6d217b19e623e0473cde6834e573280c4cada837a1a46481e30c31ce3`.

The migration ran with socket connections forbidden. It preserved the original
2,337 non-lock files byte-for-byte and published a separate dataset. Repeating
the actual `scripts/migrate-data` command produced the identical result and
unchanged migration files.

| Operation | Records |
|---|---:|
| Exact original authored/reference and generated rows | 438 |
| BANKING77 deterministic reprojection | 446 |
| WANLI deterministic reprojection | 361 |
| MultiDoGO annotation projections | 500 |
| typed-decisions teacher-reference projections | 500 |
| TAT-QA deterministic table predicates | 53 |
| Additional same-state question variants | 516 |
| Total | 2,814 |

| Split | Records | Independent families | English | German |
|---|---:|---:|---:|---:|
| Train | 1,769 | 1,036 | 1,571 | 198 |
| Validation | 282 | 183 | 271 | 11 |
| Calibration | 264 | 153 | 249 | 15 |
| Test | 499 | 360 | 498 | 1 |

All published records were compared with the frozen migration plan. All retained
records equal their historical records. Every question variant has exactly its
parent's state and frozen family; none adds an independent evaluation family.
There are 19 cardinality-one variants with `multiple_valid_options`; the other
497 have a single supported label. Only three imported MultiDoGO originals have
multiple annotated intents: the larger variant count is not a claim of broad
multi-intent coverage. Existing authored examples supply the other 16 cases.

The evidence-status inventory is explicit: 246 authored references, 192
historically accepted generated rows, 1,807 source-annotation references, 53
deterministic comparisons, and 516 derived references. The migration did not
blindly verify these new tasks with a model. Source labels can be noisy.

`provenance.jsonl` joins on record ID and identifies original dataset, original
record, pinned snapshot hash, rights declaration, parent and derivation rule.
`source-annotations.jsonl` retains complete linked imported records, including
annotations not used by a projection. `source-rights.json` preserves the license
evidence, restrictions and commercial-use assessment. Private storage does not
waive source terms. Chat JSONL for training remains under
`datasets/native-decisions/chats/`; rich records and provenance remain separate.

## Pending-only execution

Initial migration: 150 pending training tasks and 61 disputed rows for review.
The pending queue contains 139 WANLI tasks whose semantics changed to explicit
relation classification, plus 11 structurally invalid authored generations.
Previously disputed unchanged tasks are not silently promoted or retried.

Two sequential CLI smoke tasks used the saved Splash model identity, low
reasoning and temperature 0.1. They issued three model requests in total, with
no discovery request. Both completed and remained quarantined:

- The rewrite task's solver returned `multiple_valid_options` for an unresolved
  referent; its reference requires `missing_information`.
- The WANLI solver returned entailment where the source annotation says neutral.
  Inspection suggests a plausible source-label dispute; this is not proof that
  the solver is wrong. The script preserves both rather than changing the label.

Both complete responses and validation reasons remain in the child request and
outcome files. All 2,814 published rows stayed unchanged. The first exact CLI
repeat added no requests and changed no bytes. The second repeated successfully
with both network connections and generation explicitly forbidden.

The latest combined child has **148 pending tasks and 63 review items**:
`~/.local/share/foliqant/migrations/reruns/reruns/5f772a03e0e4aa9382e3740688823909c46f63833848ec54f38c420652d2d16a`.

To run its remaining queue, without replaying the original dataset or the two
completed smoke tasks:

```sh
./scripts/rerun-migrated-data \
  --from-migration "$HOME/.local/share/foliqant/migrations/reruns/reruns/5f772a03e0e4aa9382e3740688823909c46f63833848ec54f38c420652d2d16a" \
  --progress always
```

Optional `--limit N` bounds the pending selection; repeated `--job-id RECORD_ID`
selects individual pending records. Repeat the same command to resume. Its
reported child becomes the input for subsequent remaining work. A changed
selection against the older parent is a separate run, not a way to resume a
different child. No full rerun or new Hugging Face upload was started.

## Verification

The final offline suite passed **933 tests**, with seven native integration
tests deselected (103.91 seconds). Strict typing passed for 68 source files;
lint, formatting of the migration changes, all 27 generated schemas, CLI help,
documentation, skill, specification and tracked-data audits passed. The skill
audit retains its advisory warning about the existing skill's length. Native MLX
training tests remain outside this dataset migration's scope. The live smoke establishes
request, rejection-preservation and immutable resume behavior; it does not
establish an acceptance rate or improved model accuracy.
