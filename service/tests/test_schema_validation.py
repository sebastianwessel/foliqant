"""Offline JSON Schema adapter acceptance tests."""

import socket
from pathlib import Path
from typing import cast

import pytest

from foliqant.adapters.validation import WorkflowSchemas
from foliqant.compiler import compile_workflow
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenJson, FrozenObject, freeze_json
from foliqant.core.plan import (
    LlmStepPlan,
    SchemaResourcePlan,
    SourceLocation,
    WorkflowPlan,
)


def _frozen(value: object) -> FrozenJson:
    return freeze_json(value)


def _resource(path: str, schema: object) -> SchemaResourcePlan:
    return SchemaResourcePlan(path=path, schema=cast(FrozenObject, _frozen(schema)))


def _plan(
    *,
    input_path: str | None = None,
    input_schema: object | None = None,
    resources: tuple[SchemaResourcePlan, ...] = (),
    output_path: str | None = None,
    output_schema: object | None = None,
) -> WorkflowPlan:
    steps = ()
    if output_path is not None:
        steps = (
            LlmStepPlan(
                name="generate",
                type="llm",
                location=SourceLocation("steps/generate.yaml", 1, 1),
                model="local",
                output_kind="schema",
                output_schema_path=output_path,
                output_schema=cast(FrozenObject, _frozen(output_schema)),
            ),
        )
    return WorkflowPlan(
        name="test",
        revision="a" * 64,
        start="generate" if steps else "done",
        default_model=None,
        input_schema_path=input_path,
        input_schema=(
            cast(FrozenObject, _frozen(input_schema)) if input_path is not None else None
        ),
        schema_resources=resources,
        output=None,
        steps=steps,
        location=SourceLocation("workflow.yaml", 1, 1),
    )


def test_validates_input_with_relative_transitive_resource_and_fragment() -> None:
    root = {"$ref": "defs.json#/$defs/item"}
    definitions = {
        "$defs": {
            "item": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
                "additionalProperties": False,
            }
        }
    }
    schemas = WorkflowSchemas(
        _plan(
            input_path="schemas/root.json",
            input_schema=root,
            resources=(
                _resource("schemas/root.json", root),
                _resource("schemas/defs.json", definitions),
            ),
        )
    )

    schemas.validate_input(_frozen({"id": 7}))
    with pytest.raises(ServiceError) as error:
        schemas.validate_input(_frozen({"id": "PRIVATE SECRET"}))
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert str(error.value) == "The input does not satisfy the required contract."
    assert "PRIVATE SECRET" not in str(error.value)


def test_validates_step_output_and_rejects_unconfigured_step() -> None:
    root = {"$ref": "#/$defs/result", "$defs": {"result": {"const": "ok"}}}
    schemas = WorkflowSchemas(
        _plan(
            resources=(_resource("schemas/result.json", root),),
            output_path="schemas/result.json",
            output_schema=root,
        )
    )

    schemas.validate_output("generate", _frozen("ok"))
    with pytest.raises(ServiceError) as error:
        schemas.validate_output("generate", _frozen("PRIVATE BAD OUTPUT"))
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert "PRIVATE BAD OUTPUT" not in str(error.value)
    with pytest.raises(ServiceError) as error:
        schemas.validate_output("unknown", _frozen(None))
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


def test_boolean_schemas_are_supported() -> None:
    allow = cast(FrozenObject, _frozen(True))
    deny = cast(FrozenObject, _frozen(False))
    plan = _plan(
        input_path="allow.json",
        input_schema=True,
        resources=(
            SchemaResourcePlan("allow.json", allow),
            SchemaResourcePlan("deny.json", deny),
        ),
        output_path="deny.json",
        output_schema=False,
    )
    schemas = WorkflowSchemas(plan)

    schemas.validate_input(_frozen({"anything": [1, True, None]}))
    with pytest.raises(ServiceError) as error:
        schemas.validate_output("generate", _frozen(None))
    assert error.value.code == ErrorCode.INVALID_OUTPUT


def test_schema_annotation_data_is_literal_but_referenced_data_becomes_schema() -> None:
    annotation = {"const": {"$ref": "https://unreachable.invalid/schema"}}
    annotation_schemas = WorkflowSchemas(
        _plan(
            input_path="annotation.json",
            input_schema=annotation,
            resources=(_resource("annotation.json", annotation),),
        )
    )
    annotation_schemas.validate_input(_frozen({"$ref": "https://unreachable.invalid/schema"}))

    promoted = {"$ref": "#/default", "default": {"type": "string"}}
    promoted_schemas = WorkflowSchemas(
        _plan(
            input_path="promoted.json",
            input_schema=promoted,
            resources=(_resource("promoted.json", promoted),),
        )
    )
    promoted_schemas.validate_input(_frozen("accepted"))
    with pytest.raises(ServiceError) as error:
        promoted_schemas.validate_input(_frozen(4))
    assert error.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "missing.json"},
        {"$ref": "https://unreachable.invalid/schema"},
        {"$ref": "#/const", "const": 17},
    ],
)
def test_invalid_schema_reference_fails_at_adapter_construction(schema: object) -> None:
    plan = _plan(
        input_path="root.json",
        input_schema=schema,
        resources=(_resource("root.json", schema),),
    )
    with pytest.raises(ServiceError) as error:
        WorkflowSchemas(plan)
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION
    assert str(error.value) == "The workflow configuration is invalid."


def test_compiled_schemas_need_no_filesystem_or_network_after_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "steps").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "workflow.yaml").write_text(
        "version: 1\nname: test\nstart: done\ninput_schema: schemas/root.json\n"
    )
    (tmp_path / "steps/done.yaml").write_text("type: finish\noutcome: completed\n")
    (tmp_path / "schemas/root.json").write_text('{"$ref":"defs.json"}')
    definitions = tmp_path / "schemas/defs.json"
    definitions.write_text('{"const":"original"}')
    plan = compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    schemas = WorkflowSchemas(plan)
    definitions.write_text('{"const":"mutated"}')

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("validation attempted external access")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    schemas.validate_input(_frozen("original"))
    with pytest.raises(ServiceError) as error:
        schemas.validate_input(_frozen("mutated"))
    assert error.value.code == ErrorCode.INVALID_INPUT
