---
type: decision
sources:
  message: {pointer: /payload/message}
question:
  type: choice
  criteria:
    - Select billing_dispute only for a disputed charge, invoice, refund, or payment failure.
    - Select service_change only for an active request to add or change a service.
    - Select cancellation only for an active request to cancel or stop renewal.
  catalog:
    categories:
      - {id: billing_dispute, description: A billing or payment dispute}
      - {id: service_change, description: A request to add or change service}
      - {id: cancellation, description: A cancellation or non-renewal request}
instructions: Answer only from the supplied message and cite exact source evidence.
on_answer:
  billing_dispute: extract
  service_change: extract
  cancellation: extract
on_unresolved: review
---
Which support queue owns this request?
