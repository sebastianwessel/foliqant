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

The deployment’s reviewed catalog and the step’s declared tool bound access.
The default host policy permits only declared read tools; it does not authenticate
a caller or establish resource-specific business permission. An application can
inject a `RuntimePlugins.tool_authorizer` for those rules. Production credentials
and caller authentication remain host responsibilities.

`workflow.yaml` demonstrates inline steps and an inline input schema. Its
`foliqant.yaml` uses environment references for the Python executable and working
directory, which `run.py` supplies explicitly. It calls `prepare_application` and
`open_application`, with no second runner or model configuration parser.

Evaluate the full workflow and isolated lookup against explicit expectations:

```sh
uv run --no-sync python -m examples.public_request_mcp.evaluate
```

This uses the real bundled stdio server and no model/network calls. The two
synthetic cases check returned reference, status, due date, team and operation
counts. A failed assertion gives a nonzero exit. The fixed example records do
not establish correctness for a production records-office system.

Each evaluation saves a new private report by default and prints its path.
Use `--output` to select another new path, or `--repeat 3` to perform three
independent lookups per case. Repetitions remain grouped by source case and do
not increase the number of distinct synthetic requests.

Export the same cases as a reusable dataset, or choose a report destination:

```sh
uv run --no-sync python -m examples.public_request_mcp.evaluate \
  --write-dataset .foliqant/evaluation/public-request-mcp.json
uv run --no-sync foliqant evaluate --config examples/public_request_mcp/foliqant.yaml --check
uv run --no-sync python -m examples.public_request_mcp.evaluate \
  --output .foliqant/evaluation/public-request-report.json
```

Dataset export and `--check` do not start the tool server. The report command
executes the local server and records full inputs, gold and returned results in
the private artifact. Console output omits those details. The isolated report
identifies the `lookup` step. Export paths must be new; keep these generated files
under ignored `.foliqant/` or outside the checkout.
