# Model development

This separate uv project provides `foliqant-model` for data preparation, shared
adaptation, customer fine-tuning, evaluation and export. It is not required to
use the runtime package or call an existing local model endpoint.

From the repository root:

```sh
uv sync --project model --locked --group dev
uv run --project model --no-sync foliqant-model --help
uv run --project model --no-sync pytest -c model/pyproject.toml model/tests
```

Start with [model setup](../docs/getting-started/setup.md), then follow the guides:

- [Prepare data](../docs/guides/prepare-data.md) and
  [generate native decision data](../docs/guides/native-decision-data.md).
- [Train and customize](../docs/guides/train-and-customize.md).
- [Evaluate, calibrate and audit](../docs/guides/evaluate-and-audit.md).
- [Export and run](../docs/guides/export-and-run.md).

Implementation lives in `src/foliqant_model`; recipes live in `examples`.
Decision contracts are imported from `foliqant.decisions` in the root package.
Downloaded/generated datasets, weights, adapters and reports stay outside Git.
Shared adaptation fine-tunes an upstream checkpoint; it is not foundation-model
pretraining. Tiny synthetic/toolchain checks do not establish production quality.
