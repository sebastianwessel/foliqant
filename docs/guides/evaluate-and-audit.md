# Evaluate and audit a model

Evaluation runs deterministic local generation against one held-out dataset
partition. Calibration then selects an empirical acceptance threshold from the
calibration split. Audit applies that fixed policy once to the separate test
split.

These operations measure a particular model, adapter, configuration and dataset.
They do not certify legal correctness or future production behavior.

## Configure generation and scoring

Save a strict UTF-8 YAML or JSON file:

```yaml
schemaVersion: 1
seed: 42
maxTokens: 256
maxExamples: 100
timeoutSeconds: 3600
fieldPointers: []
```

Omit `maxExamples` to evaluate every row in the selected partition. Selection is
deterministic by record ID. Generation uses temperature zero. Plain text is
allowed when no JSON validators are configured.

For structured output, add one or more of these fields:

```yaml
outputSchema: result.schema.json
evidencePointer: /evidence
fieldPointers:
  - /decision
  - /reason
```

`outputSchema` resolves relative to the evaluation configuration and must be a
local Draft 2020-12 JSON Schema with no external references. An evidence value
must be a nonempty string, or list of nonempty strings, copied exactly from an
input message. Configuring a schema, evidence pointer or field pointer makes
valid JSON required.

## Evaluate an adapter

Use the adapter with the exact model parent it was trained for:

```sh
uv run --no-sync foliqant-model evaluate \
  --config /absolute/path/to/evaluation.yaml \
  --model /absolute/path/to/model \
  --adapter /absolute/path/to/adapter \
  --dataset /absolute/path/to/prepared-dataset \
  --split validation \
  --output /absolute/path/to/validation-evaluation
```

Omit `--adapter` when evaluating a merged, quantized or upstream model directly.
The success result reports the number of examples, the number of component
representatives, exact correctness and the private predictions path. The
artifact also records JSON, schema, evidence and configured-field metrics with
language and source slices.

Validation is useful while developing a model. Do not select an acceptance
policy from it. Evaluation on `calibration` or `test` rejects overlap with the
model and adapter training ancestry by record ID, declared group, exact
conversation hash or prompt hash.

## Select a policy on calibration data

First produce a calibration evaluation with the same model, optional adapter,
dataset and evaluation configuration intended for the later test:

```sh
uv run --no-sync foliqant-model evaluate \
  --config /absolute/path/to/evaluation.yaml \
  --model /absolute/path/to/model \
  --adapter /absolute/path/to/adapter \
  --dataset /absolute/path/to/prepared-dataset \
  --split calibration \
  --output /absolute/path/to/calibration-evaluation
```

Then select a threshold. This example allows at most a 10% empirical error rate
and requires at least 20 accepted component representatives:

```sh
uv run --no-sync foliqant-model calibrate \
  --evaluation /absolute/path/to/calibration-evaluation \
  --max-error 0.1 \
  --min-accepted 20 \
  --output /absolute/path/to/policy
```

The score is the mean generated-token log probability. It is not a probability
that the answer is correct. Calibration uses one predetermined representative
per declared dataset component. If no threshold meets both constraints, the
result is an abstain-all policy with a null threshold.

## Audit once on the test split

Create a test evaluation without changing the deployment profile:

```sh
uv run --no-sync foliqant-model evaluate \
  --config /absolute/path/to/evaluation.yaml \
  --model /absolute/path/to/model \
  --adapter /absolute/path/to/adapter \
  --dataset /absolute/path/to/prepared-dataset \
  --split test \
  --output /absolute/path/to/test-evaluation

uv run --no-sync foliqant-model audit \
  --evaluation /absolute/path/to/test-evaluation \
  --policy /absolute/path/to/policy \
  --output /absolute/path/to/audit
```

Audit reports coverage, accepted cases, errors and a one-sided 95% exact binomial
upper error bound. Status is `insufficient`, `meets-bound` or `fails-bound`.
Changing the model, adapter, tokenizer, generation or scoring configuration
requires a new calibration and audit.

A diagnostic dataset can exercise the complete process, but it cannot establish
production readiness. Undeclared related cases and future distribution changes
remain outside this statistical statement.

Next: [export and run](export-and-run.md).
