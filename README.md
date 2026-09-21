# Foliqant

Foliqant is a reusable Python package for evidence-backed model decisions and
deterministic workflows. It compiles versioned workflow bundles, validates
inputs and structured outputs, calls explicitly configured model or MCP
adapters, and returns one in-memory execution result to the caller.

The package does not provide an HTTP server, authentication, persistence,
background jobs, or restart recovery. Applications add those boundaries around
the Python API when they need them.

## Install and run

Foliqant requires CPython 3.12 and [uv](https://docs.astral.sh/uv/). From this
checkout:

```sh
uv sync --locked --all-extras --group dev
uv run --no-sync foliqant init /tmp/foliqant-demo
uv run --no-sync foliqant run \
  --config /tmp/foliqant-demo/foliqant.yaml \
  --workflow demo \
  --input /tmp/foliqant-demo/envelope.json
```

The generated workflow is model-free. It is the fastest way to verify the
compiler, CLI, and execution result locally.

To embed a configured workflow:

```python
import asyncio
from pathlib import Path

from foliqant import open_application, prepare_application
from foliqant.contracts.envelope import Envelope


async def main() -> None:
    prepared = prepare_application(Path("foliqant.yaml"))
    async with open_application(prepared, environment={}) as application:
        result = await application.run("demo", Envelope(payload={"message": "hello"}))
        print(result.execution.status)


asyncio.run(main())
```

## Examples

- [Support triage with local Qwen](examples/support_triage/README.md) classifies
  a synthetic email and extracts its deadline and account reference. Live calls
  require an explicit flag and the exact model settings in root `.env`.
- [Public-request lookup over MCP](examples/public_request_mcp/README.md) runs a
  useful read-only workflow against a bundled local stdio server without a model.
- [Thin HTTP wrapper](examples/http-workflow/README.md) shows how an application
  can expose the in-memory API. It is example transport code.

## Runtime and model development

The root uv project builds the `foliqant` runtime package. The separate
`model/` uv project contains data curation, training, evaluation, calibration,
and export tools for developing a model. Runtime applications do not need the
training stack, and training does not deploy the workflow runtime.

Start with the [user documentation](docs/README.md). Model developers can go
directly to [model setup](docs/getting-started/setup.md) and the
[model lifecycle guides](docs/guides/train-and-customize.md).

## Development

```sh
uv run --no-sync pytest
uv run --no-sync mypy src
uv run --no-sync ruff check src tests examples
uv run --no-sync python scripts/generate_schemas.py --check
uv build
```

Model-tooling checks run in their own project:

```sh
uv run --project model --no-sync pytest -c model/pyproject.toml model/tests
uv run --project model --no-sync mypy --config-file model/pyproject.toml model/src
uv run --project model --no-sync ruff check --config model/pyproject.toml model/src model/tests
```

Model weights, customer data, credentials, generated datasets, checkpoints, and
evaluation holdouts do not belong in Git.
