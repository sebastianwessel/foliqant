# Foliqant documentation

Foliqant has two separate parts. Choose the path that matches the work you need
to do.

| Part | Use it for | Execution model |
| --- | --- | --- |
| `foliqant` Python package | Compile and run workflows with model, MCP, and Python steps | In process; each awaited call returns one result |
| `foliqant-model` tool project | Prepare data, train adapters, evaluate, calibrate, and export models | Explicit local commands that create versioned artifacts |

## Run workflows

Start with [install and run](getting-started/runtime.md). It creates a model-free
workflow that needs no endpoint or credentials. Then continue with:

- [Runtime concepts](concepts/runtime-and-model-development.md) for ownership and
  process boundaries.
- [Workflow authoring](guides/build-workflows.md) for bundles, steps, bindings,
  and offline compilation.
- [Runtime configuration](reference/runtime-configuration.md) for model and MCP
  profiles, execution limits, telemetry, environment values, and CLI behavior.
- [Testing and evaluation](guides/testing-and-evaluation.md) for local fakes,
  JSON golden datasets, offline checks, saved-result replay, and live evaluation.
- [Evaluation results](guides/evaluation-results.md) for interpreting metrics,
  comparing observations, and diagnosing errors without tuning to the test set.

Runnable examples cover
[support triage](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_triage/README.md),
[a read-only MCP lookup](https://github.com/sebastianwessel/foliqant/blob/main/examples/public_request_mcp/README.md),
[extraction to MCP](https://github.com/sebastianwessel/foliqant/blob/main/examples/extracted_request_mcp/README.md),
and [a thin HTTP wrapper](https://github.com/sebastianwessel/foliqant/blob/main/examples/http_workflow/README.md).

## Develop models and data

Use [model development](model-development.md) for the full local lifecycle or
[data workflows](data.md) when your task is source selection, curation,
preparation, permissions, or leakage-safe partitions.

The smallest native model exercise begins with [local model setup](getting-started/setup.md)
and continues to [the first adapter](getting-started/first-model.md). Setup
downloads pinned assets and requires native Apple Silicon macOS for MLX training.
It does not train a model or start a server.

## Operate safely

- [Artifact operations](operations/artifacts.md) explains verification, immutable
  outputs, interruption recovery, and exit codes.
- [Model configuration](reference/configuration.md) lists strict curation,
  dataset, training, and evaluation settings.
- [Data licenses](guides/data-licenses.md) explains the permission declarations
  retained through model ancestry.

Contributors should use the [development guide](development.md) for the separate
uv environments and repository checks.
