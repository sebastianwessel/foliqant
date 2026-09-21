# Examples

- [Support triage](support_triage/README.md): local Qwen decision and structured
  extraction, plus in-code synthetic evaluation cases.
- [Public-request lookup](public_request_mcp/README.md): read-only local MCP
  integration without a model call.
- [HTTP wrapper](http_workflow/README.md): a thin transport around support triage.

All records are synthetic. Model-backed commands require an explicit `--live`
flag; offline tests use local fakes.

Each example has a runnable `evaluate` module using `foliqant.evaluation`.
Support and HTTP checks are offline by default; add `--live` for local Qwen.
The MCP evaluation uses its bundled stdio server. Synthetic wiring checks are
not model-quality measurements. See each example’s README for exact commands.
