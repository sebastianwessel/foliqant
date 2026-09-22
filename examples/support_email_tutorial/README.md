# Support email tutorial snapshot

This is the completed configuration built in the [support email tutorial](../../docs/tutorials/index.md).
All records and evaluation messages are synthetic. The `support_email`
workflow classifies billing versus cancellation, extracts an account reference,
guards the direct read-only lookup, and produces a draft for a human agent.
`agent_reply` is a separate bounded model tool-loop comparison. `support_multi`
prepares independent support requests through a bounded collection.

From the repository root, install the locked extras and run the scripted path:

```sh
uv sync --locked --extra openai --extra mcp
uv run --no-sync python -m examples.support_email_tutorial.run
uv run --no-sync python -m examples.support_email_tutorial.run --agent
uv run --no-sync python -m examples.support_email_tutorial.run --multi
uv run --no-sync python -m examples.support_email_tutorial.evaluate
```

The default commands use a `FunctionModel` and a real local stdio MCP server;
they make no model-endpoint request. Expect a completed direct result with
`payload.queue: billing`, `payload.account_reference: A-100`, and a `reply`.
The agent path makes two scripted model turns and one tool call. The multi
path makes two ordered read-only calls for its demo email. Evaluation checks
eight synthetic cases and writes a private report under
`.foliqant/evaluations/`.

To use a local OpenAI-compatible model instead, set `MODEL_ID` and
`MODEL_BASE_URL` to your served model and endpoint, then add `--live` to a run
or evaluation command. Those commands call the configured endpoint; the
scripted checks do not measure its quality.

`config/support_email/workflow.yaml` owns exact routing and output projection.
`handlers.py` registers the trusted reference guard and final projection.
`server.py` holds only synthetic records. `evaluation/dataset.json` contains
authored expectations, separate from `offline.py` response fixtures.
