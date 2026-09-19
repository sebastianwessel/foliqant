# Export and run a merged model

Serving runtimes usually need model weights with the adapter already fused.
Merge creates that dense model. Export then packages the merged artifact as an
ordinary checkpoint or a supported GGUF file.

Foliqant leaves export compatibility `unverified`. A compatibility claim needs
separate evidence from the intended runtime; conversion success is not enough.

## Optional: quantize before training

Quantization can reduce memory for QLoRA training. It accepts an upstream
checkpoint or a merged model and preserves its shared or customer scope:

```sh
uv run --no-sync foliqant-model quantize \
  --model /absolute/path/to/checkpoint-or-merged-model \
  --bits 4 \
  --group-size 64 \
  --output /absolute/path/to/new-quantized-model
```

`--bits` is `4` or `8`; group size is fixed at `64`. The default timeout is
3600 seconds. Use `--timeout-seconds N` when the local conversion needs a longer
bounded deadline.

Quantization is lossy and cannot be reversed into the original precision. A
later merge can dequantize for fusion, but that does not recover information
discarded by quantization.

## Merge an adapter

The adapter must name the supplied model as its exact training parent:

```sh
uv run --no-sync foliqant-model merge \
  --model /absolute/path/to/exact-model-parent \
  --adapter /absolute/path/to/adapter \
  --output /absolute/path/to/new-merged-model
```

The merged model takes the adapter's shared or customer scope. Its manifest
retains both parent identities, source rights, exposure indexes and precision
history. Inputs remain unchanged.

To create a customer adaptation, first merge the shared adapter. Use that shared
merged artifact as the model for `customize`. Merge the resulting customer
adapter with the same shared model parent to produce a customer-scoped model.

## Export a checkpoint

A checkpoint export copies a verified merged Safetensors model and its tokenizer,
configuration and chat template into a new immutable artifact:

```sh
uv run --no-sync foliqant-model export \
  --model /absolute/path/to/merged-model \
  --format checkpoint \
  --output /absolute/path/to/checkpoint-export
```

The export is intended for ordinary checkpoint-based runtimes, but its manifest
records compatibility with `status: unverified` (the CLI returns
`compatibilityStatus: unverified`). Test the exact artifact in the
runtime and hardware you plan to use before release.

## Export GGUF

GGUF export currently supports merged `llama`, `mistral` and `mixtral`
architectures. It emits F16 weights:

```sh
uv run --no-sync foliqant-model export \
  --model /absolute/path/to/merged-model \
  --format gguf \
  --output /absolute/path/to/gguf-export
```

Unsupported architectures fail before conversion. Foliqant parses the generated
GGUF metadata, but this still does not prove that a particular llama.cpp build or
other engine can run it.

## Verify and perform a runtime smoke test

Verify the artifact first:

```sh
uv run --no-sync foliqant-model verify /absolute/path/to/export
```

Foliqant does not start a serving engine. Run a small deterministic generation in
your chosen independent runtime with local files only. Record the runtime name
and version, executable identity, exact export artifact ID, bounded command and
result. Keep this release evidence separate from the immutable export manifest.

From the repository root, smoke-test a checkpoint on CPU with the pinned
independent runtime. Install the locked validation dependencies first; this does
not download model weights. Do not synchronize dependencies during a running
training job. The output path must not exist:

```sh
uv sync --locked --extra mlx --extra validation --group dev
PYTHONPATH=model/src .venv/bin/python scripts/verify_checkpoint_runtime.py \
  --artifact /absolute/path/to/checkpoint-export \
  --output /absolute/private/release-evidence/checkpoint-smoke.json \
  --timeout-seconds 300
```

Smoke-test a GGUF export with a local llama.cpp executable. Omit
`--llama-executable` when `llama-cli` is already on `PATH`:

```sh
PYTHONPATH=model/src .venv/bin/python scripts/verify_gguf_runtime.py \
  --artifact /absolute/path/to/gguf-export \
  --output /absolute/private/release-evidence/gguf-smoke.json \
  --llama-executable /absolute/path/to/llama-cli \
  --timeout-seconds 60
```

Both scripts verify the artifact inventory before use and again after the
runtime exits. They use fixed tiny prompts and token bounds, enforce a process
deadline, run without remote model code or model downloads, and write a private
canonical JSON report with an evidence ID. The GGUF invocation includes
`--single-turn`, so interactive llama.cpp builds terminate after the response.
The scripts never overwrite an evidence file.

Do not claim that one smoke response proves answer quality. Use the evaluation,
calibration and audit process for measured behavior, and keep legal and source
license review separate from technical compatibility.
