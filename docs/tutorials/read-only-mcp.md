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
  - assemble
output:
  pointer: /steps/assemble/result
  optional: true
  default: null
```

Create `require_reference.step.yaml` in each branch:

```yaml
type: handler
handler: require_reference
input:
  account_reference:
    pointer: /steps/extract/result/account_reference
```

Register `require_reference` in your host with frozen input/output schemas;
the [example handler](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/handlers.py) returns
`needs_review=True` when the extracted reference is null. It projects a
non-null string only for a usable reference. `prepare_application` receives
that registration before compilation. This guard is a business policy check,
not proof that the model extracted the correct customer's reference.

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

The final snapshot adds `assemble.step.yaml` to pair the draft with the trusted
route and guarded reference, and routes either branch to `finalize`. Its
[`select_reply` handler](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/handlers.py)
projects `{queue, account_reference, reply}` to the workflow payload. On a
review route, the configured output default is `{disposition: needs_review}`.
Use the [final workflow](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/support_email/workflow.yaml)
and [finalize flow](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/support_email/finalize/flow.yaml)
for exact bindings. This last projection makes the host's result useful
without parsing internal step records.

## Open your application with its handlers

Put the [handler implementations and registrations](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/handlers.py)
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
registrations belong in Python; YAML never imports arbitrary application code.

For an **offline comparison** without a model endpoint, run the finished snapshot:

```sh
uv run --no-sync python -m examples.support_email_tutorial.run
```

Expect `payload.queue: billing`, `payload.account_reference: A-100`, and a
reviewable `payload.reply`. Continue with [a model tool loop](model-tool-loop.md).
