# 5. Let a model use a read-only tool

Use an LLM step with `tools` when the model must derive tool arguments from a
message, see the validated result, and produce a structured answer. Keep the
server and tool allowlist authored.

## Enable only the required capability

The selected model profile must declare tool support, and the MCP catalog must
declare the same read-only tool:

```yaml
models:
  local_qwen:
    provider: openai_compatible
    model: $MODEL_ID
    base_url: $MODEL_BASE_URL
    allow_insecure_http: true
    output_mode: native
    supports_tools: true
```

The step allows one tool and requires its use:

```markdown
---
type: llm
input:
  message:
    pointer: /payload/message
output:
  schema: answer.schema.json
tools:
  server: records_office
  allow:
    - lookup_request
  choice: required
---
Select the reference and language from the message, call lookup_request, and
return only the validated status fields. Never invent a status or due date.
```

`choice: required` requires some allowlisted tool; a named choice can require one
specific allowed name. The model cannot add servers or tools, and every call
still passes authorization plus input/output validation.

## Verify both model turns

The offline `FunctionModel` first emits a tool call. The runtime executes the
real local MCP tool and delivers its result to the second scripted model turn.
The example asserts two model requests and one tool call:

```sh
python -m examples.model_tool_loop.run
python -m examples.model_tool_loop.evaluate
```

This proves the loop wiring and accounting, not that a live model will always
choose correct arguments. Review
[`examples/model_tool_loop`](https://github.com/sebastianwessel/foliqant/tree/main/examples/model_tool_loop),
then [process several requests conservatively](multi-request-processing.md).
