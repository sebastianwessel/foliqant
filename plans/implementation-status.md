# Model lifecycle implementation status

The local model-tooling scope in `specs/00-vision.md` is implemented. Final
verification is recorded in [the acceptance report](reviews/final-verification.md).

- All 14 public CLI commands are implemented with strict typed contracts.
- One-command setup acquires pinned real assets outside Git and supports offline reuse.
- Shared adaptation, warm starts, customer LoRA/QLoRA, lineage, rights, held-out
  evaluation, calibration/audit, merge, checkpoint/GGUF export and verification work.
- The full public CLI lifecycle passed with actual native model execution.
- Exported shared/customer models ran in Transformers and llama.cpp.
- End-user guides, configuration reference, runnable recipes and operating skill
  are aligned with implementation. Internal research and reviews remain separate.
- Reviews and cleanup repaired concrete safety, provenance and semantic findings.
- 187 offline tests and 7 native integration tests pass; strict types, lint,
  schema/docs/skill audits and isolated built-wheel acceptance pass.

No production financial checkpoint is selected. The tiny setup model exercises
the toolchain and establishes no financial quality or compliance guarantee.
The Go workflow service remains a separate proposal. No commits, pushes, uploads,
paid compute or commercial license clearance are part of this completion.

The reproducible native acceptance uses a completed `smoke-v1-2638284b4ec9`
setup via `FOLIQANT_TEST_SETUP`, and its `downloads/model` directory via
`FOLIQANT_TEST_MODEL`. See contributor guidance for the exact test command.
Do not synchronize dependencies while training or validation is running.
