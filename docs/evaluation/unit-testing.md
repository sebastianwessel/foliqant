# Unit-test integrations

Use unit tests for deterministic contracts: configuration compilation, binding
shapes, handler registration, routing, safe failures, and evaluator behavior.
Use reviewed evaluation suites for model quality. A local fake can prove that
your code handles a response; it cannot prove that a provider will produce that
response.

## Test through the public lifecycle

Register a local async handler and run the real compiled workflow. The matching
step configuration selects `local_classifier` and binds `message`:

```yaml
type: handler
handler: local_classifier
input:
  message:
    pointer: /payload/message
```

The test uses no model or MCP client:

```python
from pathlib import Path

from foliqant import Envelope, open_application, prepare_application
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import freeze_json

CONFIG = Path("config/settings.yaml")

INPUT_SCHEMA = freeze_json(
    {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }
)
OUTPUT_SCHEMA = freeze_json(
    {
        "type": "object",
        "properties": {"category": {"type": "string"}},
        "required": ["category"],
        "additionalProperties": False,
    }
)


async def classify(inputs, context):
    assert context.workflow == "support_triage"
    category = "cancellation" if "cancel" in inputs["message"].lower() else "other"
    return StepOutcome({"category": category})


async def test_local_handler_workflow():
    prepared = prepare_application(
        CONFIG,
        handlers={
            "local_classifier": HandlerRegistration(
                classify,
                INPUT_SCHEMA,
                OUTPUT_SCHEMA,
            )
        },
    )
    async with open_application(prepared, environment={}) as application:
        result = await application.run(
            "support_triage",
            Envelope(payload={"message": "Cancel my renewal."}),
        )

    assert result.execution.status == "completed"
    assert result.flows["triage"].steps["classify"].result == {"category": "cancellation"}
    assert result.execution.usage.model_requests == 0
```

Adjust the asserted flow and step IDs to the compiled workflow. This pattern
tests real input schemas, bindings, handler schemas, projections, transitions,
and the public `ExecutionResult`. Return an invalid value from a second fake to
assert the expected safe `invalid_output` failure. Raise a `ServiceError` to
exercise an intentional operational code; unexpected fake exceptions are
sanitized as dependency failures.

## Test evaluation code with a result fake

When the subject is a scorer or report consumer, return a strict local
`ExecutionResult` and avoid opening an application:

```python
from foliqant import Envelope, ExecutionResult
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    evaluate,
)

USAGE = {
    "model_requests": 0,
    "tool_calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_write_input_tokens": 0,
    "reasoning_output_tokens": 0,
}


async def fake_run(envelope: Envelope) -> ExecutionResult:
    return ExecutionResult.model_validate(
        {
            "payload": {"category": "cancellation"},
            "metadata": envelope.metadata.model_dump(mode="json"),
            "flows": {},
            "transitions": [],
            "execution": {
                "id": "test-run",
                "workflow": "support_triage",
                "revision": "test-revision",
                "status": "completed",
                "usage": USAGE,
            },
        },
        strict=True,
    )


async def test_category_expectation():
    suite = EvaluationSuite(
        "unit",
        "gold-1",
        (
            EvaluationCase(
                "cancellation",
                Envelope(payload={"message": "Cancel renewal."}),
                (Expectation("category", "/payload/category", "cancellation"),),
            ),
        ),
    )
    report = await evaluate(
        suite,
        EvaluationVariant(
            "fake",
            "fake-1",
            fake_run,
            "test-configuration",
            workflow="support_triage",
        ),
    )
    assert report.checks.passed == 1
    assert report.case_pass_rate == 1
```

This verifies evaluator paths, comparisons, and report handling. It does not
exercise the workflow graph.

## Isolate models, tools, and collections deliberately

- For a model step, supply a deterministic local model factory through
  `RuntimePlugins(model_factory=...)`, then call `run_step`. Assert the validated
  result, usage accounting, and safe invalid-output behavior.
- For a handler, register `HandlerRegistration` as above. Assert both input and
  output schema rejection.
- For MCP logic, unit-test the declared catalog and authorizer with a local fake
  session. Do not make a network request in a unit test.
- For a flow collection, pass an already-resolved `items` array to `run_step`.
  Assert ordered child records and `partial_result.items` on failure.

Fakes should be small and deterministic. Keep retry, timeout, cancellation, and
capacity tests separate so one failure has one cause.

## Know what a unit test cannot prove

A fake does not measure model accuracy, provider schema compatibility, remote
tool behavior, latency, token accounting, or production authorization. A
scripted suite proves wiring only. Follow unit tests with `foliqant evaluate
--check`, then run an explicitly authorized live evaluation on reviewed cases
when those properties matter.
