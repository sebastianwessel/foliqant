# Develop a model

The `foliqant-model` command prepares and qualifies local model artifacts. It is
separate from the workflow runtime: it does not deploy an endpoint, expose an
HTTP service, or add application authentication.

Model development follows an explicit, local artifact chain:

1. Acquire a pinned model and authorized data.
2. Prepare a partitioned dataset.
3. Train an adapter.
4. Evaluate on held-out records, calibrate a policy, and audit it separately.
5. Merge or quantize the verified weights.
6. Export the result and test it in the intended inference runtime.

## Start with the small exercise

1. [Set up the model workspace](getting-started/setup.md). The command installs
   the isolated environment and downloads pinned diagnostic assets; it does not
   begin training.
2. [Train and export the first adapter](getting-started/first-model.md). The
   two-step recipe verifies the native toolchain and does not claim model quality.
3. Read [model lifecycle concepts](getting-started/concepts.md) before choosing
   a larger checkpoint, longer run, or deployment target.

Native training uses MLX on Apple Silicon macOS. Model and data outputs default
to an ignored workspace inside the repository. Every completed artifact is immutable,
content-addressed, and linked to its verified parents.

## Follow the lifecycle

| Task | Guide |
| --- | --- |
| Check capabilities or fetch another pinned checkpoint | [Check and fetch](guides/check-and-fetch.md) |
| Prepare authorized JSONL records | [Prepare data](guides/prepare-data.md) |
| Build data from pinned public sources | [Automated curation](guides/automated-curation.md) |
| Train a shared or customer adapter | [Train and customize](guides/train-and-customize.md) |
| Select a threshold and test it on a separate split | [Evaluate and audit](guides/evaluate-and-audit.md) |
| Merge, quantize, export, and test another runtime | [Export and run](guides/export-and-run.md) |
| Verify or recover an artifact | [Artifact operations](operations/artifacts.md) |

Use the [model configuration reference](reference/configuration.md) for exact
fields and bounds. A successful command proves that its operation completed and
its artifact passed the implemented checks. It does not certify accuracy,
commercial clearance, or suitability for a financial decision.

For data lineage and permissions, continue with [data workflows](data.md).
