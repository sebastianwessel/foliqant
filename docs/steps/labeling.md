# Apply all supported labels

Use a `multiselect` decision when a support email may have several independent tags. Unlike [classification](classification.md), this task can return a supported subset when one tag remains unresolved.

Save as `config/support_triage/triage/label.step.yaml`, list `label` in the flow, and select a model through the workflow default or this step.

```yaml
type: decision
sources:
  message:
    pointer: /payload/message
question:
  type: multiselect
  criteria:
    - Select billing for a current invoice, payment, or charge request.
    - Select cancellation for a current request to end a subscription.
    - Return an empty set only when the writer explicitly requests neither kind of work.
  catalog:
    categories:
      - id: billing
        description: Current invoice, payment, or charge work.
      - id: cancellation
        description: Current subscription cancellation work.
  minSelections: 0
  maxSelections: 2
instructions: Which support tags are supported by the current email?
```

For “Please correct my invoice and cancel my subscription,” the native step result can be:

```json
{
  "status": "completed",
  "result": {
    "questionId": "label",
    "type": "multiselect",
    "answerability": {"status": "answerable", "issues": []},
    "reason": "Both invoice and cancellation requests are explicit.",
    "evidence_strength": "strong",
    "answer": {"optionIds": ["billing", "cancellation"]}
  }
}
```

`optionIds` is an array of unique category IDs. `minSelections` and `maxSelections` are required integers; the minimum may be zero, the maximum must be at least one and no greater than the catalog size. An answerable empty array is valid when the source affirmatively establishes no matching work. Silence about these topics should usually yield `answer: null` with `no_supported_answer` and `needs_review`. If billing is clear but cancellation instructions conflict, a nonempty subset may be `partially_answerable` with `conflicting_information`; the step is still `needs_review`. Semantic choices must be checked with [evaluations](../evaluation/task-types.md).

For advanced use, assess independent dimensions with full `questions` and explicit `allowedSourceIds`; see [shared decision configuration](decision.md).
