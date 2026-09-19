# Serving artifact export

Use `quantize` for supported 4/8-bit MLX models, `merge` to fuse an adapter with its exact parent, and `export` for checkpoint or supported F16 GGUF output. Independent runtime loading is separate release evidence; export does not claim universal compatibility.

Read the [step-by-step guide](../../docs/guides/export-and-run.md). The implementation
is in `model/src/foliqant_model`; keep downloaded data, weights, adapters,
predictions and generated artifacts outside Git.

The setup exercise uses a small diagnostic checkpoint. No production financial
model or quality guarantee is implied by a successful toolchain run.
