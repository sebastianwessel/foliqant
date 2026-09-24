# 2. Extract action and account context

Classification and extraction answer different questions. The decision
selects a queue; extraction records the details a later lookup needs.

Add `extract` after `classify` in
`my_support/config/support_email/classify/flow.yaml`:

```yaml
steps:
  - classify
  - extract
output:
  pointer: /steps/classify/selection/category/id
  default: null
```

Create `my_support/config/support_email/classify/extract.step.md`:

```markdown
---
type: llm
input:
  message:
    pointer: /payload/message
output:
  schema: extract.schema.json
---
Extract the active requested action as a short verbatim span. Copy only a
customer account reference explicitly stated in the email. Return null when
there is none. An invoice number is not an account reference.
```

Create `my_support/config/support_email/classify/extract.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "requested_action": {"type": "string", "minLength": 1},
    "account_reference": {"type": ["string", "null"]}
  },
  "required": ["requested_action", "account_reference"],
  "additionalProperties": false
}
```

The LLM receives its named `message` binding. It does not inherit the
classifier's conversation. The schema checks shape; your evaluation cases
must check whether the fields are grounded in the email. For the synthetic
invoice email, expect `A-100`. Without an explicit account reference, expect
`null` and hold the later lookup for review.

From the repository root, validate the updated files:

```sh
uv run --no-sync foliqant validate --config my_support/config/settings.yaml
```

The flow still projects the category; extraction is visible in its step record.
The next chapter moves extraction into each branch after routing. Continue to
[deterministic routing](multiflow-routing.md). See [LLM steps](../steps/llm.md)
for binding and output options.
