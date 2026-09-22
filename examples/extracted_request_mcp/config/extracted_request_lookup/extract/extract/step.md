---
type: llm
input:
  message:
    pointer: /payload/message
  language:
    pointer: /payload/language
instructions: Extract the public-request reference and preserve the supplied language.
  Write a short internal summary. Never infer or copy contact information.
prompt: |
  Customer message (JSON string): {{ message }}
  Requested language (JSON string): {{ language }}
output:
  schema: output.schema.json
---
