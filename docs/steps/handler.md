# Configure a handler step

A `handler` step calls one trusted async Python function registered by the host.
Use it for deterministic normalization, planning, policy, or projection that is
clearer and safer in code. Workflow YAML can select a registered name but cannot
import or execute arbitrary Python.

## Implement the async contract

A handler receives immutable JSON inputs and a `StepContext`, then returns a
`StepOutcome`. Register closed input and output schemas around it:

```python
from collections.abc import Mapping

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, freeze_json
from foliqant.ports.execution import StepContext

INPUT_SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "queue": {"type": "string", "enum": ["billing"]},
        "message": {"type": "string"},
    },
    "required": ["queue", "message"],
    "additionalProperties": False,
}


def schema(value: dict[str, object]) -> FrozenObject:
    frozen = freeze_json(value)
    assert isinstance(frozen, Mapping)
    return frozen


async def prepare_billing(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    return StepOutcome({"queue": "billing", "message": inputs["message"]})


HANDLERS = {
    "prepare_billing": HandlerRegistration(
        handler=prepare_billing,
        input_schema=schema(INPUT_SCHEMA),
        output_schema=schema(OUTPUT_SCHEMA),
    )
}
```

The callback must be an async function or async callable. Both schemas must be
confined valid JSON Schema objects. `effect` defaults to `read`; the current
pipeline rejects registrations with `effect: write`.

## Register before compilation

Pass registrations to `prepare_application`, because compilation checks every
configured name and its schemas. Runtime hooks are supplied separately through
`RuntimePlugins` when the application opens:

```python
from pathlib import Path

from foliqant import RuntimePlugins, open_application, prepare_application

prepared = prepare_application(
    Path("config/settings.yaml"),
    handlers=HANDLERS,
)

async with open_application(
    prepared,
    environment={},
    plugins=RuntimePlugins(),
) as app:
    # await app.run(...)
    pass
```

`RuntimePlugins` holds trusted model and MCP runtime hooks; handler registrations
remain part of the compiled application digest. Register the same handler set
for validation, tests, and production startup.

## Select the handler in YAML

```yaml
# config/routed_intake/billing/prepare.step.yaml
type: handler
handler: prepare_billing
input:
  message:
    pointer: /payload/message
```

The keys under `input` must satisfy the registered input schema. Static checks
reject provable missing, extra, or incompatible values; runtime validation
covers dynamic data before the callback runs.

## Return completion, review, or failure

`StepOutcome(result)` completes the step after the output schema validates.
`StepOutcome(result, needs_review=True)` records the validated result and stops
the flow for its unresolved route. A handler may also provide `route_key`, but
flow routing still comes from configuration.

The runtime maps contract problems to stable errors:

| Condition | Result |
| --- | --- |
| Input fails the registered schema | `invalid_input` |
| Return value is not `StepOutcome`, or its result fails the output schema | `invalid_output` |
| Handler exceeds the root deadline | `timeout` |
| Handler raises `ServiceError` | That safe error is preserved |
| Handler raises another exception | `dependency_failure` without raw exception text |

Cancellation propagates. Use native async I/O; the runtime does not move a
blocking handler to a worker automatically or prove that external work stopped.
Handlers are in-process code and share the host's trust boundary.

The [routing tutorial](../tutorials/multiflow-routing.md) contains complete
registered handlers. Test handlers with local inputs and assert both their
`StepOutcome` and the public `run_step` record; see
[unit testing](../evaluation/unit-testing.md).
