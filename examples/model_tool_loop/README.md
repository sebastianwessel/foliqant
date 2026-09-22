# 5. Let a model use a read-only tool

This learning example gives one LLM step a single allowlisted MCP tool. The
model derives a public-request reference and language, the runtime validates and
executes the real local tool, and a second model turn returns the structured
status. The scripted run records two model requests and one tool call; a live provider may use more turns within the configured budget.

The step keeps the authority boundary visible:

```yaml
tools:
  server: records_office
  allow:
    - lookup_request
  choice: required
```

The model can choose only from this compiled allowlist. Tool arguments and the
result are validated against the operator-reviewed catalog in
`config/settings.yaml`; the bundled server declares a read effect.

Run the complete loop with a local scripted `FunctionModel` and the real stdio
MCP server:

```sh
uv run --no-sync python -m examples.model_tool_loop.run
uv run --no-sync python -m examples.model_tool_loop.evaluate
```

The English and German gold covers the pipeline, flow, and LLM step. This proves
model-to-tool-to-model wiring and usage accounting, not live model quality.

To measure the explicitly configured OpenAI-compatible model, set `MODEL_ID` and
`MODEL_BASE_URL`, then opt in:

```sh
uv run --no-sync python -m examples.model_tool_loop.run --live
uv run --no-sync python -m examples.model_tool_loop.evaluate --live
```

Continue with [multi-request processing](../multi_request_processing/README.md)
to plan several assessed requests before invoking callable flows.
