"""Strict public execution results mapped from immutable engine values."""

from typing import Annotated, Literal, Self, cast

from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.config import JsonDict
from pydantic.json_schema import SkipJsonSchema

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import (
    Failure,
    RunResult,
    RunStatus,
    StepRecord,
    StepStatus,
    TokenUsage,
)
from foliqant.core.json import JsonValue, thaw_json

from .base import BoundaryModel
from .envelope import Metadata
from .workflow import Id

_NonBlank = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=512, pattern=r".*\S.*"),
]
_Count = Annotated[int, Field(strict=True, ge=0)]


def _omit_default(schema: JsonDict) -> None:
    schema.pop("default", None)


def _parse_error_code(value: object) -> ErrorCode:
    if isinstance(value, ErrorCode):
        return value
    if type(value) is str:
        try:
            return ErrorCode(value)
        except ValueError:
            pass
    raise ValueError("unknown error code")


_ErrorCode = Annotated[ErrorCode, BeforeValidator(_parse_error_code)]


class _ExecutionBoundary(BoundaryModel):
    """Execution models revalidate instances and cannot be reassigned."""

    model_config = ConfigDict(frozen=True, revalidate_instances="always")


class SafeError(_ExecutionBoundary):
    code: _ErrorCode
    message: _NonBlank
    retryable: bool
    location: _NonBlank | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_default
    )

    @model_validator(mode="after")
    def canonical_message_and_optional_location(self) -> Self:
        if self.message != str(ServiceError(self.code)):
            raise ValueError("error message must be canonical")
        if "location" in self.model_fields_set and self.location is None:
            raise ValueError("location must be omitted rather than null")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        if "location" not in self.model_fields_set:
            values.pop("location", None)
        return values


class Usage(_ExecutionBoundary):
    model_requests: _Count
    tool_calls: _Count
    input_tokens: _Count | None
    output_tokens: _Count | None
    cache_read_input_tokens: _Count | None
    cache_write_input_tokens: _Count | None
    reasoning_output_tokens: _Count | None

    @model_validator(mode="after")
    def valid_token_subsets(self) -> Self:
        try:
            TokenUsage(
                self.input_tokens,
                self.output_tokens,
                self.cache_read_input_tokens,
                self.cache_write_input_tokens,
                self.reasoning_output_tokens,
            )
        except ServiceError:
            raise ValueError("invalid token usage") from None
        return self


class ExecutionInfo(_ExecutionBoundary):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {"status": {"enum": ["failed", "cancelled"]}},
                    "required": ["error"],
                },
                {
                    "properties": {"status": {"enum": ["completed", "needs_review"]}},
                    "not": {"required": ["error"]},
                },
            ]
        }
    )

    id: _NonBlank
    workflow: Id
    revision: _NonBlank
    status: RunStatus
    usage: Usage
    error: SafeError | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)

    @model_validator(mode="after")
    def status_matches_error_presence(self) -> Self:
        has_error = "error" in self.model_fields_set
        if has_error and self.error is None:
            raise ValueError("error must be omitted rather than null")
        if self.status in {"failed", "cancelled"}:
            if not has_error:
                raise ValueError("terminal failure status requires an error")
        elif has_error:
            raise ValueError("successful status cannot contain an error")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        if "error" not in self.model_fields_set:
            values.pop("error", None)
        return values


class StepResult(_ExecutionBoundary):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {"status": {"const": "completed"}},
                    "required": ["result"],
                    "not": {"required": ["error"]},
                },
                {
                    "properties": {"status": {"const": "needs_review"}},
                    "not": {"required": ["error"]},
                },
                {
                    "properties": {"status": {"const": "failed"}},
                    "required": ["error"],
                    "not": {"required": ["result"]},
                },
                {
                    "properties": {"status": {"const": "cancelled"}},
                    "not": {"required": ["result"]},
                },
                {
                    "properties": {"status": {"const": "skipped"}},
                    "not": {"anyOf": [{"required": ["result"]}, {"required": ["error"]}]},
                },
            ]
        }
    )

    status: StepStatus
    result: JsonValue = Field(default=None, json_schema_extra=_omit_default)
    error: SafeError | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)

    @model_validator(mode="after")
    def status_matches_optional_fields(self) -> Self:
        has_result = "result" in self.model_fields_set
        has_error = "error" in self.model_fields_set
        if has_error and self.error is None:
            raise ValueError("error must be omitted rather than null")
        if self.status == "completed":
            if not has_result or has_error:
                raise ValueError("completed step requires only a result")
        elif self.status == "needs_review":
            if has_error:
                raise ValueError("review step cannot contain an error")
        elif self.status == "failed":
            if has_result or not has_error:
                raise ValueError("failed step requires only an error")
        elif self.status == "skipped":
            if has_result or has_error:
                raise ValueError("skipped step cannot contain a result or error")
        elif has_result:
            raise ValueError("cancelled step cannot contain a result")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        if "result" not in self.model_fields_set:
            values.pop("result", None)
        if "error" not in self.model_fields_set:
            values.pop("error", None)
        return values


class ExecutionResult(_ExecutionBoundary):
    payload: JsonValue
    metadata: Metadata
    decisions: dict[Id, StepResult]
    execution: ExecutionInfo

    @field_validator("metadata", mode="before")
    @classmethod
    def revalidate_metadata_instance(cls, value: object) -> object:
        if isinstance(value, Metadata):
            try:
                return value.model_dump(mode="json", warnings=False)
            except (TypeError, ValueError):
                raise ValueError("invalid metadata") from None
        return value


class AcceptanceReceipt(_ExecutionBoundary):
    execution_id: _NonBlank
    status: Literal["accepted"]


def _safe_error(failure: Failure) -> dict[str, JsonValue]:
    if not isinstance(failure.code, ErrorCode) or type(failure.retryable) is not bool:
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    return {
        "code": failure.code.value,
        "message": failure.message,
        "retryable": failure.retryable,
    }


def _step_result(record: StepRecord) -> dict[str, JsonValue]:
    if not record.has_result and record.result is not None:
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    value: dict[str, JsonValue] = {"status": record.status}
    if record.has_result:
        value["result"] = thaw_json(record.result)
    if record.error is not None:
        value["error"] = _safe_error(record.error)
    return value


def to_execution_result(value: RunResult) -> ExecutionResult:
    """Copy an immutable core result into its strict public representation."""

    try:
        decisions: dict[str, JsonValue] = {}
        for step_id, record in value.decisions:
            if step_id in decisions:
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            decisions[step_id] = _step_result(record)
        execution: dict[str, JsonValue] = {
            "id": value.execution_id,
            "workflow": value.workflow,
            "revision": value.revision,
            "status": value.status,
            "usage": {
                "model_requests": value.usage.model_requests,
                "tool_calls": value.usage.tool_calls,
                "input_tokens": value.usage.tokens.input_tokens,
                "output_tokens": value.usage.tokens.output_tokens,
                "cache_read_input_tokens": value.usage.tokens.cache_read_input_tokens,
                "cache_write_input_tokens": value.usage.tokens.cache_write_input_tokens,
                "reasoning_output_tokens": value.usage.tokens.reasoning_output_tokens,
            },
        }
        if value.error is not None:
            execution["error"] = _safe_error(value.error)
        return ExecutionResult.model_validate(
            {
                "payload": thaw_json(value.payload),
                "metadata": thaw_json(value.metadata),
                "decisions": decisions,
                "execution": execution,
            },
            strict=True,
        )
    except (ServiceError, TypeError, ValueError, ValidationError):
        raise ServiceError(ErrorCode.INVALID_OUTPUT) from None
