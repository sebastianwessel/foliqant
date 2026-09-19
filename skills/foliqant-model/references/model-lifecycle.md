# Model lifecycle operations

Use `foliqant-model <command> --help` as the exact CLI authority. All paths are
explicit. Outputs must not already exist, completed artifacts are immutable, and
commands do not retry through a remote provider.

## Train and customize

`train --config FILE --model DIR --dataset DIR --output DIR` creates a shared
adapter. Every new dataset source must allow shared training. `customize` adds
required `--customer ID` and requires a shared merged model, or its directly
derived quantized artifact. Add `--warm-start DIR` only for a completed adapter
with the exact model, scope, customer, rank, scale, dropout, layer count and
resolved target tensors.

Training receives only train and validation partitions. It masks prompt loss,
rejects hidden truncation, requires `steps` divisible by `gradientAccumulation`,
and checks both partitions against `batchSize`. A successful adapter retains
actual loss, validation observations, duration, memory, tensor names, parent
snapshots, source rights and conservative exposure ancestry. Warm start reloads
weights with a new optimizer and PRNG; it is additional training rather than an
optimizer-state continuation.

## Evaluate, calibrate and audit

`evaluate` needs config, model, dataset, split and output; `--adapter` is
optional. Use validation during development. Calibration and test reject exact
overlap with recorded training ancestry. Never substitute the reference answer
for failed generation.

Scoring supports plain exact text and strict JSON structural equality. Optional
local JSON Schema, evidence pointer and field pointers make JSON required. An
evidence quote must occur exactly in an input message. No LLM judge is used.

`calibrate --evaluation DIR --max-error FLOAT --min-accepted INT --output DIR`
uses a calibration evaluation and one deterministic representative per dataset
component. Its mean generated-token log probability is a ranking score, not a
correctness probability. No qualifying threshold produces an abstain-all policy.

`audit --evaluation DIR --policy DIR --output DIR` needs a disjoint test
evaluation with the identical deployment profile and dataset identity. It fixes
the policy before test outcomes, then records coverage and a one-sided 95% exact
binomial upper error bound. Synthetic, teacher-produced or unreviewed diagnostic
data cannot establish production readiness.

## Transform and export

Quantization accepts a verified checkpoint or merged artifact and preserves its
scope and exposure ancestry. Merge requires an adapter and its exact model parent;
quantized fusion records dequantization as lossy. Export accepts only a merged
artifact and produces a checkpoint or supported GGUF artifact.

An export manifest records runtime compatibility as `unverified`. Do not change
that claim because conversion succeeded. Independent loading and bounded
generation in the intended runtime are separate release evidence.

## Rights and integrity

Every descendant retains the exact union of source rights, including
`commercialUse`, attribution and restrictions. Do not reinterpret research or
non-commercial data as commercially cleared. Preserve all leakage indexes through
quantization, merge and export.

Run `verify DIR` before consuming or handing off an artifact. Verification checks
inventory hashes, safe paths, immutable identities and recursive lineage. It
proves recorded integrity, not model quality or legal approval.
