"""Closed authoring contracts for the offline workflow compiler."""

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Discriminator, Field, StrictBool, Tag, field_validator, model_validator

from foliqant.core.json import JsonValue
from foliqant.decisions import (
    CategoryCatalog,
    DecisionOption,
    DecisionQuestion,
)

from .base import BoundaryModel

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")]
NonBlank = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]
JsonPointer = Annotated[str, Field(pattern=r"^(?:/(?:[^~/]|~[01])*)*$")]


class PointerBinding(BoundaryModel):
    pointer: JsonPointer
    optional: StrictBool = False
    default: JsonValue | None = None

    @model_validator(mode="after")
    def explicit_fallback_only_for_optional(self) -> "PointerBinding":
        has_default = "default" in self.model_fields_set
        if self.optional != has_default:
            raise ValueError("optional pointers require one explicit default")
        return self


class LiteralBinding(BoundaryModel):
    literal: JsonValue


def _binding_kind(value: object) -> str | None:
    if isinstance(value, Mapping):
        if "pointer" in value and "literal" not in value:
            return "pointer"
        if "literal" in value and "pointer" not in value:
            return "literal"
    if isinstance(value, PointerBinding):
        return "pointer"
    if isinstance(value, LiteralBinding):
        return "literal"
    return None


Binding = Annotated[
    Annotated[PointerBinding, Tag("pointer")] | Annotated[LiteralBinding, Tag("literal")],
    Discriminator(_binding_kind),
]


class ChoiceQuestionShorthand(BoundaryModel):
    type: Literal["choice"]
    criteria: Annotated[list[NonBlank], Field(min_length=1, max_length=64)]
    catalog: CategoryCatalog


class MultiselectQuestionShorthand(BoundaryModel):
    type: Literal["multiselect"]
    criteria: Annotated[list[NonBlank], Field(min_length=1, max_length=64)]
    catalog: CategoryCatalog
    minSelections: Annotated[int, Field(strict=True, ge=0, le=1024)]
    maxSelections: Annotated[int, Field(strict=True, ge=1, le=1024)]

    @model_validator(mode="after")
    def valid_cardinality(self) -> "MultiselectQuestionShorthand":
        count = len(self.catalog.categories)
        if self.minSelections > self.maxSelections or self.maxSelections > count:
            raise ValueError("invalid selection cardinality")
        return self


class PredicateQuestionShorthand(BoundaryModel):
    type: Literal["predicate"]
    criteria: Annotated[list[NonBlank], Field(min_length=1, max_length=64)]


class OrdinalQuestionShorthand(BoundaryModel):
    type: Literal["ordinal"]
    criteria: Annotated[list[NonBlank], Field(min_length=1, max_length=64)]
    levels: Annotated[list[DecisionOption], Field(min_length=2, max_length=128)]


class RequestUnitsQuestionShorthand(BoundaryModel):
    type: Literal["request_units"]
    criteria: Annotated[list[NonBlank], Field(min_length=1, max_length=64)]
    catalog: CategoryCatalog
    allowNoMatch: StrictBool


QuestionShorthand = Annotated[
    ChoiceQuestionShorthand
    | MultiselectQuestionShorthand
    | PredicateQuestionShorthand
    | OrdinalQuestionShorthand
    | RequestUnitsQuestionShorthand,
    Field(discriminator="type"),
]


class WorkflowDefaults(BoundaryModel):
    model: Id | None = None


class WorkflowAuthoring(BoundaryModel):
    version: Literal[1]
    name: Id
    start: Id
    defaults: WorkflowDefaults = Field(default_factory=WorkflowDefaults)
    input_schema: NonBlank | None = None
    output: Binding | None = None

    @model_validator(mode="before")
    @classmethod
    def exact_integer_version(cls, value: object) -> object:
        if not isinstance(value, Mapping) or type(value.get("version")) is not int:
            raise ValueError("version must be the integer 1")
        return value


class _CommonStep(BoundaryModel):
    name: Id | None = None
    type: str
    next: Id | None = None
    on_unresolved: Id | None = None


class DecisionStepAuthoring(_CommonStep):
    type: Literal["decision"]
    model: Id | None = None
    sources: dict[Id, Binding]
    question: QuestionShorthand | None = None
    questions: Annotated[list[DecisionQuestion], Field(min_length=2, max_length=64)] | None = None
    instructions: NonBlank
    on_answer: dict[str, Id] | None = None

    @model_validator(mode="after")
    def exactly_one_question_form(self) -> "DecisionStepAuthoring":
        if (self.question is None) == (self.questions is None):
            raise ValueError("exactly one question form is required")
        if self.next is not None and self.on_answer is not None:
            raise ValueError("next and on_answer are mutually exclusive")
        if self.questions is not None and self.on_answer is not None:
            raise ValueError("multi-question steps cannot route by answer")
        if self.questions is not None:
            question_ids = [question.id for question in self.questions]
            if len(question_ids) != len(set(question_ids)):
                raise ValueError("question IDs must be unique")
        return self


class SchemaOutput(BoundaryModel):
    schema_path: NonBlank = Field(alias="schema", serialization_alias="schema")


class NamedToolChoice(BoundaryModel):
    name: NonBlank


class ToolPolicy(BoundaryModel):
    server: Id
    allow: Annotated[list[NonBlank], Field(min_length=1)]
    choice: Literal["auto", "required"] | NamedToolChoice

    @field_validator("allow")
    @classmethod
    def unique_tools(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tool names must be unique")
        return value


class LlmStepAuthoring(_CommonStep):
    type: Literal["llm"]
    model: Id | None = None
    input: dict[Id, Binding]
    instructions: NonBlank
    output: Literal["text"] | SchemaOutput
    tools: ToolPolicy | None = None


class McpStepAuthoring(_CommonStep):
    type: Literal["mcp"]
    server: Id
    tool: NonBlank
    arguments: dict[Id, Binding]


class HandlerStepAuthoring(_CommonStep):
    type: Literal["handler"]
    handler: Id
    input: dict[Id, Binding]


class FinishStepAuthoring(BoundaryModel):
    name: Id | None = None
    type: Literal["finish"]
    outcome: Literal["completed", "needs_review"]


StepAuthoring = Annotated[
    DecisionStepAuthoring
    | LlmStepAuthoring
    | McpStepAuthoring
    | HandlerStepAuthoring
    | FinishStepAuthoring,
    Field(discriminator="type"),
]


class DeclaredTool(BoundaryModel):
    """Operator-reviewed tool metadata used by offline compilation only."""

    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] | None = None
    effect: Literal["read", "write"]


class DeclaredToolCatalog(BoundaryModel):
    tools: dict[str, DeclaredTool]
