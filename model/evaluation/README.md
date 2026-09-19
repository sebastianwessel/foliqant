# Evaluation and acceptance policies

Use `evaluate` for actual held-out generation, `calibrate` to select an empirical threshold on calibration data, and `audit` to assess the fixed policy on a separate test split. A generated-token likelihood score is not a probability of correctness.

Read the [step-by-step guide](../../docs/guides/evaluate-and-audit.md). The implementation
is in `model/src/foliqant_model`; keep downloaded data, weights, adapters,
predictions and generated artifacts outside Git.

Automated curation publishes `synthetic-regression` separately from source and
augmented training corpora. Treat it as a diagnostic stress population, not as
representative production validation or human-reviewed evidence.

The setup exercise uses a small diagnostic checkpoint. No production financial
model or quality guarantee is implied by a successful toolchain run.
