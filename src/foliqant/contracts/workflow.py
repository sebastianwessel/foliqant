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
from .conditions import Condition, JsonPointer
from .identifiers import Id as Id
from .models import StepModel

NonBlank = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]


class PointerBinding(BoundaryModel):
    """An RFC 6901 pointer; a declared ``default`` makes the binding optional."""

    pointer: JsonPointer
    default: JsonValue | None = None


class PointerMember(BoundaryModel):
    """One `first_of` candidate; defaults belong to the enclosing binding."""

    pointer: JsonPointer


class FirstOfBinding(BoundaryModel):
    """The first member that resolves to a non-null value, otherwise ``default``."""

    first_of: Annotated[list[PointerMember], Field(min_length=1, max_length=16)]
    default: JsonValue | None = None


class LiteralBinding(BoundaryModel):
    literal: JsonValue


def _binding_kind(value: object) -> str | None:
    if isinstance(value, Mapping):
        kinds = [kind for kind in ("pointer", "literal", "first_of") if kind in value]
        return kinds[0] if len(kinds) == 1 else None
    if isinstance(value, PointerBinding):
        return "pointer"
    if isinstance(value, LiteralBinding):
        return "literal"
    if isinstance(value, FirstOfBinding):
        return "first_of"
    return None


Binding = Annotated[
    Annotated[PointerBinding, Tag("pointer")]
    | Annotated[LiteralBinding, Tag("literal")]
    | Annotated[FirstOfBinding, Tag("first_of")],
    Discriminator(_binding_kind),
]


class ObjectOutput(BoundaryModel):
    """Project an object with exactly these keys, each resolved from one binding."""

    fields: Annotated[dict[Id, Binding], Field(min_length=1, max_length=128)]


def _output_kind(value: object) -> str | None:
    if isinstance(value, Mapping) and "fields" in value:
        return "fields"
    if isinstance(value, ObjectOutput):
        return "fields"
    return _binding_kind(value)


OutputBinding = Annotated[
    Annotated[PointerBinding, Tag("pointer")]
    | Annotated[LiteralBinding, Tag("literal")]
    | Annotated[FirstOfBinding, Tag("first_of")]
    | Annotated[ObjectOutput, Tag("fields")],
    Discriminator(_output_kind),
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


class SourceFirstOfBinding(FirstOfBinding):
    """The first present decision source candidate with explicit rendering."""

    format: Literal["text", "json"] = "text"


class SourceLiteralBinding(LiteralBinding):
    """A literal decision source with explicit optional JSON rendering."""

    format: Literal["text", "json"] = "text"


SourceBinding = Annotated[
    Annotated[SourcePointerBinding, Tag("pointer")]
    | Annotated[SourceLiteralBinding, Tag("literal")]
    | Annotated[SourceFirstOfBinding, Tag("first_of")],
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
    max_iterations: Annotated[int, Field(strict=True, ge=1, le=1024)] = 8


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
    """One ordered operation with an identity independent of its definition file.

    A step with ``when`` runs only when the condition holds; otherwise it is
    recorded as ``skipped`` and the flow continues with the next step.
    """

    id: Id
    definition: StepAuthoring | NonBlank | None = None
    when: Condition | None = None


class FlowDefaults(BoundaryModel):
    """Defaults for the steps of one flow definition, overriding the workflow's."""

    model: Id | None = None


class FlowDefinition(BoundaryModel):
    """A reusable sequence; the containing workflow owns its instance identity."""

    defaults: FlowDefaults = Field(default_factory=FlowDefaults)
    input_schema: NonBlank | dict[str, JsonValue] | None = None
    output: OutputBinding | None = None
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
    """Exact string matching with an explicit default, without coercion.

    ``default_covers`` lists the statically allowed values that deliberately
    fall to ``default``; it must equal the uncovered set the compiler derives.
    """

    binding: Binding
    cases: Annotated[dict[str, TransitionTarget], Field(min_length=1)]
    default: TransitionTarget
    default_covers: Annotated[list[str], Field(min_length=1, max_length=256)] | None = None

    @field_validator("default_covers")
    @classmethod
    def unique_covered_values(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("covered values must be unique")
        return value


class RouteEntry(BoundaryModel):
    """One ordered route entry; the first entry whose condition holds is selected."""

    when: Condition | None = None
    flow: Id | None = None
    outcome: Literal["completed", "needs_review"] | None = None

    @model_validator(mode="after")
    def exactly_one_target(self) -> "RouteEntry":
        if (self.flow is None) == (self.outcome is None):
            raise ValueError("a route entry requires exactly one target")
        return self


class ConditionalRouting(BoundaryModel):
    """Ordered conditional targets; the last entry is the unconditional otherwise."""

    route: Annotated[list[RouteEntry], Field(min_length=1, max_length=32)]


class StartRouteEntry(BoundaryModel):
    """A start candidate; conditions read only ``/payload`` and ``/metadata``."""

    when: Condition | None = None
    flow: Id


class StartRouting(BoundaryModel):
    """Select the first flow from the accepted envelope."""

    route: Annotated[list[StartRouteEntry], Field(min_length=1, max_length=32)]


class UnresolvedRouting(BoundaryModel):
    """Issue-specific targets with a required default for ambiguity or no issue."""

    default: TransitionTarget
    no_supported_answer: TransitionTarget | None = None
    conflicting_information: TransitionTarget | None = None
    multiple_valid_options: TransitionTarget | None = None


def _route_kind(value: object) -> str | None:
    """Select a routing form by its distinguishing key for precise diagnostics."""
    if isinstance(value, Mapping):
        for key, kind in (
            ("route", "route"),
            ("cases", "cases"),
            ("binding", "cases"),
            ("flow", "flow"),
            ("outcome", "outcome"),
            ("default", "issues"),
        ):
            if key in value:
                return kind
        return None
    for model, kind in (
        (ConditionalRouting, "route"),
        (MatchRouting, "cases"),
        (FlowTarget, "flow"),
        (OutcomeTarget, "outcome"),
        (UnresolvedRouting, "issues"),
    ):
        if isinstance(value, model):
            return kind
    return None


Transition = Annotated[
    Annotated[FlowTarget, Tag("flow")]
    | Annotated[OutcomeTarget, Tag("outcome")]
    | Annotated[MatchRouting, Tag("cases")]
    | Annotated[ConditionalRouting, Tag("route")],
    Discriminator(_route_kind),
]
ReviewRouting = Annotated[
    Annotated[FlowTarget, Tag("flow")]
    | Annotated[OutcomeTarget, Tag("outcome")]
    | Annotated[UnresolvedRouting, Tag("issues")]
    | Annotated[ConditionalRouting, Tag("route")],
    Discriminator(_route_kind),
]


def _start_kind(value: object) -> str | None:
    if isinstance(value, str):
        return "flow"
    if isinstance(value, Mapping | StartRouting):
        return "route"
    return None


Start = Annotated[
    Annotated[Id, Tag("flow")] | Annotated[StartRouting, Tag("route")],
    Discriminator(_start_kind),
]


class WorkflowDefaults(BoundaryModel):
    """Workflow-wide defaults: step model and the inherited review route."""

    model: Id | None = None
    on_unresolved: ReviewRouting | None = None


class RepeatRetry(BoundaryModel):
    """A callable flow run between two attempts of the repeated flow."""

    flow: Id
    input: dict[Id, Binding] = Field(default_factory=dict)
    continue_when: Condition | None = None


class Repeat(BoundaryModel):
    """Bounded repetition of one flow instance; the static graph stays acyclic."""

    max_attempts: Annotated[int, Field(strict=True, ge=2, le=64)]
    until: Condition
    retry: RepeatRetry | None = None
    retry_input: dict[Id, Binding] = Field(default_factory=dict)


class FlowInstance(BoundaryModel):
    """One named flow invocation with explicit boundary data and authored targets."""

    definition: FlowDefinition | NonBlank | None = None
    input: dict[Id, Binding]
    transition: Transition
    on_unresolved: ReviewRouting | None = None
    """A review route never targets ``outcome: completed`` (``review_completes_run``)."""
    repeat: Repeat | None = None


class CallableFlow(BoundaryModel):
    """A flow available only to explicit collection or retry calls, without routes.

    ``repeat`` repeats every collection item invocation; its bindings and
    conditions read the item scope: ``/payload`` (the item input), ``/metadata``,
    ``/flows/<self>/result|attempts`` and ``/flows/<retry>/result|attempts``.
    """

    callable: Literal[True]
    definition: FlowDefinition | NonBlank | None = None
    repeat: Repeat | None = None

    @field_validator("callable", mode="before")
    @classmethod
    def explicit_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("callable must be true")
        return value


class WorkflowAuthoring(BoundaryModel):
    """A finite graph of explicit, sequential flow instances."""

    name: Id | None = None
    start: Start | None = None
    defaults: WorkflowDefaults = Field(default_factory=WorkflowDefaults)
    input_schema: NonBlank | dict[str, JsonValue] | None = None
    output: OutputBinding | None = None
    flows: Annotated[dict[Id, FlowInstance | CallableFlow], Field(min_length=1)]


class DeclaredTool(BoundaryModel):
    """Operator-reviewed tool metadata used by offline compilation only."""

    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] | None = None
    effect: Literal["read", "write"]


class DeclaredToolCatalog(BoundaryModel):
    tools: dict[str, DeclaredTool]
