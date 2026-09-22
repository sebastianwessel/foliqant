# Install and run the Python package

Use this path to create a workflow project, compile it offline, run one
envelope, and embed the runtime in an application.

## Install the runtime

Foliqant supports CPython 3.12. These source-checkout instructions use
[uv](https://docs.astral.sh/uv/). Install the locked runtime environment from
the repository root:

```sh
uv sync --locked --no-dev
```

Select the adapters you need. An OpenAI-compatible local model uses the `openai`
extra; MCP and OpenTelemetry use `mcp` and `telemetry`.

```sh
uv sync --locked --no-dev --extra openai --extra mcp
```

The repository does not claim a package-index release. To install a reviewed
artifact elsewhere, build and install its wheel:

```sh
uv build
uv pip install /absolute/path/to/foliqant/dist/foliqant-0.1.0-py3-none-any.whl
```

## Create a project

The initializer creates `config/settings.yaml`, one workflow and flow, an LLM
operation, a sample envelope, and `config/.env.example`:

```sh
uv run --no-sync foliqant init /tmp/foliqant-demo
cp /tmp/foliqant-demo/config/.env.example /tmp/foliqant-demo/config/.env
```

Set the model served by your local OpenAI-compatible endpoint:

```dotenv
MODEL_ID=your-served-model-id
MODEL_BASE_URL=http://127.0.0.1:8000/v1
```

Then run:

```sh
uv run --no-sync foliqant validate \
  --config /tmp/foliqant-demo/config/settings.yaml
uv run --no-sync foliqant explain \
  --config /tmp/foliqant-demo/config/settings.yaml \
  --workflow demo
uv run --no-sync foliqant run \
  --config /tmp/foliqant-demo/config/settings.yaml \
  --workflow demo \
  --input /tmp/foliqant-demo/envelope.json
```

An installed command run from the generated directory can omit `--config`; the
default is `config/settings.yaml`. `--config PATH` selects any explicit
alternative. `validate`, `explain`, and `doctor` compile offline. `run`
opens configured clients, awaits one workflow, prints one JSON result, and exits.
It does not create a background job or persist the result.

## Embed the runtime

Compile at startup and keep clients open while the host accepts work:

```python
import asyncio
import os
from pathlib import Path

from foliqant import Envelope, open_application, prepare_application


async def main() -> None:
    prepared = prepare_application(Path("config/settings.yaml"))
    async with open_application(prepared, environment=os.environ) as application:
        result = await application.run(
            "demo",
            Envelope(payload={"message": "Please send my statement."}),
        )
        print(result.model_dump_json())


asyncio.run(main())
```

See [Inputs, results, and errors](../reference/inputs-and-results.md) for the
`Envelope` you supply and the `ExecutionResult` returned by `run`, including
decision reasons, evidence strength, and failure handling.

Use `application.run_flow(workflow, flow, envelope)` to execute one flow with
already-resolved flow input, or
`application.run_step(workflow, flow, step, envelope)` to execute one operation
with already-resolved operation input. These scoped methods are useful for tests
and evaluation; normal business execution should call `run` so workflow routing
and upstream context are exercised.

`open_application` reads the selected configuration directory's `.env` once and
overlays the supplied environment mapping. Process values take precedence.
`prepare_application` validates offline without reading `.env`, resolving
secrets, or constructing clients. Use `load_environment` only when explicitly
loading an additional location.

The host owns authentication and supplies trusted `Identity` values when needed.
Envelope metadata alone does not authenticate a tenant or principal. Keep the
application context open until active model and tool calls have drained.

Continue with [workflow authoring](../guides/build-workflows.md) or the
[support triage](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_triage/README.md)
and [public-request MCP](https://github.com/sebastianwessel/foliqant/blob/main/examples/public_request_mcp/README.md)
examples.
