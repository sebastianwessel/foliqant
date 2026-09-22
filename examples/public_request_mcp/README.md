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

[config/public_request_lookup/workflow.yaml](config/public_request_lookup/workflow.yaml) references the
`lookup` flow with ordered step ID `lookup`, resolved as `lookup.step.yaml`,
and a colocated input schema.
[config/settings.yaml](config/settings.yaml) uses environment references for the Python executable and working
directory, which `run.py` supplies explicitly. It calls `prepare_application` and
`open_application`, with no second runner or model configuration parser.

Evaluate the full workflow, isolated `lookup` flow and isolated lookup step against explicit expectations:

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

Check the committed JSON dataset or choose a private report destination:

```sh
uv run --no-sync foliqant evaluate --config examples/public_request_mcp/config/settings.yaml --check
uv run --no-sync python -m examples.public_request_mcp.evaluate \
  --output .foliqant/evaluation/public-request-report.json
```

`--check` does not start the tool server. The report command
executes the local server and records full inputs, gold and returned results in
the private artifact. Console output omits those details. The isolated report
identifies flow `lookup` and step `lookup`. Report paths must be new; keep these generated files
under ignored `.foliqant/` or outside the checkout.

`settings.yaml` omits the workflow registry: immediate configuration subfolders
containing `workflow.yaml` are discovered by folder name. Flow definitions resolve
to `<flow-id>/flow.yaml`; the authored step list still determines execution order.
Single-flow workflows infer their start; multi-flow workflows name it explicitly.

## Edit evaluation data

[evaluation/dataset.json](evaluation/dataset.json) is the canonical, tracked
synthetic gold manifest. Edit suite targets and metrics there. Exactly shared
case arrays live in [lookup cases](evaluation/cases/lookup.json); edit inputs and expectations in those referenced files. Any inline cases
remain in the manifest. The evaluator resolves case files relative to the manifest
through the shared bounded JSON loader and strict validator.
No Python regeneration is required. Scripted model responses remain independent
test doubles, so a changed expectation can fail an offline wiring evaluation.
Real customer data and generated reports still belong outside Git.
