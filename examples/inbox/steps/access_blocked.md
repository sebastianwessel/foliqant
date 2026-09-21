---
type: decision
sources:
  message: {pointer: /payload/message}
question:
  type: predicate
  criteria:
    - Return true only when the message explicitly says access is blocked.
    - Return false only when the message explicitly says access still works.
instructions: Answer only from the supplied message and cite exact source evidence.
on_answer:
  "true": review
  "false": done
on_unresolved: review
---
Is the sender currently blocked from accessing the account?
