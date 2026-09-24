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

from foliqant.contracts.decisions import ChoiceResult
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import (
    Failure,
    FlowRecord,
    RunResult,
    RunStatus,
    StepRecord,
    StepStatus,
    TokenUsage,
    flow_record_value,
    step_record_value,
    usage_value,
)
from foliqant.core.json import JsonValue, thaw_json

from .base import BoundaryModel
from .envelope import Metadata
from .identifiers import Id

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


_Cost = Annotated[float, Field(ge=0, allow_inf_nan=False)]
_COST_FIELDS = ("cost", "cost_complete", "currency", "reference_model")


class _PricedUsage(_ExecutionBoundary):
    """Cost fields, present exactly when configured pricing applied to a request.

    ``cost`` is rounded to six decimals, or ``null`` with ``cost_complete: false``
    when a count the estimate needs was not reported or a request was unpriced.
    """

    cost: _Cost | None | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)
    cost_complete: bool | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_default
    )
    currency: Literal["USD"] | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_default
    )
    reference_model: _NonBlank | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_default
    )

    @model_validator(mode="after")
    def cost_fields_together(self) -> Self:
        present = self.model_fields_set
        priced = {"cost", "cost_complete", "currency"}
        if priced & present and not priced <= present:
            raise ValueError("cost, cost_complete and currency occur together")
        if "reference_model" in present and ("currency" not in present or not self.reference_model):
            raise ValueError("reference_model requires a priced estimate")
        if "currency" in present:
            if self.currency is None or self.cost_complete is None:
                raise ValueError("currency and cost_complete cannot be null")
            if self.cost_complete != (self.cost is not None):
                raise ValueError("cost_complete must describe cost presence")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        # Counts first, then the estimate, then the per-model split.
        trailing = (*_COST_FIELDS, "by_model")
        ordered = {name: value for name, value in values.items() if name not in trailing}
        for name in trailing:
            if name in self.model_fields_set and name in values:
                ordered[name] = values[name]
        return ordered


class ModelUsage(_PricedUsage):
    """Usage of one provider model ID; ``null`` counts were not reported."""

    requests: Annotated[int, Field(strict=True, ge=1)]
    input_tokens: _Count | None
    cached_input_tokens: _Count | None
    output_tokens: _Count | None
    reasoning_tokens: _Count | None

    @model_validator(mode="after")
    def valid_token_subsets(self) -> Self:
        try:
            TokenUsage(
                self.input_tokens,
                self.output_tokens,
                self.cached_input_tokens,
                None,
                self.reasoning_tokens,
            )
        except ServiceError:
            raise ValueError("invalid token usage") from None
        return self


class Usage(_PricedUsage):
    """Measured totals; ``by_model`` splits model requests by provider model ID.

    ``by_model`` is omitted when no model request was made; when present it
    accounts for every model request. Cost fields sum the priced models and are
    omitted when no model is priced.
    """

    model_requests: _Count
    tool_calls: _Count
    input_tokens: _Count | None
    output_tokens: _Count | None
    cache_read_input_tokens: _Count | None
    cache_write_input_tokens: _Count | None
    reasoning_output_tokens: _Count | None
    by_model: (
        Annotated[
            dict[Annotated[str, StringConstraints(min_length=1, max_length=512)], ModelUsage],
            Field(min_length=1),
        ]
        | SkipJsonSchema[None]
    ) = Field(default=None, json_schema_extra=_omit_default)

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
        if "by_model" in self.model_fields_set and self.by_model is None:
            raise ValueError("by_model must be omitted rather than null")
        if self.by_model is not None and self.model_requests != sum(
            item.requests for item in self.by_model.values()
        ):
            raise ValueError("by_model must account for every model request")
        if "currency" in self.model_fields_set and not any(
            item.currency is not None for item in (self.by_model or {}).values()
        ):
            raise ValueError("a total cost requires a priced model")
        return self


class TraceIds(_ExecutionBoundary):
    """OpenTelemetry identifiers of the run span, for host log correlation."""

    trace_id: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{32}$")]
    span_id: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{16}$")]


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
    trace: TraceIds | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)

    @model_validator(mode="after")
    def status_matches_error_presence(self) -> Self:
        has_error = "error" in self.model_fields_set
        if has_error and self.error is None:
            raise ValueError("error must be omitted rather than null")
        if "trace" in self.model_fields_set and self.trace is None:
            raise ValueError("trace must be omitted rather than null")
        if self.status in {"failed", "cancelled"}:
            if not has_error:
                raise ValueError("terminal failure status requires an error")
        elif has_error:
            raise ValueError("successful status cannot contain an error")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        for name in ("error", "trace"):
            if name not in self.model_fields_set:
                values.pop(name, None)
        return values


class SelectedCategory(_ExecutionBoundary):
    """Exact configured category identity; descriptions retain authored whitespace."""

    id: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"\S")]
    description: (
        Annotated[str, StringConstraints(strict=True, min_length=1)] | SkipJsonSchema[None]
    ) = Field(default=None, json_schema_extra=_omit_default)

    @model_validator(mode="after")
    def omitted_description_is_not_null(self) -> Self:
        if "description" in self.model_fields_set and self.description is None:
            raise ValueError("description must be omitted rather than null")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        if "description" not in self.model_fields_set:
            values.pop("description", None)
        return values


class StepSelection(_ExecutionBoundary):
    """Effective classification without changing the native answer or uncertainty."""

    category: SelectedCategory
    origin: Literal["model", "fallback"]


def _step_schema_extra(schema: JsonDict) -> None:
    """Attach presence rules using Pydantic's actual recursive definition reference."""
    schema.update(
        {
            "allOf": [
                {
                    "if": {"required": ["partial_result"]},
                    "then": {
                        "required": ["kind"],
                        "properties": {
                            "status": {"const": "failed"},
                            "kind": {"const": "flow_collection"},
                        },
                    },
                },
                {
                    "if": {
                        "required": ["kind"],
                        "properties": {"status": {"enum": ["completed", "needs_review"]}},
                    },
                    "then": {
                        "required": ["result"],
                        "properties": {"result": {"$ref": "#/$defs/FlowCollectionResult"}},
                    },
                },
                {
                    "if": {"required": ["selection"]},
                    "then": {
                        "required": ["result"],
                        "properties": {"status": {"enum": ["completed", "needs_review"]}},
                    },
                },
            ],
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
                {"properties": {"status": {"const": "cancelled"}}, "not": {"required": ["result"]}},
                {
                    "properties": {"status": {"const": "skipped"}},
                    "not": {"anyOf": [{"required": ["result"]}, {"required": ["error"]}]},
                },
            ],
        }
    )
    rules = cast(list[JsonDict], schema["allOf"])
    then = cast(JsonDict, rules[1]["then"])
    properties = cast(JsonDict, then["properties"])
    fields = cast(JsonDict, schema["properties"])
    properties["result"] = dict(cast(JsonDict, fields["partial_result"]))


class StepResult(_ExecutionBoundary):
    model_config = ConfigDict(json_schema_extra=_step_schema_extra)

    status: StepStatus
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    usage: Usage | None = None
    result: JsonValue = Field(default=None, json_schema_extra=_omit_default)
    selection: StepSelection | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_default
    )
    error: SafeError | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)
    partial_result: "FlowCollectionResult | SkipJsonSchema[None]" = Field(
        default=None, json_schema_extra=_omit_default
    )
    kind: Literal["flow_collection"] | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_default
    )

    @model_validator(mode="after")
    def status_matches_optional_fields(self) -> Self:
        if "partial_result" in self.model_fields_set and (
            self.status != "failed" or self.partial_result is None or self.kind != "flow_collection"
        ):
            raise ValueError("partial result requires a failed step and cannot be null")
        if "kind" in self.model_fields_set and self.kind is None:
            raise ValueError("kind must be omitted rather than null")
        if self.kind == "flow_collection" and self.status in {"completed", "needs_review"}:
            FlowCollectionResult.model_validate(self.result, strict=True)
        has_result = "result" in self.model_fields_set
        if "selection" in self.model_fields_set:
            if self.selection is None or not has_result:
                raise ValueError("selection requires a result and cannot be null")
            expected_status = "completed" if self.selection.origin == "model" else "needs_review"
            if self.status != expected_status:
                raise ValueError("selection origin must match step status")
        # A native choice result (decision steps) must agree with its selection;
        # a trusted handler may attach a selection to its own result shape.
        if (
            "selection" in self.model_fields_set
            and self.selection is not None
            and isinstance(self.result, dict)
            and self.result.get("type") == "choice"
        ):
            ChoiceResult.model_validate(self.result, strict=True)
            answerability = self.result.get("answerability")
            if not isinstance(answerability, dict):
                raise ValueError("selection requires native answerability")
            if self.selection.origin == "model":
                answer = self.result.get("answer")
                if (
                    answerability.get("status") != "answerable"
                    or not isinstance(answer, dict)
                    or answer.get("optionId") != self.selection.category.id
                ):
                    raise ValueError("model selection must match the native answer")
            elif (
                answerability.get("status") != "not_answerable"
                or not answerability.get("issues")
                or self.result.get("answer") is not None
            ):
                raise ValueError("fallback selection requires a native unanswered result")
        has_error = "error" in self.model_fields_set
        if has_error and self.error is None:
            raise ValueError("error must be omitted rather than null")
        if self.status == "skipped" and (
            self.elapsed_seconds is not None or self.usage is not None
        ):
            raise ValueError("skipped steps have no execution measurements")
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
        if "selection" not in self.model_fields_set:
            values.pop("selection", None)
        if "error" not in self.model_fields_set:
            values.pop("error", None)
        if "partial_result" not in self.model_fields_set:
            values.pop("partial_result", None)
        if "kind" not in self.model_fields_set:
            values.pop("kind", None)
        if self.elapsed_seconds is None:
            values.pop("elapsed_seconds", None)
        if self.usage is None:
            values.pop("usage", None)
        return values


_FLOW_STATUS_RULES: JsonDict = {
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


class _FlowRun(_ExecutionBoundary):
    """One execution of a flow: scoped step records and a projected result."""

    model_config = ConfigDict(json_schema_extra=_FLOW_STATUS_RULES)

    status: StepStatus
    steps: dict[Id, StepResult]
    result: JsonValue = Field(default=None, json_schema_extra=_omit_default)
    usage: Usage | None = None
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    error: SafeError | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)

    @model_validator(mode="after")
    def status_matches_optional_fields(self) -> Self:
        # Reuse the step presence and measurement rules without admitting selection.
        StepResult.model_validate(
            {
                name: getattr(self, name)
                for name in self.model_fields_set
                if name in StepResult.model_fields
            },
            strict=True,
        )
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        for name in ("result", "error", "attempts", "repeat", "retry"):
            if name not in self.model_fields_set:
                values.pop(name, None)
        for name in ("usage", "elapsed_seconds", "attempts_usage", "attempts_elapsed_seconds"):
            if getattr(self, name, None) is None:
                values.pop(name, None)
        return values


class FlowAttempt(_FlowRun):
    """One attempt of a repeated flow, or one run of a retry flow."""

    attempt: Annotated[int, Field(strict=True, ge=1, le=64)]


class RepeatInfo(_ExecutionBoundary):
    """Why a repeated flow stopped; exhaustion is not a review."""

    stopped_by: Literal["until", "exhausted", "continue_when", "review", "failure"]


class FlowResult(_FlowRun):
    """A flow's scoped step records and explicitly present projected result.

    ``attempt_count`` counts executions (zero when skipped). Repeated and retry
    flows list every run in ``attempts``; the top-level fields describe the last
    run and ``attempts_usage`` / ``attempts_elapsed_seconds`` sum all runs.
    """

    attempt_count: Annotated[int, Field(strict=True, ge=0, le=64)]
    attempts: (
        Annotated[list[FlowAttempt], Field(min_length=1, max_length=64)] | SkipJsonSchema[None]
    ) = Field(default=None, json_schema_extra=_omit_default)
    attempts_usage: Usage | None = None
    attempts_elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    repeat: RepeatInfo | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)

    @model_validator(mode="after")
    def attempts_match_count(self) -> Self:
        for name in ("attempts", "repeat"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError("absent attempt facts are omitted rather than null")
        if self.attempts is not None:
            if len(self.attempts) != self.attempt_count or [
                item.attempt for item in self.attempts
            ] != list(range(1, len(self.attempts) + 1)):
                raise ValueError("attempts must be numbered from one and match the count")
        elif self.attempt_count != (0 if self.status == "skipped" else 1):
            raise ValueError("a flow without attempts ran once or was skipped")
        return self


class FlowCollectionItem(_ExecutionBoundary):
    """One explicit invocation request; the selected step restricts its flow target."""

    id: Id
    flow: Id
    input: dict[str, JsonValue]


class FlowCollectionItemResult(FlowResult):
    """One collection item's complete flow record, including skipped local steps.

    A callable flow with ``repeat`` carries its attempts like a routed flow;
    ``retry`` records the item's retry flow runs (last run plus ``attempts``).
    """

    id: Id
    flow: Id
    retry: FlowResult | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)

    @model_validator(mode="after")
    def retry_is_omitted_rather_than_null(self) -> Self:
        if "retry" in self.model_fields_set and self.retry is None:
            raise ValueError("retry must be omitted rather than null")
        return self


class FlowCollectionResult(_ExecutionBoundary):
    """Ordered child invocation ledger; each item identity occurs exactly once."""

    items: Annotated[list[FlowCollectionItemResult], Field(max_length=1024)]

    @model_validator(mode="after")
    def unique_item_ids(self) -> Self:
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("collection item IDs must be unique")
        return self


StepResult.model_rebuild()
FlowAttempt.model_rebuild()
FlowResult.model_rebuild()
FlowCollectionItemResult.model_rebuild()


class RouteSelection(_ExecutionBoundary):
    """Which configuration form selected a target.

    ``index`` is the selected ``route`` entry; ``case`` is the matched case key
    (``cases``) or the review issue that selected an issue-specific target.
    Both are omitted when they do not apply, for example for ``default``.
    """

    kind: Literal["direct", "cases", "route", "review"]
    index: Annotated[int, Field(strict=True, ge=0, le=31)] | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_default
    )
    case: _NonBlank | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        for name in ("index", "case"):
            if name not in self.model_fields_set:
                values.pop(name, None)
        return values


class TransitionResult(_ExecutionBoundary):
    """One recorded workflow boundary with exactly one authored target."""

    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {"required": ["flow"], "not": {"required": ["outcome"]}},
                {"required": ["outcome"], "not": {"required": ["flow"]}},
            ]
        }
    )

    source: Id
    reason: Literal["completed", "needs_review"]
    flow: Id | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)
    outcome: Literal["completed", "needs_review"] | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_default
    )
    route: RouteSelection

    @model_validator(mode="after")
    def exactly_one_target(self) -> Self:
        if ("flow" in self.model_fields_set) == ("outcome" in self.model_fields_set):
            raise ValueError("transition requires exactly one target")
        if ("flow" in self.model_fields_set and self.flow is None) or (
            "outcome" in self.model_fields_set and self.outcome is None
        ):
            raise ValueError("transition target cannot be null")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        for name in ("flow", "outcome"):
            if name not in self.model_fields_set:
                values.pop(name, None)
        return values


class StartRoute(_ExecutionBoundary):
    """Which ``start`` form selected the first flow; ``index`` for a routed start."""

    kind: Literal["direct", "route"]
    index: Annotated[int, Field(strict=True, ge=0, le=31)] | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_default
    )

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        if "index" not in self.model_fields_set:
            values.pop("index", None)
        return values


class StartResult(_ExecutionBoundary):
    """The first flow of a workflow run and how it was selected."""

    flow: Id
    route: StartRoute


class ExecutionResult(_ExecutionBoundary):
    """One run: projected payload, flow records, transitions and terminal facts.

    ``start`` is present for workflow runs and omitted for isolated flow or step
    runs, which have no start selection.
    """

    payload: JsonValue
    metadata: Metadata
    flows: dict[Id, FlowResult]
    start: StartResult | SkipJsonSchema[None] = Field(default=None, json_schema_extra=_omit_default)
    transitions: list[TransitionResult]
    execution: ExecutionInfo

    @model_validator(mode="after")
    def start_is_omitted_rather_than_null(self) -> Self:
        if "start" in self.model_fields_set and self.start is None:
            raise ValueError("start must be omitted rather than null")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        if "start" not in self.model_fields_set:
            values.pop("start", None)
        return values

    @field_validator("metadata", mode="before")
    @classmethod
    def revalidate_metadata_instance(cls, value: object) -> object:
        if isinstance(value, Metadata):
            try:
                return value.model_dump(mode="json", warnings=False)
            except (TypeError, ValueError):
                raise ValueError("invalid metadata") from None
        return value


def _safe_error(failure: Failure) -> dict[str, JsonValue]:
    if not isinstance(failure.code, ErrorCode) or type(failure.retryable) is not bool:
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    return {
        "code": failure.code.value,
        "message": failure.message,
        "retryable": failure.retryable,
    }


def _step_result(record: StepRecord) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], thaw_json(step_record_value(record)))


def _flow_result(record: FlowRecord) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], thaw_json(flow_record_value(record)))


def to_execution_result(value: RunResult) -> ExecutionResult:
    """Copy an immutable core result into its strict flow-scoped representation."""

    try:
        flows: dict[str, JsonValue] = {}
        for flow_id, record in value.flows:
            if flow_id in flows:
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            flows[flow_id] = _flow_result(record)
        transitions: list[JsonValue] = []
        for transition in value.transitions:
            item: dict[str, JsonValue] = {"source": transition.source, "reason": transition.reason}
            if transition.flow is not None:
                item["flow"] = transition.flow
            if transition.outcome is not None:
                item["outcome"] = transition.outcome
            route: dict[str, JsonValue] = {"kind": transition.route_kind}
            if transition.route_index is not None:
                route["index"] = transition.route_index
            if transition.route_case is not None:
                route["case"] = transition.route_case
            item["route"] = route
            transitions.append(item)
        execution: dict[str, JsonValue] = {
            "id": value.execution_id,
            "workflow": value.workflow,
            "revision": value.revision,
            "status": value.status,
            "usage": cast(dict[str, JsonValue], thaw_json(usage_value(value.usage))),
        }
        if value.error is not None:
            execution["error"] = _safe_error(value.error)
        if value.trace is not None:
            execution["trace"] = {"trace_id": value.trace[0], "span_id": value.trace[1]}
        document: dict[str, JsonValue] = {
            "payload": thaw_json(value.payload),
            "metadata": thaw_json(value.metadata),
            "flows": flows,
            "transitions": transitions,
            "execution": execution,
        }
        if value.start is not None:
            start_route: dict[str, JsonValue] = {"kind": value.start.route_kind}
            if value.start.route_index is not None:
                start_route["index"] = value.start.route_index
            document["start"] = {"flow": value.start.flow, "route": start_route}
        return ExecutionResult.model_validate(document, strict=True)
    except (ServiceError, TypeError, ValueError, ValidationError):
        raise ServiceError(ErrorCode.INVALID_OUTPUT) from None
