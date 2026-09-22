# 1. Classify one support email

The first version asks one question: does the email request billing help or
cancellation? A single choice cannot represent two active requests, so that
case must stop for review.

Create `my_support/config/support_email/input.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {"message": {"type": "string", "minLength": 1, "maxLength": 4000}},
  "required": ["message"],
  "additionalProperties": false
}
```

Create `my_support/config/support_email/workflow.yaml`:

```yaml
defaults:
  model: local_qwen
input_schema: input.schema.json
flows:
  classify:
    input:
      message:
        pointer: /payload/message
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
output:
  pointer: /flows/classify/result
  optional: true
  default: null
```

Create `my_support/config/support_email/classify/flow.yaml`:

```yaml
steps:
  - classify
output:
  pointer: /steps/classify/selection/category/id
  optional: true
  default: null
```

The workflow binding gives the flow its message. The flow projects the
validated effective category; the full decision remains at
`flows.classify.steps.classify.result`. Create
`my_support/config/support_email/classify/classify.step.md`:

```markdown
---
type: decision
sources:
  message:
    pointer: /payload/message
question:
  type: choice
  criteria:
    - Select billing for an invoice, charge, payment, or refund request.
    - Select cancellation for an active cancellation or non-renewal request.
    - If both queues are requested, report multiple_valid_options.
  catalog:
    categories:
      - id: billing
        description: An invoice, charge, payment, or refund request.
      - id: cancellation
        description: An active cancellation or non-renewal request.
---
Choose from the supplied email only. Give a concise reason and evidence strength.
```

The body is trusted task instruction. The email is evidence and cannot add
categories. Compile without a model call:

```sh
uv run --no-sync foliqant validate --config my_support/config/settings.yaml
uv run --no-sync foliqant explain --config my_support/config/settings.yaml --workflow support_email
```

Expect one workflow, one flow, and one step. The final example's synthetic
two-queue case ends in `needs_review`. See [decision contracts](../guides/decision-contracts.md)
for `answerability`, `reason`, and `evidence_strength`. Next,
[extract account context](structured-extraction.md).
