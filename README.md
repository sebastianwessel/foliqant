# Foliqant

Foliqant is a Python library for typed, deterministic workflows around model,
MCP, and application code. It compiles versioned workflow bundles, validates
inputs and structured outputs, and returns one in-memory result to the caller.
A separate local toolchain prepares data and develops model artifacts.

## Contents

- [Documentation](#documentation)
- [Install](#install)
- [Quick start](#quick-start)
- [Examples](#examples)
- [Development](#development)

## Documentation

| Goal | Start here |
| --- | --- |
| Add a workflow to an application | [Runtime setup](docs/getting-started/runtime.md) |
| Configure models, MCP, limits, or telemetry | [Runtime configuration](docs/reference/runtime-configuration.md) |
| Prepare or review training data | [Data workflows](docs/data.md) |
| Train, evaluate, or export a model | [Model development](docs/model-development.md) |

Browse the [documentation overview](docs/index.md) for all guides.

## Install

Foliqant requires CPython 3.12 and [uv](https://docs.astral.sh/uv/). This
repository does not claim a package-index release.

```sh
git clone https://github.com/sebastianwessel/foliqant.git
cd foliqant
uv sync --locked --no-dev
```

See [runtime installation](docs/getting-started/runtime.md) to build a wheel or
select optional adapters.

## Quick start

Create and run a model-free workflow with no credentials or endpoint:

```sh
uv run --no-sync foliqant init /tmp/foliqant-demo
uv run --no-sync foliqant validate \
  --config /tmp/foliqant-demo/foliqant.yaml
uv run --no-sync foliqant run \
  --config /tmp/foliqant-demo/foliqant.yaml \
  --workflow demo \
  --input /tmp/foliqant-demo/envelope.json
```

The final command prints one JSON result with execution status `completed`.
Continue with [workflow authoring](docs/guides/build-workflows.md).

## Examples

- [Support triage](examples/support_triage/README.md): decisions and extraction with a local model.
- [Public-request lookup](examples/public_request_mcp/README.md): read-only MCP without a model.
- [HTTP wrapper](examples/http-workflow/README.md): thin transport around the in-memory API.

## Development

Use the [development guide](docs/development.md) for repository checks and the
separate environments. Keep private and generated data outside Git.
