# Train and export your first adapter

This exercise uses the small model and prepared data from setup. You will train
an adapter, measure a few held-out responses, merge it into its parent model and
export an ordinary checkpoint. Two training steps check the process; they do not
produce a qualified financial model.

Run these commands from the repository root on native Apple Silicon macOS.

## Prepare the exercise

```sh
./scripts/setup-model
```

Setup prints a JSON result containing the model, dataset and configuration paths.
The following commands read those paths from a verified offline rerun, so you
do not need to type the versioned setup directory yourself:

```sh
setup_result=$(uv run --project model --no-sync foliqant-model setup --offline)
EXERCISE=$(printf '%s' "$setup_result" | uv run --project model --no-sync python -c \
  'import json, sys; from pathlib import Path; print(Path(json.load(sys.stdin)["result"]["receiptPath"]).parent)')
OUTPUT="$PWD/.foliqant/exercises/first-model"
mkdir -p "$OUTPUT"
```

Keep this terminal open so the variables remain available. If you already ran
this exercise, choose a different `OUTPUT` name. Completed artifacts are never
overwritten.

## Train a shared adapter

```sh
uv run --project model --no-sync foliqant-model train \
  --config model/examples/train.yaml \
  --model "$EXERCISE/artifacts/upstream" \
  --dataset "$EXERCISE/artifacts/shared" \
  --output "$OUTPUT/shared-adapter"
```

The command runs two local training steps and reports the observed loss and
adapter identity. The original checkpoint is unchanged. The new adapter records
the exact parent, data and permissions used to create it.

## Measure held-out responses

```sh
uv run --project model --no-sync foliqant-model evaluate \
  --config model/examples/evaluation.yaml \
  --model "$EXERCISE/artifacts/upstream" \
  --adapter "$OUTPUT/shared-adapter" \
  --dataset "$EXERCISE/artifacts/shared" \
  --split validation \
  --output "$OUTPUT/validation"
```

This recipe generates at most 32 tokens for each of four examples. The result
reports exact matches and the location of private prediction records. Low
accuracy or truncated answers are valid measurements: `ok: true` means the
evaluation ran, not that the model is ready for use.

## Merge and export

Merge the adapter with the exact checkpoint used for training:

```sh
uv run --project model --no-sync foliqant-model merge \
  --model "$EXERCISE/artifacts/upstream" \
  --adapter "$OUTPUT/shared-adapter" \
  --output "$OUTPUT/shared-merged"

uv run --project model --no-sync foliqant-model export \
  --model "$OUTPUT/shared-merged" \
  --format checkpoint \
  --output "$OUTPUT/checkpoint"

uv run --project model --no-sync foliqant-model verify "$OUTPUT/checkpoint"
```

The checkpoint contains ordinary Safetensors weights, tokenizer files and
configuration, together with its lineage manifest. Verification checks integrity;
an [independent runtime smoke test](../guides/export-and-run.md) checks that the
export actually loads in the intended inference library.

Next, [prepare your own dataset](../guides/prepare-data.md), or use the shared
merged artifact for [customer customization](../guides/train-and-customize.md).
Before deciding when a model may answer automatically, follow
[evaluation, calibration and independent audit](../guides/evaluate-and-audit.md).
