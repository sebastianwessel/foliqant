# Train and customize adapters

Training creates a small LoRA adapter beside an existing model. The original
model stays unchanged. Shared training builds a reusable adapter from approved
shared data. Customer customization builds a separate customer-scoped adapter
from a released shared model.

Use native Apple Silicon macOS with the MLX dependencies installed. Training is
a local child process with library offline mode enabled. This is not an operating-system network sandbox. Every input and output is a
verified artifact with an exact content identity.

## Create a training configuration

Save a strict UTF-8 YAML or JSON file. This small example runs two microbatches:

```yaml
schemaVersion: 1
name: practice-adapter
seed: 42
steps: 2
batchSize: 1
gradientAccumulation: 1
maxSequenceLength: 2048
learningRate: 0.0001
numLayers: 2
rank: 4
scale: 8.0
dropout: 0.0
gradientCheckpointing: true
validationEvery: 1
validationBatches: 1
saveEvery: 1
timeoutSeconds: 3600
```

`steps` counts microbatches and must be divisible by `gradientAccumulation`.
Both the train and validation partitions need at least `batchSize` records.
Training rejects any record that exceeds `maxSequenceLength` after tokenization;
curate or split that source record rather than relying on hidden truncation.

## Train a shared adapter

Every source in the prepared dataset must declare `sharedTrainingAllowed: true`.
Use an upstream checkpoint, a shared merged model, or an eligible quantized form
of either model.

```sh
uv run --project model --no-sync foliqant-model train \
  --config /absolute/path/to/train.yaml \
  --model /absolute/path/to/upstream-model \
  --dataset /absolute/path/to/shared-dataset \
  --output /absolute/path/to/new-shared-adapter
```

The output path must not exist. Success prints one JSON object with the adapter
identity, final observed loss and elapsed training time. The adapter manifest
also records validation observations, measured peak memory, resolved tensor
names, exact parent snapshots, dataset hashes and the union of source rights.
Loss is an observed training metric; it is not a measure of financial accuracy.

The worker receives private copies of `train` and `validation`. It never receives
the calibration or test partitions.

## Merge before customer customization

Customer customization starts from a merged model at the shared stage, or its
directly derived quantized artifact. Merge the shared adapter into its exact
model parent as described in [export and run](export-and-run.md), then use a
separate prepared customer dataset:

```sh
uv run --project model --no-sync foliqant-model customize \
  --config /absolute/path/to/train.yaml \
  --model /absolute/path/to/shared-merged-model \
  --dataset /absolute/path/to/customer-dataset \
  --customer customer-a \
  --output /absolute/path/to/customer-a-adapter
```

The customer identifier becomes part of the artifact scope. A customer adapter
cannot be used as shared ancestry, and an adapter from another customer is
rejected. Customer data does not need shared-training permission, but it still
needs `trainingAllowed: true` and any required private-data authorization.

## Continue from adapter weights

Use `--warm-start` to load a completed compatible adapter and run additional
steps in a new immutable artifact:

```sh
uv run --project model --no-sync foliqant-model train \
  --config /absolute/path/to/next-train.yaml \
  --model /absolute/path/to/the-same-model-parent \
  --dataset /absolute/path/to/next-shared-dataset \
  --warm-start /absolute/path/to/previous-adapter \
  --output /absolute/path/to/new-adapter
```

The model identity, shared/customer scope, customer identifier, rank, scale,
dropout, layer count and resolved target tensors must match. Seed, learning rate,
dataset and additional step count may change. Warm start reloads adapter weights;
it does not restore optimizer state or promise a bit-for-bit continuation.

Verify any completed adapter before the next operation:

```sh
uv run --project model --no-sync foliqant-model verify /absolute/path/to/new-adapter
```

Next: [evaluate and audit](evaluate-and-audit.md).
