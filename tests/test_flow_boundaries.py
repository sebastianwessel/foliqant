"""Flow boundaries preserve presence and prevent cross-flow schema/tool access."""

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from foliqant.adapters.validation import WorkflowSchemas
from foliqant.bootstrap import WorkflowApplication, _DeclaredReadAuthorizer
from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import FlowResult, TransitionResult, to_execution_result
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import FlowRecord, RunResult, StepRecord, TransitionRecord, Usage
from foliqant.core.json import FrozenObject, freeze_json
from foliqant.core.plan import (
    FlowPlan,
    LlmStepPlan,
    McpStepPlan,
    SchemaResourcePlan,
    SourceLocation,
    TransitionTargetPlan,
    WorkflowPlan,
)


def _frozen(value):
    return cast(FrozenObject, freeze_json(value))


def _flow(name, *, steps=(), input_schema=None):
    return FlowPlan(
        name=name,
        input=(),
        steps=steps,
        input_schema_path=f"{name}/input.json" if input_schema is not None else None,
        input_schema=_frozen(input_schema) if input_schema is not None else None,
        output=None,
        transition=TransitionTargetPlan(outcome="completed"),
        on_unresolved=None,
        location=SourceLocation(f"{name}/flow.yaml", 1, 1),
    )


def _plan(*flows, resources=()):
    return WorkflowPlan(
        name="workflow",
        revision="revision",
        start=flows[0].name,
        default_model=None,
        input_schema_path=None,
        input_schema=None,
        schema_resources=resources,
        output=None,
        flows=flows,
        location=SourceLocation("workflow.yaml", 1, 1),
    )


def _core(flows=(), transitions=()):
    return RunResult(
        execution_id="run",
        workflow="workflow",
        revision="revision",
        status="completed",
        payload=_frozen({"text": "Grüße"}),
        metadata=_frozen({}),
        flows=flows,
        usage=Usage(),
        transitions=transitions,
    )


@pytest.mark.parametrize(
    "status,fields,valid",
    [
        ("completed", {"result": None}, True),
        ("completed", {}, False),
        ("needs_review", {}, True),
        ("needs_review", {"result": None}, True),
        ("skipped", {}, True),
        ("skipped", {"result": None}, False),
        ("skipped", {"elapsed_seconds": 0.0}, False),
        ("failed", {}, False),
        ("cancelled", {"result": "private"}, False),
        ("needs_review", {"error": None}, False),
        ("completed", {"result": None, "selection": {}}, False),
    ],
)
def test_flow_presence_matches_step_rules_without_selection(status, fields, valid):
    raw = {"status": status, "steps": {}, **fields}
    if valid:
        parsed = FlowResult.model_validate(raw, strict=True)
        assert ("result" in parsed.model_dump(mode="json")) == ("result" in fields)
    else:
        with pytest.raises(ValidationError):
            FlowResult.model_validate(raw, strict=True)
    if fields != {"elapsed_seconds": 0.0}:
        assert Draft202012Validator(FlowResult.model_json_schema()).is_valid(raw) == valid


@pytest.mark.parametrize(
    "fields", [{}, {"flow": None}, {"outcome": None}, {"flow": "next", "outcome": "completed"}]
)
def test_transition_requires_one_present_nonnull_target(fields):
    with pytest.raises(ValidationError):
        TransitionResult.model_validate({"source": "first", "reason": "completed", **fields})


def test_public_mapping_preserves_nested_records_without_flattened_alias():
    flow = FlowRecord(
        status="completed",
        steps=(("shared", StepRecord("completed", None, True)),),
        result=None,
        has_result=True,
        usage=Usage(),
        elapsed_seconds=0.2,
    )
    value = _core(
        (("first", flow), ("second", replace(flow, result="Grüße"))),
        (
            TransitionRecord("first", "completed", flow="second"),
            TransitionRecord("second", "completed", outcome="completed"),
        ),
    )
    result = to_execution_result(value).model_dump(mode="json")
    assert "decisions" not in result
    assert result["flows"]["first"]["result"] is None
    assert result["flows"]["second"]["result"] == "Grüße"
    assert result["flows"]["first"]["steps"]["shared"] == {"status": "completed", "result": None}
    assert result["transitions"] == [
        {"source": "first", "reason": "completed", "flow": "second"},
        {"source": "second", "reason": "completed", "outcome": "completed"},
    ]


@pytest.mark.parametrize("duplicate", ["flow", "step"])
def test_duplicate_core_ids_are_rejected_before_mapping_overwrites_them(duplicate):
    entries = (("same", StepRecord("completed", None, True)),)
    flow = FlowRecord("completed", entries * (2 if duplicate == "step" else 1), None, True)
    with pytest.raises(ServiceError) as error:
        to_execution_result(_core((("same", flow),) * (2 if duplicate == "flow" else 1)))
    assert error.value.code == ErrorCode.INVALID_OUTPUT


def test_schema_resolution_is_scoped_by_flow_and_step():
    flows, resources = [], []
    for name, kind in (("first", "string"), ("second", "integer")):
        schema = _frozen({"type": kind})
        path = f"{name}/output.json"
        step = LlmStepPlan(
            "shared",
            "llm",
            SourceLocation(path, 1, 1),
            output_kind="schema",
            output_schema_path=path,
            output_schema=schema,
        )
        flows.append(_flow(name, steps=(step,), input_schema={"type": kind}))
        resources.extend(
            (SchemaResourcePlan(path, schema), SchemaResourcePlan(f"{name}/input.json", schema))
        )
    validator = WorkflowSchemas(_plan(*flows, resources=tuple(resources)))
    for name, value in (("first", "value"), ("second", 42)):
        validator.validate_flow_input(name, value)
        validator.validate_output(name, "shared", value)
        assert Draft202012Validator(validator.provider_output_schema(name, "shared")).is_valid(
            value
        )
    with pytest.raises(ServiceError) as error:
        validator.validate_output("first", "shared", 42)
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    with pytest.raises(ServiceError) as error:
        validator.validate_flow_input("second", "wrong")
    assert error.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(ServiceError) as error:
        validator.validate_flow_input("absent", None)
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


async def test_application_scopes_isolated_flow_and_step_calls():
    calls = []

    class Runner:
        async def run(self, envelope, **kwargs):
            calls.append(("workflow",))
            return _core()

        async def run_flow(self, flow_id, envelope, **kwargs):
            calls.append(("flow", flow_id))
            return _core()

        async def run_step(self, flow_id, step_id, envelope, **kwargs):
            calls.append(("step", flow_id, step_id))
            return _core()

    app = WorkflowApplication({"workflow": Runner()})
    envelope = Envelope(payload={})
    await app.run("workflow", envelope)
    await app.run_flow("workflow", "first", envelope)
    await app.run_step("workflow", "second", "shared", envelope)
    assert calls == [("workflow",), ("flow", "first"), ("step", "second", "shared")]
    await app.aclose()


async def test_default_authorizer_cannot_borrow_same_named_step_from_other_flow():
    first = McpStepPlan(
        "shared", "mcp", SourceLocation("first.yaml", 1, 1), server="server", tool="allowed"
    )
    second = replace(first, tool="other")
    plan = _plan(_flow("first", steps=(first,)), _flow("second", steps=(second,)))
    catalog = SimpleNamespace(
        tools={"allowed": SimpleNamespace(effect="read"), "other": SimpleNamespace(effect="read")}
    )
    prepared = SimpleNamespace(
        plans={"workflow": plan},
        config=SimpleNamespace(mcp={"server": SimpleNamespace(catalog=catalog)}),
    )
    authorizer = _DeclaredReadAuthorizer(prepared)
    context = SimpleNamespace(workflow="workflow", flow_id="first", step_id="shared")
    await authorizer.authorize("server", "allowed", _frozen({}), context)
    with pytest.raises(ServiceError) as error:
        await authorizer.authorize("server", "other", _frozen({}), context)
    assert error.value.code == ErrorCode.FORBIDDEN
    context.flow_id = "absent"
    with pytest.raises(ServiceError):
        await authorizer.authorize("server", "allowed", _frozen({}), context)


def _deployment(tmp_path, *, second_handler="echo"):
    import yaml

    workflow = tmp_path / "workflow"
    shared = tmp_path / "shared"
    workflow.mkdir()
    shared.mkdir()
    definition = {
        "input_schema": {"type": "object", "required": ["message"]},
        "output": {"pointer": "/steps/shared/result"},
        "steps": [
            {
                "id": "shared",
                "definition": {
                    "type": "handler",
                    "handler": "echo",
                    "input": {"message": {"pointer": "/payload/message"}},
                },
            }
        ],
    }
    (shared / "flow.yaml").write_text(yaml.safe_dump(definition))
    second_definition = {
        **definition,
        "steps": [
            {
                "id": "shared",
                "definition": {
                    "type": "handler",
                    "handler": second_handler,
                    "input": {"message": {"pointer": "/payload/message"}},
                },
            }
        ],
    }
    (workflow / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "workflow",
                "start": "first",
                "output": {"pointer": "/flows/second/result", "optional": True, "default": None},
                "flows": {
                    "first": {
                        "definition": "../shared/flow.yaml",
                        "input": {"message": {"pointer": "/payload/message"}},
                        "transition": {"flow": "second"},
                    },
                    "second": {
                        "definition": second_definition,
                        "input": {"message": {"pointer": "/flows/first/result"}},
                        "transition": {"outcome": "completed"},
                    },
                },
            }
        )
    )
    config = tmp_path / "custom-settings.yaml"
    config.write_text("workflows: {workflow: workflow}\n")
    return config


async def test_prepared_application_runs_shared_definition_and_isolated_flow_scopes(tmp_path):
    from foliqant.adapters.handlers import HandlerRegistration
    from foliqant.bootstrap import open_application
    from foliqant.core.execution import StepOutcome
    from foliqant.settings import prepare_application

    calls = []

    async def echo(inputs, context):
        calls.append((context.flow_id, context.step_id))
        return StepOutcome(inputs["message"])

    registration = HandlerRegistration(
        echo, _frozen({"type": "object"}), _frozen({"type": "string"})
    )
    prepared = prepare_application(_deployment(tmp_path), handlers={"echo": registration})
    assert calls == []
    async with open_application(prepared, environment={}) as app:
        envelope = Envelope(payload={"message": "Grüße"})
        full = await app.run("workflow", envelope)
        isolated_flow = await app.run_flow("workflow", "second", envelope)
        isolated_step = await app.run_step("workflow", "second", "shared", envelope)
    assert full.payload == isolated_flow.payload == isolated_step.payload == "Grüße"
    assert full.flows["first"].steps["shared"].result == "Grüße"
    assert full.flows["second"].steps["shared"].result == "Grüße"
    assert len(full.transitions) == 2
    assert isolated_flow.transitions == isolated_step.transitions == []
    assert calls == [
        ("first", "shared"),
        ("second", "shared"),
        ("second", "shared"),
        ("second", "shared"),
    ]


def test_preparation_checks_handler_effects_in_every_flow(tmp_path):
    from foliqant.adapters.handlers import HandlerRegistration
    from foliqant.compiler import CompilationError
    from foliqant.core.execution import StepOutcome
    from foliqant.settings import prepare_application

    async def operation(inputs, context):
        return StepOutcome(None)

    handlers = {
        "echo": HandlerRegistration(operation, _frozen({}), _frozen({})),
        "write": HandlerRegistration(operation, _frozen({}), _frozen({}), effect="write"),
    }
    with pytest.raises(CompilationError):
        prepare_application(_deployment(tmp_path, second_handler="write"), handlers=handlers)


def test_invalid_deployment_reports_the_actual_configuration_filename(tmp_path):
    from foliqant.compiler import CompilationError
    from foliqant.settings import prepare_application

    path = tmp_path / "custom-settings.yaml"
    path.write_text("private_unknown_field: PRIVATE\n")
    with pytest.raises(CompilationError) as error:
        prepare_application(path)
    assert error.value.location.path == "custom-settings.yaml"
    assert "PRIVATE" not in str(error.value)
