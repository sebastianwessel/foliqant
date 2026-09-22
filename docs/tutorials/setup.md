# Set up the support email project

Use Python 3.12 and a source checkout of Foliqant. From the repository root:

```sh
uv sync --locked --extra openai --extra mcp
mkdir -p my_support/config/support_email/classify
```

Create files below `my_support/`. The [final configuration](https://github.com/sebastianwessel/foliqant/tree/main/examples/support_email_tutorial/config/)
is a comparison point. Create `my_support/config/settings.yaml`:

```yaml
models:
  local_qwen:
    provider: openai_compatible
    model: $MODEL_ID
    base_url: $MODEL_BASE_URL
    allow_insecure_http: true
    output_mode: native
    supports_tools: false
execution:
  concurrency: 1
  queue_limit: 0
```

These complete environment references resolve when clients open. `validate`
compiles offline without an endpoint. Set them only when making a live model
request, for example in an untracked `my_support/config/.env`:

```dotenv
MODEL_ID=your-served-qwen-model-id
MODEL_BASE_URL=http://127.0.0.1:8000/v1
```

Match those values to your local server. The finished example uses a scripted
model by default and opens no model connection:

```sh
uv run --no-sync python -m examples.support_email_tutorial.run
```

Expect `execution.status: completed`, `payload.queue: billing`, and
`payload.account_reference: A-100`. The next chapters show the files to write
before reaching that snapshot. Continue with [classification](decision-basics.md).
For installation choices, see [Install and run](../getting-started/runtime.md).
