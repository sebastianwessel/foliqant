# Explicit extraction context for MCP

This example extracts a request reference with a model, then calls a real local
stdio MCP tool. It uses selected bindings as the context
contract; there is no ambient history or separate context API.

The workflow selects only `message` and `language` for extraction. The input's
`contact_email` is not exposed to the model. The next flow receives exactly these projected result fields:

```yaml
input:
  reference:
    pointer: /flows/extract/result/reference
  language:
    pointer: /flows/extract/result/language
```

The extraction's `internal_summary` remains available in the execution result
for the current invocation but is not sent to MCP. Its schema is validated, but
the evaluation does not score arbitrary summary wording as an exact string or
measure its semantic faithfulness. The final payload is the validated tool
result. No state, conversation history, or result survives the call.

Run the default scripted model with the bundled real MCP server:

```sh
uv sync --locked --extra mcp --extra openai
uv run --no-sync python -m examples.extracted_request_mcp.run
```

The scripted model proves wiring and validation only. To call the configured
local model endpoint instead, provide the same `MODEL_ID` and
`MODEL_BASE_URL` values used by the support example:

```sh
uv run --no-sync python -m examples.extracted_request_mcp.run --live
```

Evaluate authored English and German cases for the full pipeline, isolated
extraction and lookup flows, and each isolated step:

```sh
uv run --no-sync python -m examples.extracted_request_mcp.evaluate
uv run --no-sync python -m examples.extracted_request_mcp.evaluate --live
```

Live evaluation is opt-in. The default opens only the local stdio server; it
does not perform model inference, make network requests, or download anything.
Reports contain synthetic inputs and are written under ignored `.foliqant/` by
default. These checks do not establish model quality or production correctness.

[config/settings.yaml](config/settings.yaml) loads the
[workflow](config/extracted_request_lookup/workflow.yaml). Flow definitions are found by their IDs; ordered step IDs select conventional
step files, with schemas beside the extraction step. Flow `extract` transitions to `lookup`;
the lookup step binds `/payload/reference` and `/payload/language` from its
flow input. It has no access to the original contact email or internal summary.
Call `app.run_flow("extracted_request_lookup", "lookup", envelope)` with resolved
reference/language input, or `app.run_step("extracted_request_lookup", "lookup",
"lookup", envelope)` to isolate the tool step. Records are nested under
`/flows/lookup/steps/lookup`; transitions are returned separately.

The extraction step uses `{{ message }}` and `{{ language }}` in an explicit
user-message template. Values are JSON serialized, including quoted strings;
substituted text cannot create another placeholder. Trusted `instructions` stay
separate from these untrusted values.

`settings.yaml` omits the workflow registry: immediate configuration subfolders
containing `workflow.yaml` are discovered by folder name. Flow definitions resolve
to `<flow-id>/flow.yaml`; the authored step list still determines execution order.
Single-flow workflows infer their start; multi-flow workflows name it explicitly.

## Edit evaluation data

[evaluation/dataset.json](evaluation/dataset.json) is the canonical, tracked
synthetic gold manifest. Edit suite targets and metrics there. Exactly shared
case arrays live in [extraction cases](evaluation/cases/extract.json) and
[lookup cases](evaluation/cases/lookup.json); edit inputs and expectations in those referenced files. Any inline cases
remain in the manifest. The evaluator resolves case files relative to the manifest
through the shared bounded JSON loader and strict validator.
No Python regeneration is required. Scripted model responses remain independent
test doubles, so a changed expectation can fail an offline wiring evaluation.
Real customer data and generated reports still belong outside Git.
