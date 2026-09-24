"""Offline graph model of compiled workflows, with Mermaid and Graphviz renderings.

The model reports configured topology only: flow and step IDs, binding
pointers, condensed conditions (pointers and operators), case keys, repeat
limits, effective review routes and compiler diagnostics. It never contains
literal binding values, condition operands, defaults, prompts or runtime data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from foliqant.core.conditions import describe_condition
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import JsonValue
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


@dataclass(frozen=True, slots=True)
class GraphFlow:
    """One flow node with its steps, bindings, routes and repeat configuration."""

    id: str
    role: Literal["routed", "callable", "retry"]
    document: Mapping[str, JsonValue]


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


def condition_json(condition: ConditionPlan) -> dict[str, JsonValue]:
    """Structure of a condition without operand values."""
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
                f"{index}: {describe_condition(entry.when)}"
                if entry.when is not None
                else f"{index}: otherwise",
                "review" if review else "route",
                index=index,
                inherited=inherited,
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
            GraphFlow(flow.name, role, _flow_json(flow, role, prepared, retry_owner.get(flow.name)))
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
            edges.append(GraphEdge(flow.name, flow.repeat.retry_flow, "retry", "retry", "call"))
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


def _mermaid_text(value: str) -> str:
    return (
        value.replace("&", "#amp;")
        .replace('"', "#quot;")
        .replace("<", "#lt;")
        .replace(">", "#gt;")
        .replace("|", "#124;")
    )


def _node(name: str) -> str:
    if name == START:
        return "start"
    if name.startswith("outcome:"):
        return "outcome_" + name.split(":", 1)[1]
    return "flow_" + name


def _flow_label(flow: GraphFlow, *, separator: str) -> str:
    steps = flow.document.get("steps")
    names: list[str] = []
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                names.append(f"{step['id']}{'?' if 'when' in step else ''} ({step['type']})")
    lines = [flow.id + (" [callable]" if flow.role == "callable" else "")]
    if flow.role == "retry":
        lines[0] = flow.id + " [retry]"
    lines.extend(names)
    repeat = flow.document.get("repeat")
    if isinstance(repeat, dict):
        lines.append(f"repeat <= {repeat['max_attempts']} until {repeat['until_condition']}")
    return separator.join(lines)


def render_mermaid(graph: WorkflowGraph) -> str:
    """Render a Mermaid flowchart: solid routes, dashed review, dotted calls.

    Conditional steps carry a ``?`` suffix; diagnostics follow as comments.
    """
    lines = ["flowchart TD", "  start((start))"]
    for flow in graph.flows:
        label = _mermaid_text(_flow_label(flow, separator="\n")).replace("\n", "<br/>")
        lines.append(f'  {_node(flow.id)}["{label}"]')
    for outcome in graph.outcomes:
        lines.append(f'  outcome_{outcome}(["{outcome}"])')
    styles: list[str] = []
    for index, edge in enumerate(graph.edges):
        arrow = "-->" if edge.kind in {"start", "transition"} else "-.->"
        label = edge.label + (" (default)" if edge.inherited else "")
        text = f'|"{_mermaid_text(label)}"|' if label else ""
        lines.append(f"  {_node(edge.source)} {arrow}{text} {_node(edge.target)}")
        if edge.kind == "review":
            styles.append(f"  linkStyle {index} stroke-dasharray: 6 4")
        elif edge.kind in {"collection", "retry"}:
            styles.append(f"  linkStyle {index} stroke-dasharray: 1 4")
    index = len(graph.edges)
    for flow in graph.flows:
        repeat = flow.document.get("repeat")
        if isinstance(repeat, dict):
            # Repetition is an attribute of the node, drawn as a self-loop annotation.
            label = _mermaid_text(f"repeat <= {repeat['max_attempts']}")
            lines.append(f'  {_node(flow.id)} -.->|"{label}"| {_node(flow.id)}')
            styles.append(f"  linkStyle {index} stroke-dasharray: 1 4")
            index += 1
    lines.extend(styles)
    for item in graph.diagnostics:
        lines.append(
            f"  %% {item.level} {item.code} {item.location.path}:{item.location.line}: "
            + item.message.replace("\n", " ")
        )
    return "\n".join(lines) + "\n"


def _dot_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_dot(graph: WorkflowGraph) -> str:
    """Render a Graphviz digraph with the same edge styles as :func:`render_mermaid`."""
    lines = [f'digraph "{_dot_text(graph.name)}" {{', "  rankdir=TB;"]
    lines.append('  "start" [shape=circle, label="start"];')
    for flow in graph.flows:
        label = _dot_text(_flow_label(flow, separator="\n"))
        shape = "box" if flow.role == "routed" else "box, style=dashed"
        lines.append(f'  "{_node(flow.id)}" [shape={shape}, label="{label}"];')
    for outcome in graph.outcomes:
        lines.append(f'  "outcome_{outcome}" [shape=box, style=rounded, label="{outcome}"];')
    for edge in graph.edges:
        attributes = []
        label = edge.label + (" (default)" if edge.inherited else "")
        if label:
            attributes.append(f'label="{_dot_text(label)}"')
        if edge.kind == "review":
            attributes.append("style=dashed")
        elif edge.kind in {"collection", "retry"}:
            attributes.append("style=dotted")
        suffix = f" [{', '.join(attributes)}]" if attributes else ""
        lines.append(f'  "{_node(edge.source)}" -> "{_node(edge.target)}"{suffix};')
    for flow in graph.flows:
        repeat = flow.document.get("repeat")
        if isinstance(repeat, dict):
            node = _node(flow.id)
            label = _dot_text(f"repeat <= {repeat['max_attempts']}")
            lines.append(f'  "{node}" -> "{node}" [label="{label}", style=dotted];')
    lines.append("}")
    for item in graph.diagnostics:
        lines.append(
            f"// {item.level} {item.code} {item.location.path}:{item.location.line}: "
            + item.message.replace("\n", " ")
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "GraphEdge",
    "GraphFlow",
    "WorkflowGraph",
    "explain",
    "render_dot",
    "render_mermaid",
    "workflow_graph",
]
