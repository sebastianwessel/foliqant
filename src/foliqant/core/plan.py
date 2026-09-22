"""Frozen, standard-library-only workflow plans consumed by the execution engine."""

from dataclasses import dataclass
from typing import Literal

from .json import FrozenJson, FrozenObject
from .prompt import PromptTemplate

# Keep composed ledgers below recursive public-boundary limits while preserving
# the full business-value depth allowance at every leaf.
MAX_COLLECTION_DEPTH = 16


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """A bundle-relative source coordinate safe to expose in diagnostics."""

    path: str
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class BindingPlan:
    """A literal or RFC 6901 pointer, including explicit fallback presence."""

    kind: Literal["pointer", "literal"]
    pointer: str | None = None
    literal: FrozenJson = None
    optional: bool = False
    has_default: bool = False
    default: FrozenJson = None


@dataclass(frozen=True, slots=True)
class DecisionOptionPlan:
    id: str
    description: str


@dataclass(frozen=True, slots=True)
class DecisionQuestionPlan:
    """Pydantic-free projection of the shared native decision question."""

    id: str
    type: Literal["choice", "multiselect", "predicate", "ordinal", "request_units"]
    prompt: str
    criteria: tuple[str, ...]
    allowed_source_ids: tuple[str, ...]
    options: tuple[DecisionOptionPlan, ...] = ()
    min_selections: int | None = None
    max_selections: int | None = None
    allow_no_match: bool | None = None


@dataclass(frozen=True, slots=True)
class ToolPolicyPlan:
    server: str
    allow: tuple[str, ...]
    choice_mode: Literal["auto", "required", "named"]
    choice_name: str | None = None


type DecisionIssue = Literal[
    "no_supported_answer", "conflicting_information", "multiple_valid_options"
]


@dataclass(frozen=True, slots=True)
class CategoryPlan:
    """An exact category identifier and optional caller-authored description."""

    id: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class FallbackPlan:
    """Deterministic classification for explicitly allowed native uncertainty."""

    category: CategoryPlan
    on: tuple[DecisionIssue, ...]


@dataclass(frozen=True, slots=True)
class TransitionTargetPlan:
    """Exactly one authored flow instance or terminal disposition."""

    flow: str | None = None
    outcome: Literal["completed", "needs_review"] | None = None


@dataclass(frozen=True, slots=True)
class MatchRoutingPlan:
    binding: BindingPlan
    cases: tuple[tuple[str, TransitionTargetPlan], ...]
    default: TransitionTargetPlan


@dataclass(frozen=True, slots=True)
class UnresolvedRoutingPlan:
    """Route agreeing issue targets, otherwise use the required default."""

    default: TransitionTargetPlan
    issues: tuple[tuple[DecisionIssue, TransitionTargetPlan], ...] = ()

    def target(self, issues: tuple[DecisionIssue, ...]) -> TransitionTargetPlan:
        routes = dict(self.issues)
        targets = {routes.get(issue, self.default) for issue in issues}
        return targets.pop() if len(targets) == 1 else self.default


@dataclass(frozen=True, slots=True)
class StepPlan:
    """One operation in a flow's authored list order."""

    name: str
    type: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class DecisionStepPlan(StepPlan):
    model: str = ""
    sources: tuple[tuple[str, BindingPlan], ...] = ()
    questions: tuple[DecisionQuestionPlan, ...] = ()
    question_mode: Literal["single", "multiple"] = "single"
    instructions: str = ""
    source_formats: tuple[tuple[str, Literal["text", "json"]], ...] = ()
    fallback: FallbackPlan | None = None


@dataclass(frozen=True, slots=True)
class LlmStepPlan(StepPlan):
    model: str = ""
    input: tuple[tuple[str, BindingPlan], ...] = ()
    instructions: str = ""
    output_kind: Literal["text", "schema"] = "text"
    output_schema_path: str | None = None
    output_schema: FrozenObject | None = None
    tools: ToolPolicyPlan | None = None
    prompt: PromptTemplate | None = None
    max_iterations: int = 4


@dataclass(frozen=True, slots=True)
class McpStepPlan(StepPlan):
    server: str = ""
    tool: str = ""
    arguments: tuple[tuple[str, BindingPlan], ...] = ()


@dataclass(frozen=True, slots=True)
class HandlerStepPlan(StepPlan):
    handler: str = ""
    input: tuple[tuple[str, BindingPlan], ...] = ()


@dataclass(frozen=True, slots=True)
class FlowCollectionStepPlan(StepPlan):
    items: BindingPlan
    flows: tuple[str, ...]
    max_items: int = 32

    @property
    def input(self) -> tuple[tuple[str, BindingPlan], ...]:
        """Expose the one explicit binding through the common step input interface."""
        return (("items", self.items),)


type CompiledStep = (
    DecisionStepPlan | LlmStepPlan | McpStepPlan | HandlerStepPlan | FlowCollectionStepPlan
)


@dataclass(frozen=True, slots=True)
class SchemaResourcePlan:
    """One immutable bundle-relative JSON Schema resource."""

    path: str
    schema: FrozenObject


@dataclass(frozen=True, slots=True)
class FlowPlan:
    """One configured flow instance with local steps and boundary projections."""

    name: str
    input: tuple[tuple[str, BindingPlan], ...]
    steps: tuple[CompiledStep, ...]
    input_schema_path: str | None
    input_schema: FrozenObject | None
    output: BindingPlan | None
    transition: TransitionTargetPlan | MatchRoutingPlan | None
    on_unresolved: TransitionTargetPlan | UnresolvedRoutingPlan | None
    location: SourceLocation
    callable: bool = False

    def step(self, name: str) -> CompiledStep:
        """Return an exact flow-local step ID or raise ``KeyError``."""
        for step in self.steps:
            if step.name == name:
                return step
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class WorkflowPlan:
    """A deterministic plan; boundary models never cross into this core value."""

    name: str
    revision: str
    start: str
    default_model: str | None
    input_schema_path: str | None
    input_schema: FrozenObject | None
    schema_resources: tuple[SchemaResourcePlan, ...]
    output: BindingPlan | None
    flows: tuple[FlowPlan, ...]
    location: SourceLocation

    def flow(self, name: str) -> FlowPlan:
        """Return an exact flow instance ID or raise ``KeyError``."""
        for flow in self.flows:
            if flow.name == name:
                return flow
        raise KeyError(name)
