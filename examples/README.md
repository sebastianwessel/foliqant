# Examples

- [Typed decisions and evidence](decision_evidence/README.md): compare single-choice
  triage, labels, predicates, priority, and request units with explicit support gold.
- [Support triage](support_triage/README.md): local Qwen decision and structured
  extraction, plus editable synthetic JSON evaluation cases.
- [Public-request lookup](public_request_mcp/README.md): read-only local MCP
  integration without a model call.
- [Extract then look up](extracted_request_mcp/README.md): local Qwen extracts a
  reference and passes only selected fields to a read-only MCP tool.
- [Prompt-security evaluation](security_evaluation/README.md): paired English/German
  cases for instruction boundaries and selected context between steps.
- [HTTP wrapper](http_workflow/README.md): a thin transport around support triage.

All records are synthetic. Model-backed commands require an explicit `--live`
flag; offline tests use local fakes or explicit configuration/gold checks.

Each example has a runnable `evaluate` module using `foliqant.evaluation`.
Support and HTTP checks are offline by default; add `--live` for local Qwen.
The evidence example checks configuration/gold by default and uses real Qwen
only with `--live`. The MCP evaluation uses its bundled stdio server. Synthetic wiring checks are
not model-quality measurements. See each example’s README for exact commands.

Evaluation commands save a new private report under ignored `.foliqant/` by
default; `--output` selects a new path. `--repeat` measures each authored case
several times without treating repetitions as new independent inputs. Full
reports contain inputs, gold, public reasons/results, metrics, and usage.
Ordinary workflow execution never writes evaluation data.

Workflow examples keep their entry point in `config/settings.yaml`, workflow directories
at `config/<workflow-id>/workflow.yaml`, flow definitions at `<flow-id>/flow.yaml`,
and ordered step IDs resolving to `<step-id>.step.yaml`/`.md` or
`<step-id>/step.yaml`/`.md`. Schemas live beside their definitions.
Settings can omit the workflow registry; file lookup rejects ambiguous candidates
and never determines step order. Explicit references remain available. Evaluation targets cover the pipeline, flows and individual steps.
Returned records are scoped as `/flows/{flow}/steps/{step}`; input bindings
inside a flow use only that flow’s payload and earlier local step results.

Each example’s `evaluation/dataset.json` is tracked, editable synthetic ground
truth. Python runners read those files directly; model test doubles are authored
independently. Only generated reports and real/private datasets remain ignored.
