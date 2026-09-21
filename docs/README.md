# Foliqant documentation

Foliqant has two deliberately separate parts:

| Part | Use it for | Execution model |
| --- | --- | --- |
| `foliqant` Python package | Compile and run workflows around model, MCP, and Python steps | In process; each call returns its result |
| `model/` tool project | Curate data, train adapters, evaluate, calibrate, and export models | Explicit local commands that produce versioned artifacts |

If you want to add Foliqant to an application, start with
[install and run](getting-started/runtime.md), then read
[workflow authoring](guides/build-workflows.md) and
[testing and evaluation](guides/testing-and-evaluation.md). Runtime settings and
CLI commands are listed in [runtime configuration](reference/runtime-configuration.md).

The runnable business examples are:

- [support triage](../examples/support_triage/README.md), including a local Qwen
  workflow and an in-code golden evaluation suite;
- [public-request lookup](../examples/public_request_mcp/README.md), using a
  read-only local MCP tool;
- [HTTP wrapper](../examples/http-workflow/README.md), showing only the inbound
  transport boundary.

For model development, continue with:

1. [Set up the model project](getting-started/setup.md).
2. [Run the first local lifecycle](getting-started/first-model.md).
3. [Prepare data](guides/prepare-data.md) or [curate public sources](guides/automated-curation.md).
4. [Train and customize](guides/train-and-customize.md).
5. [Evaluate and audit](guides/evaluate-and-audit.md).
6. [Export and verify](guides/export-and-run.md).

The [runtime and model-development boundary](concepts/runtime-and-model-development.md)
explains which project owns each job. [Artifact operations](operations/artifacts.md)
and the [model configuration reference](reference/configuration.md) cover the
model lifecycle in more detail.

Agents working in this checkout can use the [runtime skill](skills/foliqant.md)
or the [model operating skill](skills/foliqant-model.md); both point back to
these public guides and the implemented commands.
