"""Frozen, standard-library-only workflow plans consumed by the execution engine."""

import re
from dataclasses import dataclass, field
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
class Diagnostic:
    """A non-fatal compiler finding; errors raise ``CompilationError`` instead.

    Messages name configured identifiers and authored values only, never runtime
    data, credentials or prompt text.
    """

    code: str
    level: Literal["warning", "info"]
    location: SourceLocation
    message: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class BindingPlan:
    """A literal, RFC 6901 pointer, ``first_of`` candidates or object ``fields``.

    A pointer or ``first_of`` binding is optional exactly when it has a default.
    """

    kind: Literal["pointer", "literal", "first_of", "fields"]
    pointer: str | None = None
    literal: FrozenJson = None
    has_default: bool = False
    default: FrozenJson = None
    members: tuple[str, ...] = ()
    fields: tuple[tuple[str, "BindingPlan"], ...] = ()


type ConditionOperator = Literal[
    "present",
    "empty",
    "equals",
    "not_equals",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "matches",
    "length",
]


@dataclass(frozen=True, slots=True)
class LeafConditionPlan:
    """One source and operator; ``source`` is a literal, pointer or ``first_of``.

    ``comparison`` is set only for ``length``; ``pattern`` only for ``matches``.
    """

    source: BindingPlan
    operator: ConditionOperator
    operand: FrozenJson = None
    comparison: Literal["gt", "gte", "lt", "lte", "equals"] | None = None
    pattern: re.Pattern[str] | None = None


@dataclass(frozen=True, slots=True)
class AllConditionPlan:
    operands: tuple["ConditionPlan", ...]


@dataclass(frozen=True, slots=True)
class AnyConditionPlan:
    operands: tuple["ConditionPlan", ...]


@dataclass(frozen=True, slots=True)
class NotConditionPlan:
    operand: "ConditionPlan"


type ConditionPlan = LeafConditionPlan | AllConditionPlan | AnyConditionPlan | NotConditionPlan


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
    default_covers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteEntryPlan:
    """A conditional target; the otherwise entry has no condition."""

    target: TransitionTargetPlan
    when: ConditionPlan | None = None


@dataclass(frozen=True, slots=True)
class ConditionalRoutingPlan:
    """Ordered entries; the first true condition (or the otherwise entry) selects."""

    entries: tuple[RouteEntryPlan, ...]


@dataclass(frozen=True, slots=True)
class UnresolvedRoutingPlan:
    """Route agreeing issue targets, otherwise use the required default."""

    default: TransitionTargetPlan
    issues: tuple[tuple[DecisionIssue, TransitionTargetPlan], ...] = ()

    def target(self, issues: tuple[DecisionIssue, ...]) -> TransitionTargetPlan:
        return self.select(issues)[0]

    def select(
        self, issues: tuple[DecisionIssue, ...]
    ) -> tuple[TransitionTargetPlan, DecisionIssue | None]:
        """Return the target and the issue key that selected it (None for default)."""
        routes = dict(self.issues)
        targets = {routes.get(issue, self.default) for issue in issues}
        if len(targets) != 1:
            return self.default, None
        target = targets.pop()
        return target, next((issue for issue in issues if routes.get(issue) == target), None)


@dataclass(frozen=True, slots=True)
class RepeatPlan:
    """Bounded repetition of one routed flow, with an optional callable retry flow.

    ``retry_flow_input`` binds the retry flow's input; ``retry_input`` overrides
    input keys of the repeated flow for attempts two and later.
    """

    max_attempts: int
    until: ConditionPlan
    retry_flow: str | None = None
    retry_flow_input: tuple[tuple[str, BindingPlan], ...] = ()
    continue_when: ConditionPlan | None = None
    retry_input: tuple[tuple[str, BindingPlan], ...] = ()


@dataclass(frozen=True, slots=True)
class StepPlan:
    """One operation in a flow's authored list order, optionally guarded by ``when``."""

    name: str
    type: str
    location: SourceLocation
    when: ConditionPlan | None = field(default=None, kw_only=True)


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
    """One configured flow instance with local steps and boundary projections.

    ``on_unresolved`` is the effective review route; ``on_unresolved_inherited``
    reports that it comes from the workflow ``defaults``.
    """

    name: str
    input: tuple[tuple[str, BindingPlan], ...]
    steps: tuple[CompiledStep, ...]
    input_schema_path: str | None
    input_schema: FrozenObject | None
    output: BindingPlan | None
    transition: TransitionTargetPlan | MatchRoutingPlan | ConditionalRoutingPlan | None
    on_unresolved: TransitionTargetPlan | UnresolvedRoutingPlan | ConditionalRoutingPlan | None
    location: SourceLocation
    callable: bool = False
    repeat: RepeatPlan | None = None
    on_unresolved_inherited: bool = False
    available_flows: tuple[str, ...] = ()
    """Flows on every path to this one: their results may be bound without default."""

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
    start: str | ConditionalRoutingPlan
    default_model: str | None
    input_schema_path: str | None
    input_schema: FrozenObject | None
    schema_resources: tuple[SchemaResourcePlan, ...]
    output: BindingPlan | None
    flows: tuple[FlowPlan, ...]
    location: SourceLocation
    diagnostics: tuple[Diagnostic, ...] = ()

    def flow(self, name: str) -> FlowPlan:
        """Return an exact flow instance ID or raise ``KeyError``."""
        for flow in self.flows:
            if flow.name == name:
                return flow
        raise KeyError(name)
