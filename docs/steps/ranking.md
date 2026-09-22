# Choose an ordered level

Use an `ordinal` decision for a rubric such as support urgency. The ordered `levels` are authored in ascending or meaningful business order; the result is the chosen level ID, not a numeric score or evidence strength.

Save as `config/support_triage/triage/urgency.step.yaml` and list `urgency` in the flow. Set a model on the workflow or step.

```yaml
type: decision
sources:
  message:
    pointer: /payload/message
question:
  type: ordinal
  criteria:
    - Choose routine when the writer explicitly allows normal processing time.
    - Choose urgent when the writer gives a near-term deadline.
    - Do not infer urgency from capitalization or repetition alone.
  levels:
    - id: routine
      description: Normal timing is explicitly acceptable.
    - id: urgent
      description: An explicit near-term deadline applies.
instructions: How urgent is the support request, based on the email's timing?
```

For “Please fix this before tomorrow's payroll,” a possible public record is:

```json
{
  "status": "completed",
  "result": {
    "questionId": "urgency",
    "type": "ordinal",
    "answerability": {"status": "answerable", "issues": []},
    "reason": "The writer gives a deadline before tomorrow's payroll.",
    "evidence_strength": "strong",
    "answer": {"levelId": "urgent"}
  }
}
```

The `levels` list needs at least two unique IDs. If the email contains no timing cue, use `answer: null`, `not_answerable`, and `no_supported_answer`; the step becomes `needs_review`. `evidence_strength` rates support for the assessment, not severity: a well-supported routine rating can be strong. For larger rubrics, describe adjacent boundaries clearly and evaluate borderline cases. See [shared decision rules](decision.md) and [task scoring](../evaluation/task-types.md).
