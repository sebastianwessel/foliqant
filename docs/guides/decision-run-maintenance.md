# Extend and migrate decision runs

Use these operations after the main [native decision-data workflow](native-decision-data.md).
They verify frozen inputs and publish immutable children; they never modify or
relabel the parent run.

## Upgrade native contract version 1

Use this for a published native dataset artifact with frozen splits and
`schemaVersion: 1`. Records must use the three-message system/user/assistant
layout produced by the native data tooling. The current
input and output contract is version 2. No model calls or regeneration are needed:

```sh
uv run --project model --no-sync foliqant-model upgrade-decision-data \
  --from-dataset /absolute/path/to/existing/datasets/native-decisions \
  --output /absolute/path/to/new-upgrade
```

Supply the dataset artifact, not its enclosing generation or migration run.
The command recognizes the shipped V1 system instructions and their audited
English/German contract criteria. Unknown contract-bearing prompt variants are
rejected for review instead of rewritten by guesswork. It strictly checks the
old records, replaces `missing_information` and
`no_matching_option` with `no_supported_answer`, removes duplicate merged issues,
and updates native versions and contract instructions. Answers, source text,
explanations, evidence, language, review status, rights, and frozen splits remain
unchanged. No quarantined or disputed row becomes accepted through this upgrade.

It publishes a new dataset with parent and per-record provenance, ordinary chat
JSONL for fine-tuning, and full records for auditing. The original dataset and
reports remain immutable. Verify the returned `datasetPath` with
`foliqant-model verify /absolute/path/to/new-upgrade/datasets/native-decisions`.
The parent must remain available for ancestry verification. Repeating the exact
command checks and reuses the completed upgrade; it cannot overwrite a different
artifact. Existing model weights and adapters are not changed by a data upgrade.

Runtime, generation, training, and evaluation require current native records.
Ordinary non-native chat datasets keep their existing contract. Source
reprojection below is a separate operation; do not use continuation or repair
to reinterpret an older contract's cached responses.

## Add scoped source projections

The standard recipes project BANKING77 and WANLI records. typed-decisions,
MultiDoGO, and TAT-QA mappings are opt-in because their native targets need
narrower deterministic conversions.

Prepare a projection plan without downloads, model discovery, or model calls:

```sh
uv run --project model --no-sync foliqant-model prepare-source-projections \
  --from-run /absolute/path/to/run \
  --pilot \
  --output /absolute/path/to/new-projection-plan
```

The source run may still be generating while you prepare the plan, but it must
be complete before you apply the extension. Omit `--pilot` for all eligible
records. Omit `--output` only when the reported default workspace path is
acceptable.

Apply the plan after the parent completes:

```sh
./scripts/generate-data \
  --extend-projections-from /absolute/path/to/completed-parent-run \
  --projection-plan /absolute/path/to/projection-plan \
  --progress always
```

The options are a required pair and cannot be combined with `--continue-from`
or `--repair-from`. Add `--prepare-only` to validate and stage without endpoint
discovery or inference. Rerun the same command to resume its child.

The pilot contains 32 training tasks: eight MultiDoGO intent tasks, eight
TAT-QA table-comparison tasks, and 16 typed-decisions tasks. The mappings retain
the frozen source family and split. Excluded rows and stable reasons appear in
the report. Accepted rows remain unreviewed research data.

## Migrate a completed run

Migration republishes eligible records under the current deterministic
projection and question-variant rules:

```sh
uv run --project model --no-sync foliqant-model migrate-decisions \
  --from-run /absolute/path/to/completed-run \
  --output /absolute/path/to/new-migration
```

Migration is offline. It verifies and reuses the parent's immutable sources,
rights, families, and splits and reports unsupported or disputed rows for
review. It differs from continuation: `--continue-from` preserves the parent's
recipe and finishes missing work, while migration applies current deterministic
rules with new ancestry.

Rows that still need model verification enter a frozen pending queue. Run all
or a selected part of it separately:

```sh
uv run --project model --no-sync foliqant-model rerun-migrated-decisions \
  --from-migration /absolute/path/to/migration \
  --limit 8 \
  --job-id native-record-id \
  --progress always
```

Repeat `--job-id` to select more records. Without it, the command uses the
sorted pending queue; `--limit` bounds that selection. This command performs
local model inference with the frozen model identity and never submits held-out
or review-only rows. It stores resumable outcomes in a separate immutable child;
rerun the exact command after interruption. It never discovers or substitutes a
different model. Unsupported, disputed, and quarantined evidence remains
retained for review rather than being silently accepted or discarded.

The final result reports the migration, dataset, artifact, and report paths plus
record, pending-task, and review-item counts. Verify the returned dataset path
before training:

```sh
uv run --project model --no-sync foliqant-model verify \
  /absolute/path/to/migrated-dataset
```
