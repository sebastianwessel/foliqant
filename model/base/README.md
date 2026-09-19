# Shared model development

Train a reusable adaptation from an upstream open-weight checkpoint. This is not pretraining a foundation model from scratch. Use the `train` command with a verified upstream or shared model and data whose sources permit shared training.

Read the [step-by-step guide](../../docs/guides/train-and-customize.md). The implementation
is in `model/src/foliqant_model`; keep downloaded data, weights, adapters,
predictions and generated artifacts outside Git.

To build the bounded diagnostic source corpus without hand-editing records, use
[automated curation](../../docs/guides/automated-curation.md). Curation
preparation, optional local-model generation and training are separate commands.

The setup exercise uses a small diagnostic checkpoint. No production financial
model or quality guarantee is implied by a successful toolchain run.
