# 5. Let a model use the read-only tool

The direct path supplies `lookup_account` with an extracted reference. A
bounded model tool loop can instead call the same allowlisted tool and draft
from its validated return. The final example keeps this as a separate
`agent_reply` workflow so you can compare the two boundaries.

Create `my_support/config/agent_reply/workflow.yaml` with one `answer` flow
that binds `message` and `account_reference` from its input payload. The
[example workflow](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/agent_reply/workflow.yaml)
includes its input schema and output projection. In
`my_support/config/agent_reply/answer/flow.yaml` list one `answer` step and
project `/steps/answer/result`.

Create `my_support/config/agent_reply/answer/answer.step.md`:

```markdown
---
type: llm
max_iterations: 2
input:
  message:
    pointer: /payload/message
  account_reference:
    pointer: /payload/account_reference
output:
  schema: answer.schema.json
tools:
  server: account_records
  allow:
    - lookup_account
  choice: required
---
Call lookup_account with exactly the supplied account_reference. Draft a
reply using only validated account facts. Do not claim that a refund or
cancellation has been completed.
```

Use the [answer schema](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/agent_reply/answer/answer.schema.json):
a closed object with one required `reply` string. `max_iterations: 2`
allows a call turn and a final answer turn. `choice: required` requires an
allowlisted call; the model cannot add a server or a write tool. Runtime
argument/result validation and the shared execution limits still apply.

Run two scripted turns through the real local MCP server:

```sh
uv run --no-sync python -m examples.support_email_tutorial.run --agent
```

Expect `execution.usage.model_requests: 2`, `tool_calls: 1`, and a `reply`
in the projected payload. This proves the call/result/final-answer wiring;
it does not prove that a live model will always call the tool with the right
reference. Keep your reference extraction and business policy outside the
loop when they need separate review. See [LLM steps](../steps/llm.md) for
iteration semantics. Next, consider [multiple requests](multi-request-processing.md).
