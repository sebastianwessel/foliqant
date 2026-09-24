"""Offline graph model of compiled workflows, with Mermaid, Graphviz and Markdown renderings.

The model reports configured topology: flow and step IDs, binding pointers,
conditions with their authored operands (configuration, like case keys), case
keys, repeat limits, effective review routes and compiler diagnostics. It never
contains values bound into steps: ``literal`` bindings and ``default`` values
are reported only as present, and prompts and runtime data never appear.
Telemetry keeps its operand-free condensed conditions (``describe_condition``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from foliqant.core.conditions import describe_condition
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import JsonValue, thaw_json
from foliqant.core.plan import (
    AllConditionPlan,
    AnyConditionPlan,
    BindingPlan,
    CompiledStep,
    ConditionalRoutingPlan,
    ConditionPlan,
    DecisionStepPlan,
    Diagnostic,
    FlowCollectionStepPlan,
    FlowPlan,
    HandlerStepPlan,
    LeafConditionPlan,
    LlmStepPlan,
    MatchRoutingPlan,
    McpStepPlan,
    NotConditionPlan,
    TransitionTargetPlan,
    UnresolvedRoutingPlan,
    WorkflowPlan,
)

if TYPE_CHECKING:
    from foliqant.settings import PreparedApplication

type EdgeKind = Literal["start", "transition", "review", "collection", "retry"]
START = "start"


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One configured edge; ``target`` is a flow ID or ``outcome:<name>``."""

    source: str
    target: str
    kind: EdgeKind
    label: str
    route: Literal["direct", "cases", "route", "review", "call"]
    index: int | None = None
    case: str | None = None
    inherited: bool = False
    when: ConditionPlan | None = field(default=None, compare=False, repr=False)
    """The route entry's condition, for diagram labels relative to the source flow."""


@dataclass(frozen=True, slots=True)
class GraphFlow:
    """One flow node with its steps, bindings, routes and repeat configuration."""

    id: str
    role: Literal["routed", "callable", "retry"]
    document: Mapping[str, JsonValue]
    plan: FlowPlan = field(compare=False, repr=False)
    """The compiled flow; renderers read step details and conditions from it."""


@dataclass(frozen=True, slots=True)
class WorkflowGraph:
    """Explainable topology of one compiled workflow."""

    name: str
    revision: str
    flows: tuple[GraphFlow, ...]
    edges: tuple[GraphEdge, ...]
    outcomes: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]
    start: Mapping[str, JsonValue]

    def to_json(self) -> dict[str, JsonValue]:
        """Return the JSON document printed by ``foliqant explain``."""
        return {
            "name": self.name,
            "revision": self.revision,
            "start": dict(self.start),
            "flows": [dict(flow.document) for flow in self.flows],
            "edges": [_edge_json(edge) for edge in self.edges],
            "outcomes": list(self.outcomes),
            "diagnostics": [diagnostic_json(item) for item in self.diagnostics],
        }


def diagnostic_json(item: Diagnostic) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "code": item.code,
        "level": item.level,
        "message": item.message,
        "location": {
            "path": item.location.path,
            "line": item.location.line,
            "column": item.location.column,
        },
    }
    if item.field is not None:
        value["field"] = item.field
    return value


def _edge_json(edge: GraphEdge) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "source": edge.source,
        "target": edge.target,
        "kind": edge.kind,
        "route": edge.route,
        "label": edge.label,
    }
    if edge.index is not None:
        value["index"] = edge.index
    if edge.case is not None:
        value["case"] = edge.case
    if edge.inherited:
        value["inherited"] = True
    return value


_MAX_OPERAND = 48
_MAX_LABEL = 200


def _operand_text(value: JsonValue) -> str:
    """An authored operand, compact and bounded; simple strings stay unquoted."""
    if (
        isinstance(value, str)
        and value
        and all(character.isalnum() or character in "_-." for character in value)
    ):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))
    text = "".join(character if character.isprintable() else "?" for character in text)
    return text if len(text) <= _MAX_OPERAND else text[: _MAX_OPERAND - 3] + "..."


def _condition_source(source: BindingPlan, relative_to: str | None) -> str:
    if source.kind == "literal":
        return "literal"
    if source.kind == "first_of":
        return (
            "first_of("
            + ", ".join(_pointer_text(item, relative_to) for item in source.members)
            + ")"
        )
    return _pointer_text(source.pointer or "", relative_to)


def _pointer_text(pointer: str, relative_to: str | None) -> str:
    """Shorten pointers into ``relative_to`` (``/flows/<id>/result``) for node annotations."""
    if relative_to is not None:
        if pointer == relative_to:
            return "result"
        if pointer.startswith(relative_to + "/"):
            return pointer[len(relative_to) + 1 :]
    return pointer


def condition_label(condition: ConditionPlan, *, relative_to: str | None = None) -> str:
    """Readable condition with its authored operands: ``/x equals found``, ``in [a, b]``.

    Operands are configuration, like case keys, so they are shown; a ``literal``
    condition source stays hidden. ``relative_to`` shortens pointers below it.
    """

    def visit(item: ConditionPlan) -> str:
        if isinstance(item, LeafConditionPlan):
            source = _condition_source(item.source, relative_to)
            if item.operator in {"present", "empty"}:
                return f"{source} {item.operator}={'true' if item.operand is True else 'false'}"
            if item.operator == "length":
                return f"{source} length {item.comparison} {_operand_text(thaw_json(item.operand))}"
            if item.operator == "matches":
                pattern = item.operand if isinstance(item.operand, str) else ""
                text = "".join(c if c.isprintable() else "?" for c in pattern)
                if len(text) > _MAX_OPERAND:
                    text = text[: _MAX_OPERAND - 3] + "..."
                return f"{source} matches /{text}/"
            if item.operator in {"in", "not_in"} and isinstance(item.operand, tuple):
                values = ", ".join(_operand_text(thaw_json(value)) for value in item.operand)
                return f"{source} {item.operator} [{values}]"
            return f"{source} {item.operator} {_operand_text(thaw_json(item.operand))}"
        if isinstance(item, AllConditionPlan):
            return "all(" + "; ".join(visit(child) for child in item.operands) + ")"
        if isinstance(item, AnyConditionPlan):
            return "any(" + "; ".join(visit(child) for child in item.operands) + ")"
        return "not(" + visit(item.operand) + ")"

    text = visit(condition)
    return text if len(text) <= _MAX_LABEL else text[: _MAX_LABEL - 3] + "..."


def condition_json(condition: ConditionPlan) -> dict[str, JsonValue]:
    """Structure of a condition with its authored operands; literal sources stay hidden."""
    if isinstance(condition, AllConditionPlan):
        return {"all": [condition_json(item) for item in condition.operands]}
    if isinstance(condition, AnyConditionPlan):
        return {"any": [condition_json(item) for item in condition.operands]}
    if isinstance(condition, NotConditionPlan):
        return {"not": condition_json(condition.operand)}
    assert isinstance(condition, LeafConditionPlan)
    leaf: dict[str, JsonValue] = {"operator": condition.operator}
    if condition.operator in {"present", "empty"}:
        # The boolean selects the operator's polarity; it is not a compared value.
        leaf["operand"] = condition.operand is True
    else:
        leaf["operand"] = thaw_json(condition.operand)
    if condition.comparison is not None:
        leaf["comparison"] = condition.comparison
    source = condition.source
    if source.kind == "literal":
        leaf["literal"] = True
    elif source.kind == "first_of":
        leaf["first_of"] = list(source.members)
    else:
        leaf["pointer"] = source.pointer
    return leaf


def binding_json(binding: BindingPlan) -> dict[str, JsonValue]:
    """Pointers and presence of defaults; literal and default values are omitted."""
    if binding.kind == "literal":
        return {"literal": True}
    if binding.kind == "fields":
        return {"fields": {name: binding_json(item) for name, item in binding.fields}}
    value: dict[str, JsonValue] = (
        {"first_of": list(binding.members)}
        if binding.kind == "first_of"
        else {"pointer": binding.pointer}
    )
    if binding.has_default:
        value["has_default"] = True
    return value


def _target(target: TransitionTargetPlan) -> str:
    return target.flow if target.flow is not None else f"outcome:{target.outcome}"


def _target_json(target: TransitionTargetPlan) -> dict[str, JsonValue]:
    return {"flow": target.flow} if target.flow is not None else {"outcome": target.outcome}


def _route_json(
    route: TransitionTargetPlan | MatchRoutingPlan | ConditionalRoutingPlan | UnresolvedRoutingPlan,
) -> dict[str, JsonValue]:
    if isinstance(route, MatchRoutingPlan):
        value: dict[str, JsonValue] = {
            "binding": binding_json(route.binding),
            "cases": {key: _target_json(target) for key, target in route.cases},
            "default": _target_json(route.default),
        }
        if route.default_covers:
            value["default_covers"] = list(route.default_covers)
        return value
    if isinstance(route, ConditionalRoutingPlan):
        return {
            "route": [
                {
                    **_target_json(entry.target),
                    **(
                        {
                            "when": condition_json(entry.when),
                            "condition": describe_condition(entry.when),
                            "label": condition_label(entry.when),
                        }
                        if entry.when is not None
                        else {}
                    ),
                }
                for entry in route.entries
            ]
        }
    if isinstance(route, UnresolvedRoutingPlan):
        issues: dict[str, JsonValue] = {"default": _target_json(route.default)}
        for issue, target in route.issues:
            issues[issue] = _target_json(target)
        return issues
    return _target_json(route)


def _step_json(step: CompiledStep, prepared: PreparedApplication | None) -> dict[str, JsonValue]:
    item: dict[str, JsonValue] = {"id": step.name, "type": step.type}
    if step.when is not None:
        item["when"] = condition_json(step.when)
        item["condition"] = describe_condition(step.when)
        item["label"] = condition_label(step.when)
    if isinstance(step, (DecisionStepPlan, LlmStepPlan)):
        item["model"] = step.model
        if prepared is not None and step.model in prepared._models:
            profile = prepared._models[step.model]
            selection: dict[str, JsonValue] = {"provider": profile.provider, "model": profile.model}
            source = prepared._model_admission_groups.get(step.model)
            if source is not None:
                selection["profile"] = source
            elif step.model in prepared.config.models:
                selection["profile"] = step.model
            item["model_selection"] = selection
    if isinstance(step, DecisionStepPlan):
        item["sources"] = {name: binding_json(binding) for name, binding in step.sources}
        if step.fallback is not None:
            category: dict[str, JsonValue] = {"id": step.fallback.category.id}
            if step.fallback.category.description is not None:
                category["description"] = step.fallback.category.description
            item["fallback"] = {"category": category, "on": list(step.fallback.on)}
    elif isinstance(step, LlmStepPlan):
        item["input"] = {name: binding_json(binding) for name, binding in step.input}
        if step.tools is not None:
            item["tools"] = {"server": step.tools.server, "allow": list(step.tools.allow)}
    elif isinstance(step, McpStepPlan):
        item["server"], item["tool"] = step.server, step.tool
        item["arguments"] = {name: binding_json(binding) for name, binding in step.arguments}
    elif isinstance(step, HandlerStepPlan):
        item["handler"] = step.handler
        item["input"] = {name: binding_json(binding) for name, binding in step.input}
    elif isinstance(step, FlowCollectionStepPlan):
        item["items"] = binding_json(step.items)
        item["flows"] = list(step.flows)
        item["max_items"] = step.max_items
    return item


def _flow_json(
    flow: FlowPlan,
    role: Literal["routed", "callable", "retry"],
    prepared: PreparedApplication | None,
    retry_owner: str | None,
) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "id": flow.name,
        "role": role,
        "steps": [_step_json(step, prepared) for step in flow.steps],
    }
    if flow.callable:
        value["callable"] = True
    if retry_owner is not None:
        value["retry_for"] = retry_owner
    if flow.input:
        value["input"] = {name: binding_json(binding) for name, binding in flow.input}
    if flow.output is not None:
        value["output"] = binding_json(flow.output)
    if flow.transition is not None:
        value["transition"] = _route_json(flow.transition)
    if not flow.callable:
        value["on_unresolved"] = (
            _route_json(flow.on_unresolved)
            if flow.on_unresolved is not None
            else {"outcome": "needs_review", "implicit": True}
        )
        if flow.on_unresolved_inherited:
            value["on_unresolved_inherited"] = True
        value["available_results"] = [f"/flows/{name}/result" for name in flow.available_flows]
    if flow.repeat is not None:
        repeat: dict[str, JsonValue] = {
            "max_attempts": flow.repeat.max_attempts,
            "until": condition_json(flow.repeat.until),
            "until_condition": describe_condition(flow.repeat.until),
            "until_label": condition_label(
                flow.repeat.until, relative_to=f"/flows/{flow.name}/result"
            ),
        }
        if flow.repeat.retry_flow is not None:
            retry: dict[str, JsonValue] = {
                "flow": flow.repeat.retry_flow,
                "input": {
                    name: binding_json(binding) for name, binding in flow.repeat.retry_flow_input
                },
            }
            if flow.repeat.continue_when is not None:
                retry["continue_when"] = condition_json(flow.repeat.continue_when)
                retry["continue_condition"] = describe_condition(flow.repeat.continue_when)
                retry["continue_label"] = condition_label(flow.repeat.continue_when)
            repeat["retry"] = retry
        if flow.repeat.retry_input:
            repeat["retry_input"] = {
                name: binding_json(binding) for name, binding in flow.repeat.retry_input
            }
        value["repeat"] = repeat
    return value


def _route_edges(
    source: str,
    route: TransitionTargetPlan | MatchRoutingPlan | ConditionalRoutingPlan | UnresolvedRoutingPlan,
    kind: EdgeKind,
    *,
    inherited: bool = False,
) -> list[GraphEdge]:
    review = kind == "review"
    if isinstance(route, MatchRoutingPlan):
        edges = [
            GraphEdge(source, _target(target), kind, key, "cases", case=key)
            for key, target in route.cases
        ]
        edges.append(GraphEdge(source, _target(route.default), kind, "default", "cases"))
        return edges
    if isinstance(route, ConditionalRoutingPlan):
        return [
            GraphEdge(
                source,
                _target(entry.target),
                kind,
                f"{index}: {condition_label(entry.when)}"
                if entry.when is not None
                else f"{index}: otherwise",
                "review" if review else "route",
                index=index,
                inherited=inherited,
                when=entry.when,
            )
            for index, entry in enumerate(route.entries)
        ]
    if isinstance(route, UnresolvedRoutingPlan):
        edges = [
            GraphEdge(
                source,
                _target(route.default),
                kind,
                "review: default",
                "review",
                inherited=inherited,
            )
        ]
        edges.extend(
            GraphEdge(
                source,
                _target(target),
                kind,
                f"review: {issue}",
                "review",
                case=issue,
                inherited=inherited,
            )
            for issue, target in route.issues
        )
        return edges
    return [
        GraphEdge(
            source,
            _target(route),
            kind,
            "review" if review else "",
            "review" if review else "direct",
            inherited=inherited,
        )
    ]


def workflow_graph(
    plan: WorkflowPlan, prepared: PreparedApplication | None = None
) -> WorkflowGraph:
    """Build the graph model of one compiled plan."""
    retry_owner = {
        flow.repeat.retry_flow: flow.name
        for flow in plan.flows
        if flow.repeat is not None and flow.repeat.retry_flow is not None
    }
    flows: list[GraphFlow] = []
    edges: list[GraphEdge] = []
    if isinstance(plan.start, str):
        start: dict[str, JsonValue] = {"flow": plan.start}
        edges.append(GraphEdge(START, plan.start, "start", "", "direct"))
    else:
        start = _route_json(plan.start)
        edges.extend(_route_edges(START, plan.start, "start"))
    for flow in plan.flows:
        role: Literal["routed", "callable", "retry"] = (
            "retry" if flow.name in retry_owner else "callable" if flow.callable else "routed"
        )
        flows.append(
            GraphFlow(
                flow.name, role, _flow_json(flow, role, prepared, retry_owner.get(flow.name)), flow
            )
        )
        if flow.transition is not None:
            edges.extend(_route_edges(flow.name, flow.transition, "transition"))
        if not flow.callable:
            edges.extend(
                _route_edges(
                    flow.name,
                    flow.on_unresolved or TransitionTargetPlan(outcome="needs_review"),
                    "review",
                    inherited=flow.on_unresolved_inherited,
                )
            )
        for step in flow.steps:
            if isinstance(step, FlowCollectionStepPlan):
                edges.extend(
                    GraphEdge(flow.name, target, "collection", step.name, "call")
                    for target in step.flows
                )
        if flow.repeat is not None and flow.repeat.retry_flow is not None:
            label = "retry"
            if flow.repeat.continue_when is not None:
                label += f", continue when {condition_label(flow.repeat.continue_when)}"
            edges.append(GraphEdge(flow.name, flow.repeat.retry_flow, "retry", label, "call"))
    outcomes = tuple(
        sorted(
            {edge.target.split(":", 1)[1] for edge in edges if edge.target.startswith("outcome:")}
        )
    )
    return WorkflowGraph(
        plan.name,
        plan.revision,
        tuple(flows),
        tuple(edges),
        outcomes,
        plan.diagnostics,
        start,
    )


def explain(prepared: PreparedApplication, workflow: str | None = None) -> WorkflowGraph:
    """Return the graph model of one workflow of a prepared application.

    ``workflow`` may be omitted when exactly one workflow is configured.
    """
    if workflow is None:
        if len(prepared.plans) != 1:
            raise ServiceError(ErrorCode.INVALID_INPUT)
        workflow = next(iter(prepared.plans))
    plan = prepared.plans.get(workflow)
    if plan is None:
        raise ServiceError(ErrorCode.NOT_FOUND)
    return workflow_graph(plan, prepared)


_MAX_DIAGRAM_TEXT = 60
_TITLE_SEPARATOR = "  ·  "
_STEP_CLASSES = {
    "decision": "decision",
    "llm": "llm",
    "handler": "handler",
    "mcp": "mcp",
    "flow_collection": "collection",
}
# Soft fill and stroke per step class; the text color keeps labels readable on dark themes.
_STEP_COLORS = {
    "decision": ("#fff4e5", "#d68a1d"),
    "llm": ("#eef3ff", "#3b6fd6"),
    "handler": ("#f2f2f2", "#666666"),
    "mcp": ("#e9f8ee", "#2f9e5d"),
    "collection": ("#f6ecff", "#8a4fd6"),
}
_TEXT_COLOR = "#1f2328"
_MERMAID_SHAPES = {
    "decision": ("{{", "}}"),
    "llm": ("([", "])"),
    "handler": ("[", "]"),
    "mcp": ("[/", "/]"),
    "collection": ("[[", "]]"),
}
_DOT_SHAPES = {
    "decision": 'shape=hexagon, style="filled"',
    "llm": 'shape=box, style="rounded,filled"',
    "handler": 'shape=box, style="filled"',
    "mcp": 'shape=parallelogram, style="filled"',
    "collection": 'shape=box, peripheries=2, style="filled"',
}
# Flow IDs are snake_case without ``__``: step nodes join flow and step with ``__``,
# fixed nodes end with ``__`` and a flow ID that is a Mermaid keyword gets ``___``.
_RESERVED_IDS = frozenset(
    {
        "call",
        "class",
        "classdef",
        "click",
        "default",
        "direction",
        "end",
        "flowchart",
        "graph",
        "href",
        "interpolate",
        "linkstyle",
        "style",
        "subgraph",
    }
)
_START_NODE = "start__"
_CALLABLE_GROUP = "callable__"
_LEGEND_GROUP = "legend__"
_REVIEW_DASH = "6 4"
# Class definitions in emission order: step types, the conditional border, flow
# subgraphs, the callable and legend groups, and the start and outcome nodes.
_MERMAID_CLASSES = {
    **{
        css: f"fill:{fill},stroke:{stroke},color:{_TEXT_COLOR}"
        for css, (fill, stroke) in _STEP_COLORS.items()
    },
    "conditional": "stroke-dasharray: 4 3",
    "flow": f"fill:#fafbfc,stroke:#9aa1ab,color:{_TEXT_COLOR}",
    "group": f"fill:none,stroke:#b8bec6,stroke-dasharray: 4 3,color:{_TEXT_COLOR}",
    "terminal": f"fill:#ffffff,stroke:#57606a,color:{_TEXT_COLOR}",
}
_CALL_DASH = "1 4"
_CONDITIONAL_DASH = "4 3"


@dataclass(frozen=True, slots=True)
class _StepView:
    """One step node: its ID, class, label lines and the label of its incoming edge."""

    node: str
    css: str
    lines: tuple[str, ...]
    conditional: bool
    entry: str


def _short(text: str) -> str:
    """Bound one diagram text at 60 characters, ending a cut text with ``…``."""
    return text if len(text) <= _MAX_DIAGRAM_TEXT else text[: _MAX_DIAGRAM_TEXT - 1] + "…"


def _flow_node(name: str) -> str:
    return name + "___" if name in _RESERVED_IDS else name


def _target_node(target: str) -> str:
    if target.startswith("outcome:"):
        return f"outcome_{target.split(':', 1)[1]}__"
    return _flow_node(target)


def _step_detail(step: CompiledStep) -> str:
    """Second label line: the type with its question types, tools or called flows."""
    if isinstance(step, DecisionStepPlan):
        types = list(dict.fromkeys(question.type for question in step.questions))
        return "decision · " + ", ".join(types) if types else "decision"
    if isinstance(step, LlmStepPlan):
        return "llm · tools" if step.tools is not None else "llm"
    if isinstance(step, FlowCollectionStepPlan):
        return "flow_collection → " + ", ".join(step.flows)
    return step.type


def _step_views(flow: GraphFlow) -> list[_StepView]:
    """Steps in authored order; a ``when`` labels the edge from the previous step."""
    views: list[_StepView] = []
    previous: str | None = None
    for step in flow.plan.steps:
        lines = [step.name + ("?" if step.when is not None else ""), _short(_step_detail(step))]
        entry = ""
        if step.when is not None:
            if previous is None:
                lines.append("when " + _short(condition_label(step.when)))
            else:
                relative = f"/steps/{previous}/result"
                entry = "when " + _short(condition_label(step.when, relative_to=relative))
        views.append(
            _StepView(
                f"{flow.id}__{step.name}",
                _STEP_CLASSES.get(step.type, "handler"),
                tuple(lines),
                step.when is not None,
                entry,
            )
        )
        previous = step.name
    return views


def _flow_title(flow: GraphFlow) -> str:
    """``lookup  ·  repeat ≤ 2 until plan not_equals Unknown``; callables sit in their group."""
    parts = [flow.id]
    if flow.role == "retry":
        parts.append(f"retry for {flow.document.get('retry_for')}")
    repeat = flow.plan.repeat
    if repeat is not None:
        until = condition_label(repeat.until, relative_to=f"/flows/{flow.id}/result")
        parts.append(f"repeat ≤ {repeat.max_attempts} until {_short(until)}")
    return _TITLE_SEPARATOR.join(parts)


def _edge_text(edge: GraphEdge, flows: Mapping[str, GraphFlow]) -> str:
    """Diagram label of a flow-level edge; conditions are relative to the source flow."""
    if edge.kind == "collection":
        return "calls"
    if edge.kind == "retry":
        repeat = flows[edge.source].plan.repeat if edge.source in flows else None
        if repeat is None or repeat.continue_when is None:
            return "retry"
        continue_when = condition_label(
            repeat.continue_when, relative_to=f"/flows/{edge.target}/result"
        )
        return "retry, continue when " + _short(continue_when)
    if edge.index is not None:
        if edge.when is None:
            text = f"{edge.index}: otherwise"
        else:
            relative = None if edge.kind == "start" else f"/flows/{edge.source}/result"
            text = f"{edge.index}: " + _short(condition_label(edge.when, relative_to=relative))
        if edge.kind == "review":
            text = "review " + text
    else:
        text = _short(edge.label)
    return text + (" (default)" if edge.inherited else "")


def _partition(graph: WorkflowGraph) -> tuple[list[GraphFlow], list[GraphFlow]]:
    """Routed flows keep the main path at the top level; callable and retry flows are grouped."""
    routed = [flow for flow in graph.flows if flow.role == "routed"]
    return routed, [flow for flow in graph.flows if flow.role != "routed"]


def _mermaid_text(value: str) -> str:
    return (
        value.replace("#", "#35;")
        .replace("&", "#amp;")
        .replace('"', "#quot;")
        .replace("<", "#lt;")
        .replace(">", "#gt;")
        .replace("|", "#124;")
    )


def _mermaid_label(lines: tuple[str, ...] | list[str]) -> str:
    return "<br/>".join(_mermaid_text(line) for line in lines)


class _MermaidWriter:
    """Collects node, edge, class and link-style lines; link indices follow emission order."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.links = 0
        self.dashes: dict[str, list[int]] = {}
        self.classes: dict[str, list[str]] = {}

    def node(self, indent: str, node: str, css: str, lines: tuple[str, ...], dashed: bool) -> None:
        opening, closing = _MERMAID_SHAPES[css]
        self.lines.append(f'{indent}{node}{opening}"{_mermaid_label(lines)}"{closing}')
        self.mark(node, css)
        if dashed:
            self.mark(node, "conditional")

    def edge(self, indent: str, source: str, target: str, label: str, dash: str | None) -> None:
        arrow = "-->" if dash in {None, _CONDITIONAL_DASH} else "-.->"
        text = f'|"{_mermaid_text(label)}"|' if label else ""
        self.lines.append(f"{indent}{source} {arrow}{text} {target}")
        if dash is not None:
            self.dashes.setdefault(dash, []).append(self.links)
        self.links += 1

    def flow(self, flow: GraphFlow, indent: str) -> None:
        title = _mermaid_text(_flow_title(flow))
        self.lines.extend(
            [f'{indent}subgraph {_flow_node(flow.id)}["{title}"]', f"{indent}  direction TB"]
        )
        self.mark(_flow_node(flow.id), "flow")
        views = _step_views(flow)
        for view in views:
            self.node(indent + "  ", view.node, view.css, view.lines, view.conditional)
        for previous, view in zip(views, views[1:], strict=False):
            dash = _CONDITIONAL_DASH if view.conditional else None
            self.edge(indent + "  ", previous.node, view.node, view.entry, dash)
        self.lines.append(f"{indent}end")

    def legend(self) -> None:
        self.lines.extend([f'  subgraph {_LEGEND_GROUP}["legend"]', "    direction LR"])
        self.mark(_LEGEND_GROUP, "group")
        for step_type, css in _STEP_CLASSES.items():
            self.node("    ", f"legend_{css}__", css, (step_type,), False)
        self.node("    ", "legend_conditional__", "handler", ("step?", "when …"), True)
        # Invisible links keep the unconnected legend nodes in one row; they count as links.
        nodes = [f"legend_{css}__" for css in _STEP_CLASSES.values()] + ["legend_conditional__"]
        self.lines.append("    " + " ~~~ ".join(nodes))
        self.links += len(nodes) - 1
        self.lines.append("  end")

    def mark(self, node: str, css: str) -> None:
        self.classes.setdefault(css, []).append(node)

    def finish(self) -> list[str]:
        lines = list(self.lines)
        used = [css for css in _MERMAID_CLASSES if css in self.classes]
        lines.extend(f"  classDef {css} {_MERMAID_CLASSES[css]}" for css in used)
        lines.extend(f"  class {','.join(self.classes[css])} {css}" for css in used)
        for dash in (_CONDITIONAL_DASH, _REVIEW_DASH, _CALL_DASH):
            if dash in self.dashes:
                indices = ",".join(str(index) for index in self.dashes[dash])
                lines.append(f"  linkStyle {indices} stroke-dasharray: {dash}")
        return lines


def _edge_dash(edge: GraphEdge) -> str | None:
    if edge.kind == "review":
        return _REVIEW_DASH
    if edge.kind in {"collection", "retry"}:
        return _CALL_DASH
    return None


def render_mermaid(graph: WorkflowGraph, *, diagnostics: bool = True, legend: bool = False) -> str:
    """Render a Mermaid flowchart with one subgraph per flow and its steps inside.

    Step shapes and colors follow the step type; ``?`` and a dashed border mark
    a step with ``when``, whose condition labels the edge from the previous
    step. Flow edges connect subgraphs: routes solid, review routes dashed,
    collection and retry calls dotted; a repeat is annotated in the flow title.
    Callable and retry flows are grouped in ``callable flows``. ``legend``
    appends a node per step type; diagnostics follow as comments unless
    ``diagnostics`` is false.
    """
    writer = _MermaidWriter()
    writer.lines.extend(["flowchart TD", f"  {_START_NODE}((start))"])
    writer.mark(_START_NODE, "terminal")
    routed, called = _partition(graph)
    for flow in routed:
        writer.flow(flow, "  ")
    if called:
        writer.lines.extend([f'  subgraph {_CALLABLE_GROUP}["callable flows"]', "    direction TB"])
        writer.mark(_CALLABLE_GROUP, "group")
        for flow in called:
            writer.flow(flow, "    ")
        writer.lines.append("  end")
    for outcome in graph.outcomes:
        writer.lines.append(f'  outcome_{outcome}__(["{_mermaid_text(outcome)}"])')
        writer.mark(f"outcome_{outcome}__", "terminal")
    flows = {flow.id: flow for flow in graph.flows}
    for edge in graph.edges:
        source = _START_NODE if edge.kind == "start" else _flow_node(edge.source)
        writer.edge(
            "  ", source, _target_node(edge.target), _edge_text(edge, flows), _edge_dash(edge)
        )
    if legend:
        writer.legend()
    lines = writer.finish()
    for item in graph.diagnostics if diagnostics else ():
        lines.append(
            f"  %% {item.level} {item.code} {item.location.path}:{item.location.line}: "
            + item.message.replace("\n", " ")
        )
    return "\n".join(lines) + "\n"


_DOT_DEFAULTS = (
    f'  graph [fontname="Helvetica", fontsize=12, fontcolor="{_TEXT_COLOR}"];',
    f'  node [fontname="Helvetica", fontsize=11, fontcolor="{_TEXT_COLOR}"];',
    '  edge [fontname="Helvetica", fontsize=10];',
)


def _dot_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _dot_node(node: str, css: str, lines: tuple[str, ...], conditional: bool) -> str:
    fill, stroke = _STEP_COLORS[css]
    shape = _DOT_SHAPES[css]
    if conditional:
        shape = shape.replace('style="', 'style="dashed,')
    label = _dot_text("\n".join(lines))
    return f'"{node}" [{shape}, fillcolor="{fill}", color="{stroke}", label="{label}"];'


def _dot_flow(flow: GraphFlow, indent: str) -> list[str]:
    lines = [
        f'{indent}subgraph "cluster_{flow.id}" {{',
        f'{indent}  label="{_dot_text(_flow_title(flow))}";',
        f'{indent}  style="rounded,filled";',
        f'{indent}  fillcolor="#fafbfc";',
        f'{indent}  color="#9aa1ab";',
    ]
    views = _step_views(flow)
    lines.extend(
        indent + "  " + _dot_node(view.node, view.css, view.lines, view.conditional)
        for view in views
    )
    for previous, view in zip(views, views[1:], strict=False):
        attributes = [f'label="{_dot_text(view.entry)}"'] if view.entry else []
        if view.conditional:
            attributes.append("style=dashed")
        suffix = f" [{', '.join(attributes)}]" if attributes else ""
        lines.append(f'{indent}  "{previous.node}" -> "{view.node}"{suffix};')
    lines.append(f"{indent}}}")
    return lines


def _dot_legend() -> list[str]:
    lines = [
        f'  subgraph "cluster_{_LEGEND_GROUP}" {{',
        '    label="legend";',
        "    style=dashed;",
        '    color="#b8bec6";',
    ]
    for step_type, css in _STEP_CLASSES.items():
        lines.append("    " + _dot_node(f"legend_{css}__", css, (step_type,), False))
    lines.append("    " + _dot_node("legend_conditional__", "handler", ("step?", "when …"), True))
    lines.append("  }")
    return lines


def render_dot(graph: WorkflowGraph, *, diagnostics: bool = True, legend: bool = False) -> str:
    """Render a Graphviz digraph with the structure and styles of :func:`render_mermaid`.

    Each flow is a cluster with its steps; flow edges run between clusters
    (``compound=true``), from the last step of the source to the first step
    of the target.
    """
    lines = [
        f'digraph "{_dot_text(graph.name)}" {{',
        "  rankdir=TB;",
        "  compound=true;",
        *_DOT_DEFAULTS,
        f'  "{_START_NODE}" [shape=circle, color="#57606a", label="start"];',
    ]
    routed, called = _partition(graph)
    for flow in routed:
        lines.extend(_dot_flow(flow, "  "))
    if called:
        lines.extend(
            [
                f'  subgraph "cluster_{_CALLABLE_GROUP}" {{',
                '    label="callable flows";',
                "    style=dashed;",
                '    color="#b8bec6";',
            ]
        )
        for flow in called:
            lines.extend(_dot_flow(flow, "    "))
        lines.append("  }")
    for outcome in graph.outcomes:
        lines.append(
            f'  "outcome_{outcome}__" [shape=box, style=rounded, color="#57606a", '
            f'label="{_dot_text(outcome)}"];'
        )
    flows = {flow.id: flow for flow in graph.flows}
    for edge in graph.edges:
        # Edges run from the last step of the source to the first step of the target,
        # clipped at the cluster borders.
        attributes: list[str] = []
        if edge.kind == "start":
            tail = _START_NODE
        else:
            tail = f"{edge.source}__{flows[edge.source].plan.steps[-1].name}"
            if edge.target != edge.source:
                attributes.append(f'ltail="cluster_{edge.source}"')
        if edge.target.startswith("outcome:"):
            head = _target_node(edge.target)
        else:
            head = f"{edge.target}__{flows[edge.target].plan.steps[0].name}"
            if edge.target != edge.source:
                attributes.append(f'lhead="cluster_{edge.target}"')
        label = _edge_text(edge, flows)
        if label:
            attributes.append(f'label="{_dot_text(label)}"')
        if edge.kind == "review":
            attributes.append("style=dashed")
        elif edge.kind in {"collection", "retry"}:
            attributes.append("style=dotted")
        suffix = f" [{', '.join(attributes)}]" if attributes else ""
        lines.append(f'  "{tail}" -> "{head}"{suffix};')
    if legend:
        lines.extend(_dot_legend())
    lines.append("}")
    for item in graph.diagnostics if diagnostics else ():
        lines.append(
            f"// {item.level} {item.code} {item.location.path}:{item.location.line}: "
            + item.message.replace("\n", " ")
        )
    return "\n".join(lines) + "\n"


def _legend_document(output_format: Literal["mermaid", "dot"]) -> str:
    """A diagram holding only the legend, placed once at the top of a document."""
    if output_format == "dot":
        header = ['digraph "legend" {', "  rankdir=TB;", *_DOT_DEFAULTS]
        return "\n".join([*header, *_dot_legend(), "}"])
    writer = _MermaidWriter()
    writer.lines.append("flowchart LR")
    writer.legend()
    return "\n".join(writer.finish())


def _start_text(plan: WorkflowPlan) -> str:
    if isinstance(plan.start, str):
        return f"`{plan.start}`"
    parts = [
        f"`{_target(entry.target)}` when `{condition_label(entry.when)}`"
        if entry.when is not None
        else f"otherwise `{_target(entry.target)}`"
        for entry in plan.start.entries
    ]
    return "; ".join(parts)


def _output_text(binding: BindingPlan | None) -> str:
    """The workflow output projection, without literal or default values."""
    if binding is None:
        return "the accepted input payload"
    if binding.kind == "literal":
        return "a literal value"
    if binding.kind == "fields":
        return "an object with " + ", ".join(f"`{name}`" for name, _ in binding.fields)
    fallback = ", else its default" if binding.has_default else ""
    if binding.kind == "first_of":
        return (
            "the first present of " + ", ".join(f"`{item}`" for item in binding.members) + fallback
        )
    return f"`{binding.pointer}`{fallback}"


def _diagnostic_line(item: Diagnostic) -> str:
    location = f"{item.location.path}:{item.location.line}:{item.location.column}"
    field = f" at `{item.field}`" if item.field else ""
    return f"- {item.level} `{item.code}` in `{location}`{field}: {item.message}"


def render_document(
    prepared: PreparedApplication,
    output_format: Literal["mermaid", "dot"] = "mermaid",
    *,
    legend: bool = True,
) -> str:
    """Render every workflow as one Markdown document for configuration documentation.

    The document starts with one legend of step shapes and edge styles unless
    ``legend`` is false. Each workflow gets a ``## <name>`` section with its
    start and output, the fenced diagram and its diagnostics. The text is
    deterministic for the same configuration, so a stored copy can be checked
    for drift (``foliqant explain --format mermaid --all --output PATH --check``).
    """
    render = render_mermaid if output_format == "mermaid" else render_dot
    lines = [
        "# Workflows",
        "",
        f"Generated by `foliqant explain --format {output_format} --all`; regenerate it after "
        "changing the configuration instead of editing it.",
        "",
    ]
    if legend:
        lines.extend(
            [
                "Each flow is a box with its steps in order; the shape and color show the step "
                "type. A dashed step marked `?` runs only when its condition, shown on the edge "
                "into it, holds. Solid edges are routes, dashed edges review routes and dotted "
                "edges calls of the flows grouped under callable flows.",
                "",
                f"```{output_format}",
                _legend_document(output_format),
                "```",
                "",
            ]
        )
    for name in sorted(prepared.plans):
        plan = prepared.plans[name]
        graph = workflow_graph(plan, prepared)
        lines.extend(
            [
                f"## {name}",
                "",
                f"Start: {_start_text(plan)}. Output: {_output_text(plan.output)}.",
                "",
                f"```{output_format}",
                render(graph, diagnostics=False).rstrip("\n"),
                "```",
                "",
            ]
        )
        if plan.diagnostics:
            lines.append("Diagnostics:")
            lines.append("")
            lines.extend(_diagnostic_line(item) for item in plan.diagnostics)
        else:
            lines.append("Diagnostics: none.")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


__all__ = [
    "GraphEdge",
    "GraphFlow",
    "WorkflowGraph",
    "condition_label",
    "explain",
    "render_document",
    "render_dot",
    "render_mermaid",
    "workflow_graph",
]
