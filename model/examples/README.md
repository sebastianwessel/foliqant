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
uv run --no-sync foliqant-model train \
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
