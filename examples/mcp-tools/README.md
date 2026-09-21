# Local stdio MCP workflow example

Prepare the locked service environment with the production MCP extra, then run
the example from the repository root:

```bash
uv sync --project service --locked --no-dev --extra mcp
uv run --project service --no-sync python examples/mcp-tools/run.py
```

`run.py` compiles an explicit MCP workflow step, starts `server.py` as a local
stdio subprocess through the maintained MCP SDK, verifies its discovered schema
against the declared catalog, calls the read-only synthetic `lookup` tool, and
prints the strict execution result as JSON. It uses the active service Python
interpreter and the resolved bundled server path as trusted startup settings; no
endpoint or shell command comes from the request.

The domain-qualified metadata key demonstrates explicit forwarding of the
trusted synthetic tenant and principal. The server returns that metadata in the
structured demo result so the behavior is visible, but it never writes identity
values to logs. `DemoAllowAuthorizer` permits the one bundled tool only to show
where host authorization belongs. It performs no authentication and must not be
used as production authorization.

This is a bounded, nondurable local example. It performs no model inference and
contacts no network endpoint. It does not demonstrate production durability,
credential management, reconciliation, or distributed admission.
