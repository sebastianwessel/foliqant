# Explicit extraction context for MCP

This example extracts a request reference with a model, then calls a real local
stdio MCP tool. It uses Foliqant's existing bindings as the explicit context
contract; there is no ambient history or separate context API.

The workflow selects only `message` and `language` for extraction. The input's
`contact_email` is not exposed to the model. The MCP step then selects exactly
these earlier result fields:

```yaml
arguments:
  reference:
    pointer: /steps/extract/result/reference
  language:
    pointer: /steps/extract/result/language
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
local Qwen endpoint instead, provide the same `FOLIQANT_CURATION_MODEL` and
`FOLIQANT_CURATION_ENDPOINT_URL` values used by the support example:

```sh
uv run --no-sync python -m examples.extracted_request_mcp.run --live
```

Evaluate authored English and German cases for the full pipeline, isolated
extraction, and isolated MCP lookup:

```sh
uv run --no-sync python -m examples.extracted_request_mcp.evaluate
uv run --no-sync python -m examples.extracted_request_mcp.evaluate --live
```

Live evaluation is opt-in. The default opens only the local stdio server; it
does not perform model inference, make network requests, or download anything.
Reports contain synthetic inputs and are written under ignored `.foliqant/` by
default. These checks do not establish model quality or production correctness.
