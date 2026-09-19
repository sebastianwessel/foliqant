# Local exercise configurations

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
