# Shared model development

Build the reusable Foliqant model by adapting a selected upstream open-weight checkpoint. This is not pretraining a foundation model from scratch.

Future contents: source/model manifests, rights-reviewed dataset recipes, shared adaptation configuration, and reproducible training entry points. Record exact parent revision, tokenizer/chat template, training data versions, and evaluation evidence. Keep weights and raw datasets outside Git.

No model is selected and no training recipe is implemented. See [research](../../docs/research-and-concept.md). Customer-specific adapters belong in `model/customization/`.

See [local training and model lineage](../../docs/local-training-and-model-lineage.md) for Apple Silicon and sequential adaptation guidance.
