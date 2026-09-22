# Ask a yes/no question

Use a `predicate` decision when a support email must establish one claim, such as whether the writer disputes a charge. Its value is one of the strings `"true"`, `"false"`, or `"unknown"`—it is not a JSON boolean. Unknown means the supplied evidence supports neither yes nor no.

Save this definition as `config/support_triage/triage/dispute.step.yaml` and list `dispute` in that flow's `steps`. The workflow needs `defaults.model`, or add `model` to this step.

```yaml
type: decision
sources:
  message:
    pointer: /payload/message
question:
  type: predicate
  criteria:
    - Return true for an explicit disputed charge.
    - Return false for an explicit statement that no charge is disputed.
    - Return unknown when neither conclusion is supported.
instructions: Does the writer dispute a charge? Use only the supplied message.
```

For “I dispute the duplicate charge,” the public record at `/flows/triage/steps/dispute` can contain:

```json
{
  "status": "completed",
  "result": {
    "questionId": "dispute",
    "type": "predicate",
    "answerability": {"status": "answerable", "issues": []},
    "reason": "The writer explicitly disputes a duplicate charge.",
    "evidence_strength": "strong",
    "answer": {"value": "true"}
  }
}
```

For “Please send my invoice,” do not infer `false` merely because no dispute was mentioned. The native result uses `answer: {"value": "unknown"}`, `answerability.status: "not_answerable"`, and an issue such as `no_supported_answer`; the step is `needs_review`. It may still have `evidence_strength: "strong"` when absence of support under the criteria is clear. Conflicting statements may instead justify `conflicting_information`. See [shared decision rules](decision.md) for reason, strength, status, and multiple questions.

For advanced use, add other named `sources`, use `format: json` for structured source data, or use a full question in `questions` when assessing several claims. Test explicit yes, explicit no, absent evidence, and conflict cases with [decision evaluations](../evaluation/task-types.md).
