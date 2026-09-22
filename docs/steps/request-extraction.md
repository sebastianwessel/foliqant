# Identify distinct requests

Use a `request_units` decision when one support email contains zero, one, or several requested actions. It preserves source order, status, optional exact subjects, and explicitly supported relationships. It assesses requests; it does not execute them or create child flows.

Save as `config/support_triage/triage/requests.step.yaml`, list `requests` in the flow, and select a model through the workflow default or step.

```yaml
type: decision
sources:
  message:
    pointer: /payload/message
question:
  type: request_units
  criteria:
    - Return distinct requested actions in source order.
    - Do not turn quoted history or withdrawn requests into active work.
    - Use an exact account reference from the message as subject, or null.
    - Include only relationships explicitly supported by the message.
  catalog:
    categories:
      - id: invoice_copy
        description: Send a copy of an invoice.
      - id: cancel_subscription
        description: Cancel a subscription.
  allowNoMatch: false
instructions: Identify the writer's requests using only the supplied email.
```

For “Send invoice INV-42, then cancel my subscription,” a possible native public step record is:

```json
{
  "status": "completed",
  "result": {
    "questionId": "requests",
    "type": "request_units",
    "answerability": {"status": "answerable", "issues": []},
    "reason": "Two current requests appear in order, with an explicit sequence.",
    "evidence_strength": "strong",
    "answer": {
      "units": [
        {"id": "invoice", "status": "active", "categoryId": "invoice_copy", "subject": "INV-42", "description": "Send invoice INV-42."},
        {"id": "cancel", "status": "active", "categoryId": "cancel_subscription", "subject": null, "description": "Cancel my subscription."}
      ],
      "relations": [
        {"type": "precedes", "beforeRequestId": "invoice", "afterRequestId": "cancel"}
      ]
    }
  }
}
```

Unit statuses are `active`, `withdrawn`, `conditional`, and `quoted`. `categoryId` may be JSON `null` only with `allowNoMatch: true` and a `no_supported_answer` issue. A non-null `subject` must appear verbatim in an allowed source. The result has no `evidence` array or per-unit citations; use its short `reason` for the assessment. `units` and `relations` can be empty for an explicit no-action message. Unclear intent calls for `answer: null`, `not_answerable`, and `no_supported_answer`. A clear subset with unresolved work can be `partially_answerable` and produces `needs_review`.

Advanced relations are `requires` (`requestId`, `requiredRequestId`), `precedes` (`beforeRequestId`, `afterRequestId`), `mutually_exclusive` (`requestIds`), and `conditional_on` (`requestId`, `predicateQuestionId`, `requiredValue: "true"|"false"`). `conditional_on` refers to a predicate question in the same decision step, so use the full `questions` form described in [shared decision configuration](decision.md). Relations must reference known IDs and cannot form directed cycles. Avoid inventing dependencies merely because actions appear together.

To act on extracted requests, have trusted code turn the assessment into explicit `{id, flow, input}` items, then use a [flow collection](flow-collection.md). Review active, withdrawn, quoted, repeated, and ambiguous cases in [evaluations](../evaluation/task-types.md).
