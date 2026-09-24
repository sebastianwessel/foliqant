# Examples

## Learning sequence

For a from-scratch scenario that combines these capabilities, build the
[support email tutorial](support_email_tutorial/README.md).

Work through these in order. Each stage adds one runtime capability and keeps
model-backed commands offline unless `--live` is explicit.

1. [Classify one request](decision_basics/README.md): one typed decision and a
   review outcome.
2. [Add structured extraction](support_triage/README.md): selected prior-step
   context and a validated JSON result.
3. [Route between flows](routed_intake/README.md): exact deterministic routing
   to trusted handlers.
4. [Call a read-only MCP tool](public_request_mcp/README.md): one direct,
   declared tool call without a model.
5. [Let a model use a tool](model_tool_loop/README.md): a complete
   model-to-MCP-to-model loop.
6. [Process several requests](multi_request_processing/README.md): typed request
   assessment, trusted planning, bounded callable flows, and disposition.

## Focused examples

- [Typed decisions and evidence](decision_evidence/README.md) compares all
  decision question forms, evidence strength, and application fallback.
- [Extract then look up](extracted_request_mcp/README.md) passes only selected
  extraction fields to a direct MCP operation.
- [Conditional intake](conditional_intake/README.md) keeps all control flow in
  configuration: a routed start, `cases` and `route` transitions, step `when`,
  `first_of` and object outputs, declared handlers, and a `repeat` that retries
  an MCP lookup with a model-corrected reference.
- [Prompt-security evaluation](security_evaluation/README.md) contains paired
  English and German cases for instruction boundaries and selected context.
- [HTTP wrapper](http_workflow/README.md) embeds the runtime in a thin transport.

All records are synthetic. Each learning example keeps editable reviewed gold
under its own `evaluation/` directory and observes pipeline, flow, and step
scopes. Reusing one source case across scopes does not create additional
independent business examples.

Evaluation commands save a new private report under ignored `.foliqant/` by
default. Full reports can contain inputs, gold, public results, reasons, metrics,
and usage; keep generated reports and real customer data out of Git. The tracked
synthetic fixtures establish wiring and authored policy behavior, not general
model quality, security, latency, cost, or cache efficiency.

Workflow examples use `config/settings.yaml`, conventional
`config/<workflow>/workflow.yaml`, `<flow>/flow.yaml`, and colocated step and
schema files. Ordered step IDs and routes remain explicit; filesystem order
never controls execution.
