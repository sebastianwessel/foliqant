# 3. Route between flows

Use a new flow when a result selects a different business boundary. In this
example, classification chooses between billing and cancellation preparation;
the workflow, not the model, owns the allowed destinations.

The [workflow guide](../configuration/workflows.md) explains transitions and
review routes. The [handler guide](../steps/handler.md) explains the trusted
Python functions used after classification.

## Add branch flows

Declare the start and match its projected scalar result exactly:

```yaml
start: classify
flows:
  classify:
    input:
      message:
        pointer: /payload/message
    transition:
      binding:
        pointer: /flows/classify/result
      cases:
        billing:
          flow: billing
        cancellation:
          flow: cancellation
      default:
        outcome: needs_review
    on_unresolved:
      outcome: needs_review
  billing:
    input:
      message:
        pointer: /payload/message
    transition:
      outcome: completed
  cancellation:
    input:
      message:
        pointer: /payload/message
    transition:
      outcome: completed
```

The required default makes an unexpected string a review outcome. Exact matching
does not coerce numbers, booleans, null, or similar strings.

## Register trusted branch code

Each branch contains a handler step:

```yaml
type: handler
handler: prepare_billing
input:
  message:
    pointer: /payload/message
```

YAML can select only a name that the host registered before compilation:

```python
from collections.abc import Mapping
from pathlib import Path

from foliqant import prepare_application
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import freeze_json


async def prepare_billing(inputs, context):
    del context
    return StepOutcome(
        {
            "queue": "billing",
            "action": "request_invoice_review",
            "message": inputs["message"],
        }
    )


async def prepare_cancellation(inputs, context):
    del context
    return StepOutcome(
        {
            "queue": "cancellation",
            "action": "prepare_cancellation",
            "message": inputs["message"],
        }
    )


input_schema = freeze_json(
    {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }
)
output_schema = freeze_json(
    {
        "type": "object",
        "properties": {
            "queue": {"enum": ["billing", "cancellation"]},
            "action": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["queue", "action", "message"],
        "additionalProperties": False,
    }
)
assert isinstance(input_schema, Mapping)
assert isinstance(output_schema, Mapping)

handlers = {
    "prepare_billing": HandlerRegistration(
        prepare_billing,
        input_schema,
        output_schema,
    ),
    "prepare_cancellation": HandlerRegistration(
        prepare_cancellation,
        input_schema,
        output_schema,
    ),
}
prepared = prepare_application(Path("config/settings.yaml"), handlers=handlers)
```

Handlers are trusted host code, but their selected input and returned result are
still validated against the registration schemas. Built-in workflow access is
read-only; write-effect registrations are rejected.

## Observe the actual branch

Pipeline results retain every declared flow. The selected branch is completed,
the other branch is skipped, and `/transitions` records the authored target.
Flow and step evaluations isolate the classifier without running the branch.
For an invoice message, check `transitions[0].flow == "billing"`, the billing
flow result queue is `billing`, and the cancellation flow status is `skipped`.

Run and inspect the example:

```sh
python -m examples.routed_intake.run
python -m examples.routed_intake.evaluate
```

Continue from
[`examples/routed_intake`](https://github.com/sebastianwessel/foliqant/tree/main/examples/routed_intake)
to [a declared read-only MCP call](read-only-mcp.md).
