# Customer customization

Create a separate customer adapter from a shared merged model with `customize`. Keep each customer dataset and its derived artifacts separate. An adapter records its exact parent, customer identifier and source permissions; customer ancestry cannot enter shared training.

Read the [step-by-step guide](../../docs/guides/train-and-customize.md). The implementation
is in `model/src/foliqant_model`; keep downloaded data, weights, adapters,
predictions and generated artifacts outside Git.

The setup exercise uses a small diagnostic checkpoint. No production financial
model or quality guarantee is implied by a successful toolchain run.
