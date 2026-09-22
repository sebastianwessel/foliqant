# 2. Add structured extraction

A queue decision and an extraction answer different questions. Keep them as two
steps so each contract, prompt, and evaluation can be inspected independently.

## Extend the flow

Order classification before extraction and project the structured extraction:

```yaml
output:
  pointer: /steps/extract/result
  optional: true
  default:
    status: needs_review
steps:
  - classify
  - extract
```

The extraction step receives only the original message. It does not receive the
classification conversation or result implicitly:

```markdown
---
type: llm
input:
  message:
    pointer: /payload/message
prompt: "{{ message }}"
output:
  schema: output.schema.json
---
Extract the currently active requested action, any deadline, and the account
reference. Return null for details the message does not state.
```

Exact `{{ name }}` substitutions serialize the selected value as compact JSON.
A value containing template syntax is inserted once and is never evaluated as a
second template. The Markdown body remains stable trusted instructions; only the
frontmatter `prompt` substitutes input. Keep `output.schema.json` beside the step so the output shape
is reviewed with the prompt.

## Keep the workflow route conservative

The workflow still owns completion and review. If classification is unresolved,
`on_unresolved` prevents the next step from inventing a category. A fallback can
select an application review bucket, but it does not turn an unresolved native
answer into model correctness.

## Evaluate the seam

Use a pipeline suite for business behavior, a flow suite for the two-step
boundary, and separate step suites for classification and extraction. Step cases
supply already-resolved direct inputs; they do not execute upstream bindings.

Run the complete offline example:

```sh
python -m examples.support_triage.run
python -m examples.support_triage.evaluate
```

Study
[`examples/support_triage`](https://github.com/sebastianwessel/foliqant/tree/main/examples/support_triage),
then [route the selected category between flows](multiflow-routing.md).
