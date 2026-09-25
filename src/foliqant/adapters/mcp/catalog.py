"""Immutable declared MCP catalog validation and result normalization."""

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Never, cast

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import ValidationError as PydanticValidationError

from foliqant.compiler.schema_helpers import validate_confined_tool_schema
from foliqant.contracts.workflow import DeclaredToolCatalog
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenJson, FrozenObject, JsonValue, freeze_json, thaw_json

MAX_TOOL_RESULT_BYTES = 1024 * 1024


def _fail(code: ErrorCode) -> Never:
    raise ServiceError(code) from None


def _frozen_object(value: object, code: ErrorCode) -> FrozenObject:
    try:
        frozen = freeze_json(value)
    except (RecursionError, ServiceError):
        _fail(code)
    if not isinstance(frozen, Mapping):
        _fail(code)
    return frozen


def _canonical_bytes(value: FrozenJson, code: ErrorCode) -> bytes:
    try:
        return json.dumps(
            thaw_json(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeError, ValueError):
        _fail(code)


def _schema_digest(value: object, code: ErrorCode) -> bytes:
    schema = _frozen_object(value, code)
    return hashlib.sha256(_canonical_bytes(schema, code)).digest()


@dataclass(frozen=True, slots=True)
class _DeclaredToolSnapshot:
    input_schema: FrozenObject
    input_digest: bytes
    input_validator: Draft202012Validator
    output_schema: FrozenObject | None
    output_digest: bytes | None
    output_validator: Draft202012Validator | None
    effect: Literal["read", "write"]


class ToolCatalog:
    """Run-scoped immutable copy of an operator-reviewed MCP tool catalog.

    The transport owns discovery and pagination. ``verify`` consumes its bounded
    snapshot and authorizes only the names and exact schemas declared here.
    """

    def __init__(
        self,
        catalog: DeclaredToolCatalog,
        *,
        max_result_bytes: int = MAX_TOOL_RESULT_BYTES,
    ) -> None:
        if (
            not isinstance(max_result_bytes, int)
            or isinstance(max_result_bytes, bool)
            or max_result_bytes < 1
        ):
            _fail(ErrorCode.INVALID_CONFIGURATION)
        try:
            copied = DeclaredToolCatalog.model_validate(
                catalog.model_dump(mode="python"), strict=True
            )
            tools: dict[str, _DeclaredToolSnapshot] = {}
            for name, declared in copied.tools.items():
                input_schema = _frozen_object(
                    declared.input_schema, ErrorCode.INVALID_CONFIGURATION
                )
                input_mutable = cast(dict[str, object], thaw_json(input_schema))
                input_validator = validate_confined_tool_schema(input_mutable)
                output_schema: FrozenObject | None = None
                output_digest: bytes | None = None
                output_validator: Draft202012Validator | None = None
                if declared.output_schema is not None:
                    output_schema = _frozen_object(
                        declared.output_schema, ErrorCode.INVALID_CONFIGURATION
                    )
                    output_mutable = cast(dict[str, object], thaw_json(output_schema))
                    output_validator = validate_confined_tool_schema(output_mutable)
                    output_digest = hashlib.sha256(
                        _canonical_bytes(output_schema, ErrorCode.INVALID_CONFIGURATION)
                    ).digest()
                tools[name] = _DeclaredToolSnapshot(
                    input_schema=input_schema,
                    input_digest=hashlib.sha256(
                        _canonical_bytes(input_schema, ErrorCode.INVALID_CONFIGURATION)
                    ).digest(),
                    input_validator=input_validator,
                    output_schema=output_schema,
                    output_digest=output_digest,
                    output_validator=output_validator,
                    effect=declared.effect,
                )
        except ServiceError:
            raise
        except (PydanticValidationError, RecursionError, SchemaError, TypeError, ValueError):
            _fail(ErrorCode.INVALID_CONFIGURATION)
        self._tools: Mapping[str, _DeclaredToolSnapshot] = MappingProxyType(tools)
        self._names = tuple(sorted(tools))
        self._max_result_bytes = max_result_bytes

    @property
    def names(self) -> tuple[str, ...]:
        """Return the deterministic allowlist captured for this run."""

        return self._names

    def effect(self, name: str) -> Literal["read", "write"]:
        """Return the operator-declared effect policy for an authorized tool."""

        return self._tool(name, ErrorCode.FORBIDDEN).effect

    def input_schema(self, name: str) -> dict[str, JsonValue]:
        """Return a fresh mutable copy of an authorized tool's input schema."""

        frozen = self._tool(name, ErrorCode.FORBIDDEN).input_schema
        return cast(dict[str, JsonValue], thaw_json(frozen))

    def verify(self, discovered: Sequence[Tool]) -> None:
        """Fail closed (``tool_catalog_mismatch``) unless every declared tool has the exact
        discovered schemas."""

        by_name: dict[str, Tool] = {}
        try:
            for tool in discovered:
                if not isinstance(tool, Tool) or tool.name in by_name:
                    _fail(ErrorCode.TOOL_CATALOG_MISMATCH)
                by_name[tool.name] = tool
            for name, declared in self._tools.items():
                actual = by_name.get(name)
                if actual is None:
                    _fail(ErrorCode.TOOL_CATALOG_MISMATCH)
                input_digest = _schema_digest(actual.input_schema, ErrorCode.TOOL_CATALOG_MISMATCH)
                if not hmac.compare_digest(input_digest, declared.input_digest):
                    _fail(ErrorCode.TOOL_CATALOG_MISMATCH)
                if actual.output_schema is None:
                    if declared.output_digest is not None:
                        _fail(ErrorCode.TOOL_CATALOG_MISMATCH)
                else:
                    output_digest = _schema_digest(
                        actual.output_schema, ErrorCode.TOOL_CATALOG_MISMATCH
                    )
                    if declared.output_digest is None or not hmac.compare_digest(
                        output_digest, declared.output_digest
                    ):
                        _fail(ErrorCode.TOOL_CATALOG_MISMATCH)
        except ServiceError:
            raise
        except (AttributeError, RecursionError, TypeError, ValueError):
            _fail(ErrorCode.TOOL_CATALOG_MISMATCH)

    def validate_input(self, name: str, arguments: FrozenObject) -> None:
        """Validate immutable tool arguments using a fixed safe input failure."""

        declared = self._tool(name, ErrorCode.INVALID_INPUT)
        frozen = _frozen_object(arguments, ErrorCode.INVALID_INPUT)
        try:
            declared.input_validator.validate(thaw_json(frozen))
        except ValidationError:
            _fail(ErrorCode.INVALID_INPUT)
        except (LookupError, RecursionError, TypeError, ValueError):
            _fail(ErrorCode.INVALID_CONFIGURATION)

    def validate_result(self, name: str, result: CallToolResult) -> FrozenJson:
        """Validate and normalize one MCP result without exposing server diagnostics."""

        declared = self._tool(name, ErrorCode.FORBIDDEN)
        if not isinstance(result, CallToolResult):
            _fail(ErrorCode.DEPENDENCY_FAILURE)
        if result.is_error:
            # The tool ran and reported failure: a failure, never a tool result.
            _fail(ErrorCode.TOOL_ERROR)
        if "structured_content" in result.model_fields_set:
            if declared.output_validator is None:
                _fail(ErrorCode.INVALID_OUTPUT)
            try:
                frozen = freeze_json(result.structured_content)
                declared.output_validator.validate(thaw_json(frozen))
            except (RecursionError, ServiceError, ValidationError):
                _fail(ErrorCode.INVALID_OUTPUT)
            except (LookupError, TypeError, ValueError):
                _fail(ErrorCode.INVALID_CONFIGURATION)
            if len(_canonical_bytes(frozen, ErrorCode.INVALID_OUTPUT)) > self._max_result_bytes:
                _fail(ErrorCode.TOOL_OUTPUT_LIMIT_EXCEEDED)
            return frozen
        if declared.output_validator is not None:
            _fail(ErrorCode.INVALID_OUTPUT)
        blocks: list[str] = []
        for block in result.content:
            if not isinstance(block, TextContent):
                _fail(ErrorCode.INVALID_OUTPUT)
            blocks.append(block.text)
        text = "\n".join(blocks)
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            _fail(ErrorCode.INVALID_OUTPUT)
        if size > self._max_result_bytes:
            _fail(ErrorCode.TOOL_OUTPUT_LIMIT_EXCEEDED)
        return text

    def _tool(self, name: str, code: ErrorCode) -> _DeclaredToolSnapshot:
        if not isinstance(name, str):
            _fail(code)
        declared = self._tools.get(name)
        if declared is None:
            _fail(code)
        return declared
