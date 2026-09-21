# Public-request lookup over MCP

This example looks up a synthetic public-record request through a real local
stdio MCP session. The workflow declares one read-only tool, validates its
discovered schema, authorizes it at the host boundary, and returns structured
status data. It makes no model or network call.

Install the MCP adapter and run the example:

```sh
uv sync --locked --extra mcp
uv run --no-sync python -m examples.public_request_mcp.run
```

The script starts the bundled server with the active Python interpreter and
passes a fixed request reference through the compiled workflow. The expected
payload contains the reference, `in_review` status, due date, and assigned team.

The example's `ExampleAuthorizer` permits only this reviewed tool. It shows where
an embedding application enforces business authorization; it does not
authenticate a caller. Production MCP credentials, transport selection, and
resource-specific permissions remain host responsibilities.
