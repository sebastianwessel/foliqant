# Choose one category

Use a `choice` decision when one current support request must go to exactly one queue. Categories are stable IDs with descriptions; they are not invented by the model. When an email contains two independent requests, this single-choice task may need review or a [request extraction](request-extraction.md) step.

Save as `config/support_triage/triage/classify.step.yaml` and list `classify` in the flow. Provide a workflow default model or set `model` here.

```yaml
type: decision
sources:
  message:
    pointer: /payload/message
question:
  type: choice
  criteria:
    - Select billing only for a current question about an invoice, payment, or charge.
    - Select cancellation only for a current request to end a subscription.
    - If both queues apply, abstain with multiple_valid_options.
  catalog:
    categories:
      - id: billing
        description: Current invoice, payment, or charge work.
      - id: cancellation
        description: Current subscription cancellation work.
instructions: Which one support queue owns this email? Use only the supplied message.
```

For “Why was my invoice charged twice?”, the public `/flows/triage/steps/classify` record can contain:

```json
{
  "status": "completed",
  "result": {
    "questionId": "classify",
    "type": "choice",
    "answerability": {"status": "answerable", "issues": []},
    "reason": "The email asks about an invoice charge.",
    "evidence_strength": "strong",
    "answer": {"optionId": "billing"}
  },
  "selection": {
    "category": {"id": "billing", "description": "Current invoice, payment, or charge work."},
    "origin": "model"
  }
}
```

`answer.optionId` is the native model answer. `selection` is a separate effective category, suitable for an explicitly configured binding or route. For two active queues, the result must have `answer: null`, `answerability.status: "not_answerable"`, and `multiple_valid_options`; the step is `needs_review`. Unknown IDs and inconsistent answers fail validation. See [shared decision rules](decision.md).

## Fallback selection

An optional fallback is available only for one `choice` question:

```yaml
fallback:
  category:
    id: manual_review
    description: A queue for cases requiring a person.
  "on":
    - no_supported_answer
```

Add it at the top level of the step. This category is excluded from model choices and must have a distinct ID. It produces `selection.origin: "fallback"` only for a validated `not_answerable` result whose nonempty issues are all allowed by `on`. The native result stays unanswered and the step stays `needs_review`; fallback does not handle timeouts or invalid output. Include every issue you intend to allow explicitly. Evaluate unsupported, conflicting, and multi-queue emails before routing automatically.
