# 1. Classify one request

Start with one business question: does a message concern billing or an active
cancellation? Unsupported or ambiguous evidence must reach human review.

Keep [Inputs, results, and errors](../reference/inputs-and-results.md) nearby for
the envelope and decision shapes, including what `reason`, `evidence_strength`,
and answerability issues mean.

## Create the boundaries

Use one workflow, one flow, and one decision step:

```text
config/
  settings.yaml
  decision_basics/
    input.schema.json
    workflow.yaml
    classify/
      flow.yaml
      classify.step.md
evaluation/
  dataset.json
  cases/
    requests.json
```

The workflow selects only the message and owns the terminal outcomes:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "message": {"type": "string", "minLength": 1, "maxLength": 4000}
  },
  "required": ["message"],
  "additionalProperties": false
}
```

Save that as `input.schema.json`, then create `workflow.yaml`:

```yaml
defaults:
  model: local
input_schema: input.schema.json
output:
  pointer: /flows/classify/result
  optional: true
  default: null
flows:
  classify:
    input:
      message:
        pointer: /payload/message
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
```

The flow fixes step order and projects its effective category:

```yaml
output:
  pointer: /steps/classify/selection/category/id
  optional: true
  default: null
steps:
  - classify
```

## Author the decision

Put the task definition in `classify.step.md`. Descriptions say what each
category means; criteria say how to distinguish them. The message is evidence,
not a place to redefine the task.

```markdown
---
type: decision
sources:
  message:
    pointer: /payload/message
question:
  type: choice
  criteria:
    - Select billing only for an invoice, charge, payment, or refund request.
    - Select cancellation only for an active request to cancel or stop renewal.
    - If neither or both apply, do not select one.
  catalog:
    categories:
      - id: billing
        description: An invoice, charge, payment, or refund request.
      - id: cancellation
        description: An active cancellation or non-renewal request.
---
Classify the request using only the supplied message.
```

A shorthand choice requires at least two catalog categories. The compiled
question and criteria remain trusted task configuration; the bound source text
remains untrusted evidence.

## Configure and open the application

Add an explicit model profile to `config/settings.yaml`:

```yaml
models:
  local:
    provider: openai_compatible
    model: $MODEL_ID
    base_url: $MODEL_BASE_URL
    allow_insecure_http: true
    output_mode: native
    supports_tools: false
```

Install a reviewed checkout as described in [Install and run](../getting-started/runtime.md).
In application code, prepare before opening clients and pass one envelope:

```python
import asyncio
import os
from pathlib import Path

from foliqant import Envelope, open_application, prepare_application


async def main() -> None:
    prepared = prepare_application(Path("config/settings.yaml"))
    async with open_application(prepared, environment=os.environ) as application:
        result = await application.run(
            "decision_basics",
            Envelope(payload={"message": "Cancel my subscription at renewal."}),
        )
        print(result.model_dump_json())


asyncio.run(main())
```

## Verify each scope

Run the example without opening an endpoint:

```sh
python -m examples.decision_basics.run
python -m examples.decision_basics.evaluate
```

The gold has four independently authored messages: English and German billing
and cancellation cases. Observe the same source cases at pipeline, flow, and
step scope, but count them as four business cases rather than twelve new inputs.
One expectation is a public JSON pointer:

```json
{
  "name": "selection",
  "path": "/flows/classify/steps/classify/selection/category/id",
  "expected": "cancellation"
}
```

Read the complete files in
[`examples/decision_basics`](https://github.com/sebastianwessel/foliqant/tree/main/examples/decision_basics),
then [add structured extraction](structured-extraction.md).
