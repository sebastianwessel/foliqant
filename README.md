# Foliqant

Foliqant is a Python library for typed, in-memory workflows around models, MCP,
and application code. A workflow connects explicit flows; each flow runs a
reviewed sequence of operations and returns one result to the caller. Inputs,
bindings, schemas, routes, and external capabilities are validated before use.

The separate `model/` project prepares data and develops model artifacts. The
runtime package does not train, download, discover, or serve models.

## Documentation

| Goal | Start here |
| --- | --- |
| Add a workflow to an application | [Runtime setup](docs/getting-started/runtime.md) |
| Author flows, operations, bindings, and prompts | [Build a workflow](docs/guides/build-workflows.md) |
| Configure models, MCP, limits, or telemetry | [Runtime configuration](docs/reference/runtime-configuration.md) |
| Measure pipelines, flows, and operations against gold | [Workflow evaluation](docs/guides/testing-and-evaluation.md) |
| Prepare or review training data | [Data workflows](docs/data.md) |
| Train, evaluate, or export a model | [Model development](docs/model-development.md) |

Browse the [documentation overview](docs/index.md) for all guides.

## Install

Foliqant requires CPython 3.12 and [uv](https://docs.astral.sh/uv/). This
repository does not claim a package-index release.

```sh
git clone https://github.com/sebastianwessel/foliqant.git
cd foliqant
uv sync --locked --no-dev --extra openai
```

## Quick start

Create a small local-model project:

```sh
uv run --no-sync foliqant init /tmp/foliqant-demo
cp /tmp/foliqant-demo/config/.env.example /tmp/foliqant-demo/config/.env
```

Set `MODEL_ID` and `MODEL_BASE_URL` in `config/.env`, then validate and run:

```sh
uv run --no-sync foliqant validate \
  --config /tmp/foliqant-demo/config/settings.yaml
uv run --no-sync foliqant run \
  --config /tmp/foliqant-demo/config/settings.yaml \
  --workflow demo \
  --input /tmp/foliqant-demo/envelope.json
```

Installed commands run from a workflow project use `config/settings.yaml` by
default. `validate`, `explain`, and `doctor` are offline; `run` contacts
only the model and tool endpoints explicitly configured by the project.

## Examples

- [Decision evidence](examples/decision_evidence/README.md): reasons, support strength, and focused gold.
- [Support triage](examples/support_triage/README.md): a decision followed by structured extraction.
- [Public-request lookup](examples/public_request_mcp/README.md): a read-only MCP operation without a model.
- [Extraction to MCP](examples/extracted_request_mcp/README.md): selected model output passed to a tool.
- [Prompt security](examples/security_evaluation/README.md): paired English/German inputs and scoped trust checks.
- [HTTP wrapper](examples/http_workflow/README.md): a thin transport around the in-memory API.

The example evaluators distinguish deterministic wiring checks from opt-in live
model measurements. Neither synthetic cases nor a successful smoke run establish
model quality or security effectiveness.

## Development

Use the [development guide](docs/development.md) for repository checks and the
separate environments. Keep private and generated data outside Git.
