"""Declared handler contracts, registration checks, review facts and step context."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from foliqant import Envelope, open_application, prepare_application
from foliqant.adapters.handlers import HandlerExecutor, HandlerRegistration
from foliqant.compiler import CompilationError
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import Selection, StepOutcome
from foliqant.core.plan import CategoryPlan, HandlerStepPlan, SourceLocation

_INPUT = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}
_OUTPUT = {
    "type": "object",
    "properties": {"queue": {"type": "string", "enum": ["billing", "general"]}},
    "required": ["queue"],
    "additionalProperties": False,
}


def _project(tmp_path: Path, *, output_schema: str = "contracts/route.output.json") -> Path:
    config = tmp_path / "config"
    (config / "contracts").mkdir(parents=True)
    (config / "contracts/route.input.json").write_text(json.dumps(_INPUT))
    (config / "contracts/route.output.json").write_text(json.dumps(_OUTPUT))
    (config / "settings.yaml").write_text(
        "handlers:\n"
        "  route_message:\n"
        "    input_schema: contracts/route.input.json\n"
        f"    output_schema: {output_schema}\n"
        "    effect: read\n"
    )
    workflow = config / "demo"
    workflow.mkdir()
    (workflow / "workflow.yaml").write_text(
        json.dumps(
            {
                "flows": {
                    "main": {
                        "input": {"message": {"pointer": "/payload/message"}},
                        "definition": {
                            "output": {"pointer": "/steps/route/result/queue"},
                            "steps": [
                                {
                                    "id": "route",
                                    "definition": {
                                        "type": "handler",
                                        "handler": "route_message",
                                        "input": {"message": {"pointer": "/payload/message"}},
                                    },
                                }
                            ],
                        },
                        "transition": {
                            "binding": {"pointer": "/flows/main/result"},
                            "cases": {"billing": {"outcome": "completed"}},
                            "default": {"outcome": "needs_review"},
                        },
                        "on_unresolved": {
                            "default": {"outcome": "needs_review"},
                        },
                    }
                },
                "output": {"pointer": "/flows/main/result"},
            }
        )
    )
    return config / "settings.yaml"


async def _route(inputs, context):
    return StepOutcome({"queue": "billing" if "invoice" in inputs["message"] else "general"})


def test_declared_contracts_compile_and_check_without_registrations(tmp_path):
    prepared = prepare_application(_project(tmp_path))
    assert set(prepared.handler_contracts) == {"route_message"}
    assert prepared.handlers == {}
    step = prepared.plans["demo"].flow("main").step("route")
    assert isinstance(step, HandlerStepPlan)
    # The declared output enum drives route coverage checks offline.
    uncovered = [item for item in prepared.diagnostics if item.code == "uncovered_value"]
    assert len(uncovered) == 1 and "general" in uncovered[0].message


def test_cli_validates_and_explains_workflows_with_declared_handlers(tmp_path):
    settings = _project(tmp_path)
    for command in (["validate"], ["explain"], ["doctor"]):
        completed = subprocess.run(
            [sys.executable, "-m", "foliqant", *command, "--config", str(settings)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    strict = subprocess.run(
        [sys.executable, "-m", "foliqant", "validate", "--strict", "--config", str(settings)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert strict.returncode == 2
    failure = json.loads(strict.stdout)
    assert failure["error"]["reason"] == "uncovered_value"
    assert [item["code"] for item in failure["problems"]] == ["uncovered_value"]
    assert [item["code"] for item in failure["diagnostics"]] == ["uncovered_value"]
    first, summary = strict.stderr.splitlines()
    assert ": uncovered_value at flows." in first and first.endswith(")")
    assert summary == "foliqant: invalid_configuration: 1 problem."


def test_strict_preparation_raises_on_warnings(tmp_path):
    with pytest.raises(CompilationError) as error:
        prepare_application(_project(tmp_path), strict=True)
    assert error.value.reason == "uncovered_value"
    assert (error.value.location.line, error.value.location.column) != (1, 1)


async def test_registration_supplies_the_callable_and_declared_schemas_apply(tmp_path):
    prepared = prepare_application(
        _project(tmp_path), handlers={"route_message": HandlerRegistration(_route)}
    )
    registration = prepared.handlers["route_message"]
    assert registration.output_schema is not None and dict(registration.output_schema)
    async with open_application(prepared, environment={}) as app:
        result = await app.run("demo", Envelope(payload={"message": "invoice 42"}))
        assert result.payload == "billing"
        rejected = await app.run("demo", Envelope(payload={"message": 42}))
        assert rejected.execution.error is not None
        assert rejected.execution.error.code == ErrorCode.INVALID_INPUT


async def test_declared_handler_without_registration_fails_at_activation(tmp_path):
    prepared = prepare_application(_project(tmp_path))
    with pytest.raises(CompilationError) as error:
        async with open_application(prepared, environment={}):
            pass
    assert error.value.reason == "missing_handler_registration"
    assert error.value.field == "handlers.route_message"


def test_cli_run_reports_missing_registrations(tmp_path):
    settings = _project(tmp_path)
    envelope = tmp_path / "envelope.json"
    envelope.write_text('{"payload": {"message": "invoice"}}')
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "foliqant",
            "run",
            "--config",
            str(settings),
            "--workflow",
            "demo",
            "--input",
            str(envelope),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error"]["reason"] == "missing_handler_registration"
    assert "settings.yaml:" in completed.stderr
    assert "missing_handler_registration at handlers.route_message" in completed.stderr


@pytest.mark.parametrize(
    "registration,field",
    [
        (
            HandlerRegistration(_route, _INPUT, {**_OUTPUT, "additionalProperties": True}),
            "handlers.route_message.output_schema/additionalProperties",
        ),
        (
            HandlerRegistration(
                _route,
                {**_INPUT, "properties": {"message": {"type": "integer"}}},
                _OUTPUT,
            ),
            "handlers.route_message.input_schema/properties/message/type",
        ),
        (HandlerRegistration(_route, effect="write"), "handlers.route_message.effect"),
    ],
)
def test_registration_must_match_its_declaration(tmp_path, registration, field):
    with pytest.raises(CompilationError) as error:
        prepare_application(_project(tmp_path), handlers={"route_message": registration})
    assert error.value.reason == "handler_contract_mismatch"
    assert error.value.field == field


def test_matching_registration_schemas_are_accepted(tmp_path):
    registration = HandlerRegistration(_route, _INPUT, _OUTPUT)
    prepared = prepare_application(_project(tmp_path), handlers={"route_message": registration})
    assert set(prepared.handlers) == {"route_message"}


def test_registration_schemas_compare_json_numbers_by_value(tmp_path):
    settings = _project(tmp_path)

    def schema(length: object, level: object) -> dict[str, object]:
        properties = {"message": {"type": "string", "maxLength": length}, "level": {"const": level}}
        return {**_INPUT, "properties": properties}

    (settings.parent / "contracts/route.input.json").write_text(json.dumps(schema(20, 1)))
    # A generator emitting `20.0` declares the same JSON Schema as `20`.
    generated = HandlerRegistration(_route, schema(20.0, 1.0), _OUTPUT)
    prepare_application(settings, handlers={"route_message": generated})
    # Booleans never equal numbers.
    flagged = HandlerRegistration(_route, schema(20, True), _OUTPUT)
    with pytest.raises(CompilationError) as error:
        prepare_application(settings, handlers={"route_message": flagged})
    assert error.value.reason == "handler_contract_mismatch"
    assert error.value.field == "handlers.route_message.input_schema/properties/level/const"


def test_undeclared_registrations_are_unknown(tmp_path):
    with pytest.raises(CompilationError) as error:
        prepare_application(
            _project(tmp_path),
            handlers={
                "route_message": HandlerRegistration(_route),
                "other": HandlerRegistration(_route),
            },
        )
    assert error.value.reason == "unknown_handler"
    assert error.value.field == "handlers.other"
    assert "handlers" in error.value.hint


@pytest.mark.parametrize("reference", ["../outside.json", "/etc/passwd", "missing.json"])
def test_declared_schema_paths_stay_inside_the_configuration(tmp_path, reference):
    (tmp_path / "outside.json").write_text(json.dumps(_OUTPUT))
    with pytest.raises(CompilationError) as error:
        prepare_application(_project(tmp_path, output_schema=reference))
    assert error.value.reason == "invalid_handler_schema"
    assert error.value.field == "handlers.route_message.output_schema"


def test_declared_schemas_must_be_self_contained(tmp_path):
    settings = _project(tmp_path)
    (settings.parent / "contracts/route.output.json").write_text(json.dumps({"$ref": "other.json"}))
    with pytest.raises(CompilationError) as error:
        prepare_application(settings)
    assert error.value.reason == "invalid_handler_schema"


def test_declared_contract_changes_revise_the_configuration(tmp_path):
    settings = _project(tmp_path)
    before = prepare_application(settings).configuration_digest
    output = settings.parent / "contracts/route.output.json"
    output.write_text(json.dumps({**_OUTPUT, "description": "Queue selection."}))
    assert prepare_application(settings).configuration_digest != before


# Adapter regression: review facts and selections are forwarded ----------------


def _handler_step() -> HandlerStepPlan:
    return HandlerStepPlan(
        name="route", type="handler", location=SourceLocation("flow.yaml", 1, 1), handler="h"
    )


class _Context:
    step_id = "route"
    deadline = float("inf")


@pytest.mark.parametrize(
    "outcome",
    [
        StepOutcome(
            {"queue": "general"}, needs_review=True, unresolved_issues=("no_supported_answer",)
        ),
        StepOutcome(
            {"queue": "general"},
            needs_review=True,
            selection=Selection(CategoryPlan("general"), "fallback"),
            unresolved_issues=("multiple_valid_options",),
        ),
        StepOutcome({"queue": "billing"}, selection=Selection(CategoryPlan("billing"), "model")),
    ],
)
async def test_handler_adapter_forwards_selection_and_unresolved_issues(outcome):
    async def respond(inputs, context):
        return outcome

    executor = HandlerExecutor({"h": HandlerRegistration(respond, _INPUT, _OUTPUT)})
    result = await executor.execute(_handler_step(), {"message": "x"}, _Context())  # type: ignore[arg-type]
    assert result.unresolved_issues == outcome.unresolved_issues
    assert result.selection == outcome.selection
    assert result.needs_review == outcome.needs_review


def test_unresolved_registration_is_rejected_by_the_executor():
    with pytest.raises(ServiceError) as error:
        HandlerExecutor({"h": HandlerRegistration(_route)})
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


async def test_handler_selection_and_issues_reach_public_results_and_routes(tmp_path):
    async def unsure(inputs, context):
        return StepOutcome(
            {"queue": "general"},
            needs_review=True,
            selection=Selection(CategoryPlan("general", "General queue."), "fallback"),
            unresolved_issues=("no_supported_answer",),
        )

    settings = _project(tmp_path)
    workflow_file = settings.parent / "demo/workflow.yaml"
    workflow = json.loads(workflow_file.read_text())
    workflow["flows"]["main"]["on_unresolved"] = {
        "default": {"outcome": "needs_review"},
        "no_supported_answer": {"flow": "triage"},
    }
    workflow["flows"]["triage"] = {
        "input": {"queue": {"pointer": "/flows/main/result", "default": None}},
        "definition": {
            "steps": [
                {
                    "id": "note",
                    "definition": {
                        "type": "handler",
                        "handler": "route_message",
                        "input": {"message": {"literal": "triage"}},
                    },
                }
            ]
        },
        "transition": {"outcome": "needs_review"},
        "on_unresolved": {"outcome": "needs_review"},
    }
    workflow["start"] = "main"
    workflow_file.write_text(json.dumps(workflow))
    prepared = prepare_application(
        settings, handlers={"route_message": HandlerRegistration(unsure)}
    )
    async with open_application(prepared, environment={}) as app:
        result = await app.run("demo", Envelope(payload={"message": "hello"}))
    step = result.flows["main"].steps["route"]
    assert step.status == "needs_review"
    assert step.selection is not None and step.selection.origin == "fallback"
    assert result.transitions[0].flow == "triage"
    assert result.transitions[0].route.case == "no_supported_answer"


async def test_handler_context_carries_role_attempt_and_trace(tmp_path):
    seen = []

    async def record(inputs, context):
        seen.append((context.flow_role, context.attempt, context.collection_item, context.trace))
        return StepOutcome({"queue": "billing"})

    prepared = prepare_application(
        _project(tmp_path), handlers={"route_message": HandlerRegistration(record)}
    )
    async with open_application(prepared, environment={}) as app:
        await app.run("demo", Envelope(payload={"message": "invoice"}))
    assert seen == [("routed", 1, None, {})]
