# 4. Look up an account and draft a reply

Use a direct `mcp` step when policy already knows which tool to call. Here
extraction supplies an account reference, a trusted guard checks that one is
present, and a local read-only MCP server returns synthetic account facts.

First, add the `mcp.account_records` transport and reviewed `lookup_account`
catalog to `my_support/config/settings.yaml`. Copy the complete
[`mcp` block](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/settings.yaml) because
its input and output schemas must match the server's discovered schemas
exactly. Set `supports_tools: true` only if you will also try chapter 5; the
direct call itself does not ask the model to choose a tool. Create
`my_support/server.py` from the [small synthetic server](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/server.py),
then replace the copied `transport` with:

```yaml
transport:
  type: stdio
  command: $MCP_PYTHON
  args:
    - -m
    - my_support.server
  cwd: $PROJECT_ROOT
  env:
    PYTHONUNBUFFERED: "1"
```

The host below supplies the Python executable and repository root. Keep the
catalog from the example unchanged: its schemas describe this server's exact
signature. The server exposes one operation, `lookup_account`, with `effect: read`.

Add these steps to each branch `flow.yaml` after `extract`:

```yaml
steps:
  - extract
  - require_reference
  - lookup
  - draft
output:
  fields:
    queue:
      literal: billing
    account_reference:
      pointer: /steps/extract/result/account_reference
    reply:
      pointer: /steps/draft/result/reply
      default: null
```

Use `literal: cancellation` in the cancellation branch. The object output
assembles the host payload `{queue, account_reference, reply}` directly from
the step results; `reply` needs a default because an earlier step may stop the
flow for review.

Create `require_reference.step.yaml` in each branch:

```yaml
type: handler
handler: require_reference
input:
  account_reference:
    pointer: /steps/extract/result/account_reference
```

Declare the handler's contract in `my_support/config/settings.yaml`, copying
the two schema files from the example's
[`contracts/`](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/contracts/require_reference.input.json)
directory:

```yaml
handlers:
  require_reference:
    input_schema: contracts/require_reference.input.json
    output_schema: contracts/require_reference.output.json
    effect: read
```

With the declaration, `foliqant validate` checks the workflow without any
Python. The [example handler](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/handlers.py)
returns `needs_review=True` with the issue `no_supported_answer` when the
extracted reference is null, and projects a non-null string only for a usable
reference. The host registers the callable with `prepare_application`. This
guard is a business policy check, not proof that the model extracted the
correct customer's reference.

Create `lookup.step.yaml` in each branch:

```yaml
type: mcp
server: account_records
tool: lookup_account
arguments:
  account_reference:
    pointer: /steps/require_reference/result/account_reference
```

The runtime validates both the argument and tool result against the reviewed
catalog. The model cannot substitute another tool. An empty reference stops
before lookup; the synthetic gold checks `lookup.status: skipped` in that case.

Next create `draft.step.md` and `draft.schema.json` beside it, using the
[billing draft](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/support_email/billing/draft.step.md)
as the concrete shape. Bind only `/payload/message` and the validated
`/steps/lookup/result`; require a JSON object with one `reply` string. Tell the
model to acknowledge the request without claiming a refund or cancellation
has happened.

The workflow output from chapter 3 (`first_of` over both branch results)
now returns `{queue, account_reference, reply}` from whichever branch ran. When
`classify` needs review, no branch ran and the output default
`{disposition: needs_review}` applies; when `require_reference` stops a branch,
that branch's object output carries `reply: null`. Use the
[final workflow](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/support_email/workflow.yaml)
and [billing flow](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/support_email/billing/flow.yaml)
for exact bindings. The projection makes the host's result useful without
parsing internal step records.

## Open your application with its handlers

Put the [handler implementation and registration](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/handlers.py)
in `my_support/handlers.py`. Create `my_support/run.py` to connect your
configuration, handlers, and client lifecycle:

```python
import asyncio
import os
import sys
from pathlib import Path

from foliqant import Envelope, open_application, prepare_application
from my_support.handlers import HANDLERS


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    prepared = prepare_application(root / "my_support/config/settings.yaml", handlers=HANDLERS)
    environment = {
        **os.environ,
        "MCP_PYTHON": sys.executable,
        "PROJECT_ROOT": str(root),
    }
    async with open_application(prepared, environment=environment) as app:
        result = await app.run(
            "support_email",
            Envelope(
                payload={
                    "message": "Please review the duplicate charge on invoice INV-7 for account A-100."
                }
            ),
        )
        print(result.model_dump_json(indent=2))


asyncio.run(main())
```

After setting `MODEL_ID` and `MODEL_BASE_URL` in `my_support/config/.env`,
run `uv run --no-sync python -m my_support.run` from the repository root.
This calls your configured model and the local synthetic MCP server. Handler
contracts are reviewed configuration; the callables are registered in Python,
and YAML never imports application code.

For an **offline comparison** without a model endpoint, run the finished snapshot:

```sh
uv run --no-sync python -m examples.support_email_tutorial.run
```

Expect `payload.queue: billing`, `payload.account_reference: A-100`, and a
reviewable `payload.reply`. Continue with [retrying a lookup](retry-lookup.md) or
[a model tool loop](model-tool-loop.md).
