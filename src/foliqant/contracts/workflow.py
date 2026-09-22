"""Closed authoring contracts for the offline workflow compiler."""

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Discriminator, Field, StrictBool, Tag, field_validator, model_validator

from foliqant.core.json import JsonValue
from foliqant.core.plan import DecisionIssue
from foliqant.decisions import (
    CategoryCatalog,
    DecisionOption,
    DecisionQuestion,
)
from foliqant.decisions.category_catalog import CategoryDescription, CategoryKey

from .base import BoundaryModel
from .identifiers import Id as Id
from .models import StepModel

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


class FallbackCategory(BoundaryModel):
    """Caller-defined fallback category, excluded from the model's options."""

    id: CategoryKey
    description: CategoryDescription | None = None


class DecisionFallback(BoundaryModel):
    category: FallbackCategory
    on: Annotated[list[DecisionIssue], Field(min_length=1, max_length=3)]

    @field_validator("on")
    @classmethod
    def unique_issues(cls, value: list[DecisionIssue]) -> list[DecisionIssue]:
        if len(set(value)) != len(value):
            raise ValueError("fallback issues must be unique")
        return value


class SourcePointerBinding(PointerBinding):
    """A selected decision source, rendered as nonempty text or canonical JSON."""

    format: Literal["text", "json"] = "text"


class SourceLiteralBinding(LiteralBinding):
    """A literal decision source with explicit optional JSON rendering."""

    format: Literal["text", "json"] = "text"


SourceBinding = Annotated[
    Annotated[SourcePointerBinding, Tag("pointer")]
    | Annotated[SourceLiteralBinding, Tag("literal")],
    Discriminator(_binding_kind),
]


class _CommonStep(BoundaryModel):
    type: str


class DecisionStepAuthoring(_CommonStep):
    type: Literal["decision"]
    model: StepModel | None = None
    sources: dict[Id, SourceBinding]
    question: QuestionShorthand | None = None
    questions: Annotated[list[DecisionQuestion], Field(min_length=2, max_length=64)] | None = None
    instructions: NonBlank
    fallback: DecisionFallback | None = None

    @model_validator(mode="after")
    def exactly_one_question_form(self) -> "DecisionStepAuthoring":
        if (self.question is None) == (self.questions is None):
            raise ValueError("exactly one question form is required")
        if self.fallback is not None:
            if not isinstance(self.question, ChoiceQuestionShorthand):
                raise ValueError("fallback requires a single choice question")
            if self.fallback.category.id in {
                category.id for category in self.question.catalog.categories
            }:
                raise ValueError("fallback category must differ from ordinary options")
        if self.questions is not None:
            question_ids = [question.id for question in self.questions]
            if len(question_ids) != len(set(question_ids)):
                raise ValueError("question IDs must be unique")
        return self


class SchemaOutput(BoundaryModel):
    schema_value: NonBlank | dict[str, JsonValue] = Field(
        alias="schema", serialization_alias="schema"
    )


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
    model: StepModel | None = None
    input: dict[Id, Binding]
    instructions: NonBlank
    output: Literal["text"] | SchemaOutput
    prompt: NonBlank | None = None
    tools: ToolPolicy | None = None
    max_iterations: Annotated[int, Field(strict=True, ge=1, le=1024)] = 4


class McpStepAuthoring(_CommonStep):
    type: Literal["mcp"]
    server: Id
    tool: NonBlank
    arguments: dict[Id, Binding]


class HandlerStepAuthoring(_CommonStep):
    type: Literal["handler"]
    handler: Id
    input: dict[Id, Binding]


class FlowCollectionStepAuthoring(_CommonStep):
    """Invoke allowlisted callable flows sequentially from explicit item inputs."""

    type: Literal["flow_collection"]
    items: Binding
    flows: Annotated[list[Id], Field(min_length=1)]
    max_items: Annotated[int, Field(strict=True, ge=1, le=1024)] = 32

    @field_validator("flows")
    @classmethod
    def unique_flows(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("callable flow IDs must be unique")
        return value


StepAuthoring = Annotated[
    DecisionStepAuthoring
    | LlmStepAuthoring
    | McpStepAuthoring
    | HandlerStepAuthoring
    | FlowCollectionStepAuthoring,
    Field(discriminator="type"),
]


class NamedStep(BoundaryModel):
    """One ordered operation with an identity independent of its definition file."""

    id: Id
    definition: StepAuthoring | NonBlank | None = None


class FlowDefinition(BoundaryModel):
    """A reusable sequence; the containing workflow owns its instance identity."""

    input_schema: NonBlank | dict[str, JsonValue] | None = None
    output: Binding | None = None
    steps: Annotated[list[NamedStep | Id], Field(min_length=1)]

    @field_validator("steps")
    @classmethod
    def unique_steps(cls, value: list[NamedStep | str]) -> list[NamedStep | str]:
        if len({step if isinstance(step, str) else step.id for step in value}) != len(value):
            raise ValueError("step IDs must be unique within a flow")
        return value


class FlowTarget(BoundaryModel):
    flow: Id


class OutcomeTarget(BoundaryModel):
    outcome: Literal["completed", "needs_review"]


TransitionTarget = FlowTarget | OutcomeTarget


class MatchRouting(BoundaryModel):
    """Exact string matching with an explicit default, without coercion."""

    binding: Binding
    cases: Annotated[dict[str, TransitionTarget], Field(min_length=1)]
    default: TransitionTarget


class UnresolvedRouting(BoundaryModel):
    """Issue-specific targets with a required default for ambiguity or no issue."""

    default: TransitionTarget
    no_supported_answer: TransitionTarget | None = None
    conflicting_information: TransitionTarget | None = None
    multiple_valid_options: TransitionTarget | None = None


class FlowInstance(BoundaryModel):
    """One named flow invocation with explicit boundary data and authored targets."""

    definition: FlowDefinition | NonBlank | None = None
    input: dict[Id, Binding]
    transition: TransitionTarget | MatchRouting
    on_unresolved: TransitionTarget | UnresolvedRouting | None = None

    @model_validator(mode="after")
    def unresolved_does_not_complete(self) -> "FlowInstance":
        route = self.on_unresolved
        targets = (
            [
                route.default,
                route.no_supported_answer,
                route.conflicting_information,
                route.multiple_valid_options,
            ]
            if isinstance(route, UnresolvedRouting)
            else [route]
        )
        if any(
            isinstance(target, OutcomeTarget) and target.outcome == "completed"
            for target in targets
        ):
            raise ValueError("unresolved flows cannot directly complete the workflow")
        return self


class CallableFlow(BoundaryModel):
    """A flow available only to explicit collection calls, without boundary routes."""

    callable: Literal[True]
    definition: FlowDefinition | NonBlank | None = None

    @field_validator("callable", mode="before")
    @classmethod
    def explicit_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("callable must be true")
        return value


class WorkflowAuthoring(BoundaryModel):
    """A finite graph of explicit, sequential flow instances."""

    name: Id | None = None
    start: Id | None = None
    defaults: WorkflowDefaults = Field(default_factory=WorkflowDefaults)
    input_schema: NonBlank | dict[str, JsonValue] | None = None
    output: Binding | None = None
    flows: Annotated[dict[Id, FlowInstance | CallableFlow], Field(min_length=1)]


class DeclaredTool(BoundaryModel):
    """Operator-reviewed tool metadata used by offline compilation only."""

    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] | None = None
    effect: Literal["read", "write"]


class DeclaredToolCatalog(BoundaryModel):
    tools: dict[str, DeclaredTool]
