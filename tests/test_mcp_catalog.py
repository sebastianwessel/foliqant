"""Offline MCP catalog authorization and validation tests."""

from collections.abc import Callable, Mapping
from typing import cast

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent, Tool

from foliqant.adapters.mcp import ToolCatalog
from foliqant.contracts.workflow import DeclaredToolCatalog
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenObject, freeze_json


def _declared() -> DeclaredToolCatalog:
    return DeclaredToolCatalog.model_validate(
        {
            "tools": {
                "lookup": {
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"$ref": "#/$defs/query"}},
                        "required": ["query"],
                        "additionalProperties": False,
                        "$defs": {"query": {"type": "string", "minLength": 1}},
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {"count": {"type": "integer", "minimum": 0}},
                        "required": ["count"],
                        "additionalProperties": False,
                    },
                    "effect": "read",
                },
                "write_note": {
                    "input_schema": {"type": "object"},
                    "effect": "write",
                },
            }
        },
        strict=True,
    )


def _tools() -> list[Tool]:
    catalog = _declared()
    return [
        Tool(
            name="lookup",
            description="discovered text is not authorization data",
            inputSchema={
                "$defs": {"query": {"minLength": 1, "type": "string"}},
                "additionalProperties": False,
                "required": ["query"],
                "properties": {"query": {"$ref": "#/$defs/query"}},
                "type": "object",
            },
            outputSchema=catalog.tools["lookup"].output_schema,
        ),
        Tool(name="write_note", inputSchema={"type": "object"}),
    ]


def _frozen_object(value: object) -> FrozenObject:
    frozen = freeze_json(value)
    assert isinstance(frozen, Mapping)
    return cast(FrozenObject, frozen)


def _assert_code(code: ErrorCode, operation: Callable[[], object]) -> None:
    with pytest.raises(ServiceError) as raised:
        operation()
    assert raised.value.code == code


def test_catalog_is_an_immutable_independent_snapshot() -> None:
    declared = _declared()
    catalog = ToolCatalog(declared)
    declared.tools["write_note"].input_schema["required"] = ["changed"]

    assert catalog.names == ("lookup", "write_note")
    assert catalog.effect("lookup") == "read"
    assert catalog.effect("write_note") == "write"
    first = catalog.input_schema("write_note")
    first["required"] = ["caller_mutation"]
    assert catalog.input_schema("write_note") == {"type": "object"}


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.invalid/schema.json"},
        {"$ref": "#/$defs/missing"},
        {"$id": "https://example.invalid/schema.json", "type": "object"},
    ],
)
def test_catalog_revalidates_confined_schemas(schema: dict[str, object]) -> None:
    declared = DeclaredToolCatalog.model_validate(
        {"tools": {"bad": {"input_schema": schema, "effect": "read"}}}, strict=True
    )
    _assert_code(ErrorCode.INVALID_CONFIGURATION, lambda: ToolCatalog(declared))


def test_verify_uses_canonical_schema_digests_and_ignores_undeclared_tools() -> None:
    catalog = ToolCatalog(_declared())
    discovered = _tools()
    discovered.append(Tool(name="server_extra", inputSchema={"type": "object"}))

    catalog.verify(discovered)
    _assert_code(
        ErrorCode.INVALID_INPUT,
        lambda: catalog.validate_input("server_extra", _frozen_object({})),
    )
    _assert_code(ErrorCode.FORBIDDEN, lambda: catalog.effect("server_extra"))


@pytest.mark.parametrize("mutation", ["missing", "input", "output", "duplicate"])
def test_verify_fails_closed_without_exposing_discovery_details(mutation: str) -> None:
    catalog = ToolCatalog(_declared())
    tools = _tools()
    if mutation == "missing":
        tools.pop(0)
    elif mutation == "input":
        tools[0].input_schema["required"] = []
    elif mutation == "output":
        assert tools[0].output_schema is not None
        tools[0].output_schema["required"] = []
    else:
        tools.append(_tools()[0])

    _assert_code(ErrorCode.DEPENDENCY_FAILURE, lambda: catalog.verify(tools))


def test_validate_input_uses_the_declared_schema_and_safe_errors() -> None:
    catalog = ToolCatalog(_declared())
    catalog.validate_input("lookup", _frozen_object({"query": "fund"}))

    _assert_code(
        ErrorCode.INVALID_INPUT,
        lambda: catalog.validate_input("lookup", _frozen_object({"query": ""})),
    )
    _assert_code(
        ErrorCode.INVALID_INPUT,
        lambda: catalog.validate_input("secret", _frozen_object({"token": "do-not-echo"})),
    )


def test_structured_result_wins_is_validated_frozen_and_bounded() -> None:
    catalog = ToolCatalog(_declared(), max_result_bytes=64)
    result = catalog.validate_result(
        "lookup",
        CallToolResult(
            structuredContent={"count": 2},
            content=[ImageContent(data="not-returned", mimeType="image/png")],
        ),
    )
    assert result == {"count": 2}
    with pytest.raises(TypeError):
        cast(dict[str, object], result)["count"] = 3

    _assert_code(
        ErrorCode.INVALID_OUTPUT,
        lambda: catalog.validate_result(
            "lookup",
            CallToolResult(structuredContent={"count": -1}, content=[]),
        ),
    )
    _assert_code(
        ErrorCode.INVALID_OUTPUT,
        lambda: catalog.validate_result(
            "lookup",
            CallToolResult(structuredContent={"count": 10**70}, content=[]),
        ),
    )


def test_structured_result_requires_a_declared_output_schema() -> None:
    catalog = ToolCatalog(_declared())
    _assert_code(
        ErrorCode.INVALID_OUTPUT,
        lambda: catalog.validate_result(
            "write_note", CallToolResult(structuredContent={"saved": True}, content=[])
        ),
    )


def test_unstructured_text_is_deterministic_and_non_text_is_rejected() -> None:
    catalog = ToolCatalog(_declared(), max_result_bytes=12)
    assert (
        catalog.validate_result(
            "write_note",
            CallToolResult(content=[TextContent(text="first"), TextContent(text="second")]),
        )
        == "first\nsecond"
    )
    assert catalog.validate_result("write_note", CallToolResult(content=[])) == ""
    _assert_code(
        ErrorCode.INVALID_OUTPUT,
        lambda: catalog.validate_result(
            "write_note", CallToolResult(content=[TextContent(text="thirteen bytes")])
        ),
    )
    _assert_code(
        ErrorCode.INVALID_OUTPUT,
        lambda: catalog.validate_result(
            "write_note",
            CallToolResult(content=[ImageContent(data="binary", mimeType="image/png")]),
        ),
    )


def test_tool_error_is_a_safe_dependency_failure() -> None:
    catalog = ToolCatalog(_declared())
    _assert_code(
        ErrorCode.DEPENDENCY_FAILURE,
        lambda: catalog.validate_result(
            "write_note",
            CallToolResult(content=[TextContent(text="raw server diagnostic")], isError=True),
        ),
    )
