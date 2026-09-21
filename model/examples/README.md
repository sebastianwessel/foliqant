# Local exercise configurations

`native-full.yaml` and `native-pilot.yaml` are the native decision-data recipes.
Run `./scripts/generate-data` for full generation or add `--pilot` for a separate
16-candidate check (eight priority scenarios per language). `--prepare-only` prepares sources and typed tasks without
inference. See [the guide](../../docs/guides/native-decision-data.md) for coverage
gates and outputs. The native recipes use `examplesPerScenario`; this caps the
finite genuine-case catalog and does not promise that many rows. The initial
catalog has four cases per scenario and language, and larger values still select four. Semantic
template groups control split isolation and diversity reporting. Both native profiles select English and German. German cases retain German response prose; translations share their English sibling's partition. All generated
data remains unreviewed research data outside Git.

Both native recipes explicitly select standalone Splash on loopback port 8000,
model `incoai/Qwen3.8-27B-Splash`, low reasoning, temperature `0.1`, 8,192
generated tokens and a 300-second timeout. Root `.env` overrides endpoint settings
for your machine. Requests remain sequential. The generic `curation.yaml` recipe
retains provider-neutral endpoint settings; it is not the native full-run recipe.

Authored seeds explicitly limit which non-metadata state sources can be
rewritten. Adequacy cases rewrite only `original-state`; their task contract and
proposed answer stay fixed. Other authored cases allow their non-metadata source
text to be rewritten, while projected verification changes no source text.

Recipe, task-contract and prompt changes create a fresh run identity. Resume
only with the exact same recipe, effective `.env` and workspace; never copy or
edit old request/outcome caches into a repaired run.

The native recipes project BANKING77 into choices with versioned editorial
category definitions and WANLI into three-way text-relation choices. Neutral is
an answerable relation category. These references remain unreviewed; valid model
disagreement is quarantined without automatic label-chasing retries. Recoverable
output errors receive phase-specific feedback, reusing an already valid rewrite.
Additional typed-decisions, MultiDoGO and TAT-QA mappings require an
explicit offline plan derived from an existing frozen run:

```sh
./scripts/prepare-source-projections --from-run /absolute/path/to/run --pilot \
  --output /absolute/path/to/new-projection-plan
./scripts/generate-data \
  --extend-projections-from /absolute/path/to/completed-parent-run \
  --projection-plan /absolute/path/to/new-projection-plan \
  --progress always
```

Plan preparation makes no model request or download, including model discovery.
It may run while the parent is generating once source snapshots, families and
splits are frozen; extension execution must wait for parent completion.
The extension flags must appear together and cannot be combined with
continuation or repair. `--prepare-only` is allowed after the parent completes
and also performs no model discovery. Full execution creates only new projection
verification jobs in an immutable child; prior outcomes remain unchanged. See
the guide for the exact bounded mappings and exclusions.
Candidate identity groups are resolved before caps: all new conflicting-target,
cross-split or cross-family members are excluded, while same-target duplicates
within one frozen family retain the deterministic smallest-record-ID
representative. Existing baseline tasks are unchanged.
The pilot plan is exactly 32 training tasks: eight MultiDoGO, eight TAT-QA and
16 typed-decisions tasks with four from each workflow. Prepare a full plan
separately from the same parent by omitting `--pilot`; do not assume its model
calls can reuse pilot calls. Offline implementation is complete; the live
projection pilot and quality acceptance remain deferred.

These checked-in recipes contain no data or weights. `curation.yaml` selects the
five pinned public sources at no more than 1,000 records each and bounds local
generation to 100 jobs. It omits `endpoint.model`: exactly one model must be
loaded in LM Studio for automatic selection. If several models are exposed, copy
the recipe and set the exact local model ID.

Prepare the sources without a model:

```sh
./scripts/curate-data --prepare-only
```

After LM Studio is running, rerun `./scripts/curate-data` with the same recipe
and workspace to resume generation. This publishes diagnostic datasets; it does
not train.

`train.yaml` and `evaluation.yaml` are small diagnostic recipes for the model and
prepared datasets produced by `./scripts/setup-model`. Use the paths printed by
setup and choose a new output directory whose parent already exists.

```sh
uv run --project model --no-sync foliqant-model train \
  --config model/examples/train.yaml \
  --model /path/from/modelPath \
  --dataset /path/from/sharedDatasetPath \
  --output /path/to/new-shared-adapter
```

The same training configuration can be used with `customize` after merging the
shared adapter. Pass the customer dataset and a customer identifier. Two steps
exercise the code; they do not qualify a financial model.

Read [automated curation](../../docs/guides/automated-curation.md) for source
terms, outputs and resume behavior. Follow
[train and customize](../../docs/guides/train-and-customize.md), then
[evaluate and audit](../../docs/guides/evaluate-and-audit.md) and
[export and run](../../docs/guides/export-and-run.md).
