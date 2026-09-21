---
type: decision
sources:
  message: {pointer: /payload/message}
question:
  type: choice
  criteria:
    - Select payment only for charges, invoices, refunds, or payment failures.
    - Select access only for sign-in, credentials, or account access.
  catalog:
    categories:
      - {id: payment, description: Payment or invoice support}
      - {id: access, description: Account access support}
instructions: Answer only from the supplied message and cite exact source evidence.
on_answer:
  payment: done
  access: access_blocked
on_unresolved: review
---
Which inbox queue applies?
