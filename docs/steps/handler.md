# Configure a handler step

A `handler` step calls one trusted async Python function supplied by the host.
Use it for deterministic normalization, planning, policy, or projection that is
clearer and safer in code. Configuration declares the handler's **contract**;
the host **registers** the callable. Workflow YAML can select a declared name
but cannot import or execute arbitrary Python.

Before writing a handler, check whether configuration already expresses the
job: [`route` and `when`](../configuration/conditions.md) replace handlers that
only compute a routing key, and [`first_of` and object
outputs](../configuration/context.md#combine-candidates-and-build-objects)
replace handlers that only pick or assemble values.

## Declare the contract in settings

Declare every handler under `handlers` in `settings.yaml`, next to the MCP
catalogs:

```yaml
# config/settings.yaml
handlers:
  prepare_billing:
    input_schema: contracts/handoff.input.json
    output_schema: contracts/handoff.output.json
    effect: read
```

Schemas are inline JSON Schema objects or JSON/YAML files relative to the
settings file; paths must stay inside its directory and schemas must be
self-contained (only internal `$ref`). `effect: write` is declared honestly but
rejected by the read-only pipeline.

Because the contract lives in configuration, `foliqant validate`, `explain`,
`doctor` and `evaluate --check` compile workflows with handlers without any
Python registration. The compiler uses the declared schemas for binding type
checks and for [route coverage](../configuration/workflows.md#match-an-exact-value-with-cases).

## Implement and register the callable

A handler receives immutable JSON inputs and a `StepContext`, then returns a
`StepOutcome`:

```python
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject
from foliqant.ports.execution import StepContext


async def prepare_billing(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    return StepOutcome({"queue": "billing", "message": inputs["message"]})


HANDLERS = {"prepare_billing": HandlerRegistration(prepare_billing)}
```

The callback must be an async function or async callable. `HandlerRegistration`
also accepts `input_schema`, `output_schema` and `effect`. They are optional;
when given (for example generated from Pydantic models in the host) they must
equal the declaration after canonicalisation, otherwise preparation fails with
`handler_contract_mismatch` naming the handler and the first differing path.
This keeps code and reviewed configuration from drifting apart.

Pass registrations to `prepare_application`:

```python
from pathlib import Path

from foliqant import open_application, prepare_application

prepared = prepare_application(Path("config/settings.yaml"), handlers=HANDLERS)

async with open_application(prepared, environment={}) as app:
    # await app.run(...)
    pass
```

| Situation | Result |
| --- | --- |
| A registration for a handler not declared in settings | `unknown_handler` (field `handlers.<name>`) |
| A registration whose schemas or effect differ from the declaration | `handler_contract_mismatch` |
| A declared handler without a registration | compiles; `open_application` fails with `missing_handler_registration` |

Register the same handler set for tests and production startup.

## Select the handler in YAML

```yaml
# config/routed_intake/billing/prepare.step.yaml
type: handler
handler: prepare_billing
input:
  message:
    pointer: /payload/message
```

The keys under `input` must satisfy the declared input schema. Static checks
reject provable missing, extra, or incompatible values; runtime validation
covers dynamic data before the callback runs. For input
`{"message": "Please check my invoice"}`, this example produces
`{"status": "completed", "result": {"queue": "billing", "message": "Please check my invoice"}}`
at `/flows/billing/steps/prepare`. A deterministic handler needs no model.

## Return completion, review, or failure

`StepOutcome(result)` completes the step after the output schema validates.
`StepOutcome(result, needs_review=True)` records the validated result and stops
the flow for its review route. A handler can say **why** it needs review, which
selects an issue-specific `on_unresolved` target exactly as a decision does:

```python
return StepOutcome(
    {"account_reference": ""},
    needs_review=True,
    unresolved_issues=("no_supported_answer",),
)
```

Issues are `no_supported_answer`, `conflicting_information` and
`multiple_valid_options`; they require `needs_review=True`. A handler may also
return a `selection` (`Selection(CategoryPlan(id), origin)`), which appears as
the step's `selection` record. Its `origin` must be `fallback` exactly when the
step needs review; when the result is a native choice result, the selection must
agree with its answer. Flow routing always comes from configuration.

The runtime maps contract problems to stable errors:

| Condition | Result |
| --- | --- |
| Input fails the declared schema | `invalid_input` |
| Return value is not `StepOutcome`, its result fails the output schema, or its review facts are inconsistent | `invalid_output` |
| Handler exceeds the root deadline | `run_timeout` |
| Handler raises `ServiceError` | That safe error, including `retryable`, is preserved |
| Handler raises another exception | `handler_failed` without raw exception text |

A failed handler fails its flow and the run; it never selects a review route.
Raise `ServiceError` for an expected failure, for example a retryable
`dependency_failure` of a service the handler calls.

Cancellation propagates. Use native async I/O; the runtime does not move a
blocking handler to a worker automatically or prove that external work stopped.
Handlers are in-process code and share the host's trust boundary. The default
root `run_timeout` is 900 seconds and applies to handlers too; see
[execution limits](../configuration/limits.md).

## Use the step context

`StepContext` carries invocation facts, never routing authority:

| Field | Meaning |
| --- | --- |
| `execution_id`, `workflow`, `revision`, `flow_id`, `step_id` | Identity of the run and the operation |
| `caller` | Caller identity context and accepted metadata |
| `deadline`, `model_timeout`, `tool_timeout`, `budget` | Bounds for any work the handler starts |
| `trace` | W3C carrier (`traceparent`, `tracestate`) of the current step span; empty without telemetry |
| `attempt` | The [repeat](../configuration/repeat.md) attempt, `1` when the flow is not repeated |
| `collection_item` | The item ID when running inside a flow collection |
| `flow_role` | `routed`, `callable` (collection) or `retry` |

Forward `trace` on the handler's own outbound requests to keep one end-to-end
trace; see [observability](../integration/observability.md).

The [routing tutorial](../tutorials/multiflow-routing.md) and the
[conditional intake example](https://github.com/sebastianwessel/foliqant/blob/main/examples/conditional_intake/README.md)
contain complete declared handlers. Test handlers with local inputs and assert
both their `StepOutcome` and the public `run_step` record; see
[unit testing](../evaluation/unit-testing.md).
