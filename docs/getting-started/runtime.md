# Install and run the Python package

Use this path when you want to execute workflows in an application. Model
training has a separate environment described in [model setup](setup.md).

## Requirements

- CPython 3.12
- [uv](https://docs.astral.sh/uv/)

Install the locked runtime environment from the repository root:

```sh
uv sync --locked --no-dev
```

For a runtime-only installation, select just the adapters you use. For example,
an OpenAI-compatible local model needs the `openai` extra:

```sh
uv sync --locked --no-dev --extra openai
```

Add `--extra mcp` for MCP clients or `--extra telemetry` for OpenTelemetry.
Training dependencies are not part of the runtime project.

## Build and install the package

The project is not claiming a published package index release. Build a wheel
from the reviewed checkout and install that exact artifact into another Python
3.12 environment:

```sh
uv build
uv pip install /absolute/path/to/foliqant/dist/foliqant-0.1.0-py3-none-any.whl
```

In another uv project, record the wheel as a dependency instead:

```sh
uv add /absolute/path/to/foliqant/dist/foliqant-0.1.0-py3-none-any.whl
```

Build artifacts appear under `dist/`. Installing the runtime wheel does not
install the separate model-training project.

## Create the smallest workflow

The initializer creates a model-free workflow, configuration, and sample input:

```sh
uv run --no-sync foliqant init /tmp/foliqant-demo
uv run --no-sync foliqant validate --config /tmp/foliqant-demo/foliqant.yaml
uv run --no-sync foliqant explain \
  --config /tmp/foliqant-demo/foliqant.yaml \
  --workflow demo
uv run --no-sync foliqant run \
  --config /tmp/foliqant-demo/foliqant.yaml \
  --workflow demo \
  --input /tmp/foliqant-demo/envelope.json
```

`validate`, `explain`, and `doctor` are offline. `run` opens configured clients,
awaits the selected workflow, prints one JSON result, and exits. It does not
create a job or save the result.

## Embed the runtime

Compile at startup and keep clients open while the host accepts work:

```python
import asyncio
import os
from pathlib import Path

from foliqant import Envelope, open_application, prepare_application


async def main() -> None:
    config_path = Path("foliqant.yaml")
    prepared = prepare_application(config_path)
    async with open_application(prepared, environment=os.environ) as application:
        result = await application.run(
            "demo",
            Envelope(payload={"message": "hello"}),
        )
        print(result.model_dump_json())


asyncio.run(main())
```

`open_application` reads the configuration directory's `.env` once and overlays
the supplied environment mapping. The CLI uses the same path. Process values
take precedence. `prepare_application` validates offline without reading `.env`,
resolving secrets or constructing clients. Use the public `load_environment`
helper only when explicitly loading an additional environment file location.

The host owns authentication and supplies trusted `Identity` values when needed.
Envelope metadata alone does not authenticate a tenant or principal. Keep the
application context open until active model and tool calls have drained.

Next, follow [workflow authoring](../guides/build-workflows.md) or run the
[support triage](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_triage/README.md)
and [public-request MCP](https://github.com/sebastianwessel/foliqant/blob/main/examples/public_request_mcp/README.md)
examples.
