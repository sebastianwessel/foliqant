# Terms

| Term | Meaning |
|---|---|
| Upstream checkpoint | Pinned third-party model weights and tokenizer used as the starting point. |
| Shared model | A Foliqant adaptation intended for reuse; not a foundation model pretrained from zero. |
| Customer customization | Separately authorized training against one exact shared model release. |
| LoRA | Training a small set of low-rank adapter parameters while keeping parent weights frozen. |
| QLoRA | LoRA training with a quantized frozen parent; merging/dequantizing cannot restore precision lost in quantization. |
| Component | Records connected transitively by declared group keys; the indivisible dataset split unit. |
| Representative | Lowest record ID in a component, selected without model outputs, used as one risk-audit observation. |
| Deployment profile | Hash of exact model/adapter, tokenizer/template, generation and scoring settings and runtime identity. |
| Acceptance policy | Empirical threshold for choosing which validated outputs to accept; not a per-message correctness probability. |
| Artifact | Finalized directory with closed manifest and verified inventory; immutable by convention and verification. |
| Diagnostic | Evidence useful for toolchain checks, without a production financial-quality claim. |
