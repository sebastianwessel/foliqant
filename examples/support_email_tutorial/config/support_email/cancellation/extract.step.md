---
type: llm
input:
  message:
    pointer: /payload/message
output:
  schema: extract.schema.json
---
Extract the active cancellation request as a short verbatim span and the
customer account reference explicitly stated in the email. Return null if no
account reference is stated. Never invent a reference.
