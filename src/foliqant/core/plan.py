"""Frozen, standard-library-only workflow plans consumed by the execution engine."""

from dataclasses import dataclass
from typing import Literal

from .json import FrozenJson, FrozenObject


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
    "missing_information", "conflicting_information", "multiple_valid_options", "no_matching_option"
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
class UnresolvedRoutingPlan:
    """Route agreeing issue targets, otherwise use the required default."""

    default: str
    issues: tuple[tuple[DecisionIssue, str], ...] = ()

    def target(self, issues: tuple[DecisionIssue, ...]) -> str:
        routes = dict(self.issues)
        targets = {routes.get(issue, self.default) for issue in issues}
        return targets.pop() if len(targets) == 1 else self.default


@dataclass(frozen=True, slots=True)
class StepPlan:
    """Common immutable step data.

    ``unresolved_before_transition`` tells the runtime to evaluate decision
    answerability before following ``next``. A missing unresolved target means
    the workflow terminates with ``needs_review``.
    """

    name: str
    type: str
    location: SourceLocation
    next: str | None = None
    on_unresolved: str | UnresolvedRoutingPlan | None = None
    unresolved_before_transition: bool = False


@dataclass(frozen=True, slots=True)
class DecisionStepPlan(StepPlan):
    model: str = ""
    sources: tuple[tuple[str, BindingPlan], ...] = ()
    questions: tuple[DecisionQuestionPlan, ...] = ()
    question_mode: Literal["single", "multiple"] = "single"
    instructions: str = ""
    on_answer: tuple[tuple[str, str], ...] = ()
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
class FinishStepPlan(StepPlan):
    outcome: Literal["completed", "needs_review"] = "completed"


type CompiledStep = DecisionStepPlan | LlmStepPlan | McpStepPlan | HandlerStepPlan | FinishStepPlan


@dataclass(frozen=True, slots=True)
class SchemaResourcePlan:
    """One immutable bundle-relative JSON Schema resource."""

    path: str
    schema: FrozenObject


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
    steps: tuple[CompiledStep, ...]
    location: SourceLocation

    def step(self, name: str) -> CompiledStep:
        """Return an exact step ID or raise ``KeyError``."""

        for step in self.steps:
            if step.name == name:
                return step
        raise KeyError(name)
