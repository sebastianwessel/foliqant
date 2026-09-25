# 3. Route between flows

This example classifies one message, then follows an exact workflow route to a
trusted billing or cancellation handler. The model produces a reviewed category;
the workflow owns the allowed destinations and the handlers own application code.

The important files are:

```text
config/routed_intake/
  workflow.yaml
  classify/
    flow.yaml
    classify.step.md
  billing/
    flow.yaml
    prepare.step.yaml
  cancellation/
    flow.yaml
    prepare.step.yaml
```

`workflow.yaml` matches `/flows/classify/result` exactly. `billing` and
`cancellation` are the only cases, and an unmatched result ends in
`needs_review`. Each branch receives only the original message and calls a
read-only handler. The handlers' contracts are declared under `handlers` in
`config/settings.yaml` (schemas in `config/shared/handler_contracts/`); `handlers.py` only
registers the callables, so `foliqant validate` works without Python.

Run the complete route with local scripted model output:

```sh
uv run --no-sync python -m examples.routed_intake.run
uv run --no-sync python -m examples.routed_intake.evaluate
```

The tracked gold has independent English and German billing and cancellation
requests. It checks the whole route plus the reusable classification flow and
step. These are four business inputs observed at three scopes.

To use the explicitly configured OpenAI-compatible endpoint, set `MODEL_ID` and
`MODEL_BASE_URL`, then opt in:

```sh
uv run --no-sync python -m examples.routed_intake.run --live
uv run --no-sync python -m examples.routed_intake.evaluate --live
```

Continue with the [read-only MCP example](../public_request_mcp/README.md) after
the route boundary and handler registration are clear.
