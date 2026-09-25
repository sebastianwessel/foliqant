"""Static values of pointers in flow scope and workflow boundary scope."""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Protocol, cast

from foliqant.contracts.workflow import DeclaredToolCatalog
from foliqant.core.json import FrozenObject, thaw_json
from foliqant.core.plan import (
    BindingPlan,
    CompiledStep,
    DecisionQuestionPlan,
    DecisionStepPlan,
    FlowCollectionStepPlan,
    FlowPlan,
    HandlerStepPlan,
    LlmStepPlan,
    McpStepPlan,
)

from .static_schema import SchemaView
from .static_values import Arr, Const, Obj, Static, of, pointer, types, union

_ISSUES = ["no_supported_answer", "conflicting_information", "multiple_valid_options"]
_STATUSES = ["completed", "needs_review", "failed", "cancelled", "skipped"]
_ERROR_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "retryable": {"type": "boolean"},
    },
    "additionalProperties": False,
}


class HandlerSchemas(Protocol):
    """Structural schema interface of declared or registered handler contracts."""

    @property
    def input_schema(self) -> FrozenObject | None: ...

    @property
    def output_schema(self) -> FrozenObject | None: ...


def tokens(pointer: str) -> list[str]:
    """Split an RFC 6901 pointer into unescaped reference tokens."""
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]


def collection_result_schema() -> dict[str, object]:
    """Known ledger structure; child business results keep their authored types."""
    return {
        "type": "object",
        "required": ["items"],
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "flow": {"type": "string"},
                        "status": {"type": "string"},
                        "steps": {"type": "object"},
                        "result": {},
                        "usage": {"type": "object"},
                        "elapsed_seconds": {"type": "number"},
                        "error": {"type": "object"},
                        "attempt_count": {"type": "integer"},
                        "attempts": {"type": "array"},
                        "attempts_usage": {"type": "object"},
                        "attempts_elapsed_seconds": {"type": "number"},
                        "repeat": {
                            "type": "object",
                            "properties": {
                                "stopped_by": {
                                    "type": "string",
                                    "enum": [
                                        "until",
                                        "exhausted",
                                        "continue_when",
                                        "review",
                                        "failure",
                                    ],
                                }
                            },
                        },
                        "retry": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            }
        },
    }


def _question_schema(question: DecisionQuestionPlan) -> dict[str, object]:
    options = [option.id for option in question.options]
    answer: dict[str, object]
    if question.type == "choice":
        answer = {
            "anyOf": [
                {"type": "null"},
                {"type": "object", "properties": {"optionId": {"type": "string", "enum": options}}},
            ]
        }
    elif question.type == "multiselect":
        answer = {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "optionIds": {
                            "type": "array",
                            "items": {"type": "string", "enum": options},
                        }
                    },
                },
            ]
        }
    elif question.type == "predicate":
        answer = {
            "type": "object",
            "properties": {"value": {"type": "string", "enum": ["true", "false", "unknown"]}},
        }
    elif question.type == "ordinal":
        answer = {
            "anyOf": [
                {"type": "null"},
                {"type": "object", "properties": {"levelId": {"type": "string", "enum": options}}},
            ]
        }
    else:
        answer = {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {"units": {"type": "array"}, "relations": {"type": "array"}},
                },
            ]
        }
    return {
        "type": "object",
        "properties": {
            "questionId": {"type": "string", "const": question.id},
            "type": {"type": "string", "const": question.type},
            "answer": answer,
            "answerability": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["answerable", "not_answerable", "undetermined"],
                    },
                    "issues": {"type": "array", "items": {"type": "string", "enum": _ISSUES}},
                },
            },
            "reason": {"type": "string"},
            "evidence_strength": {"enum": ["limited", "strong", None]},
        },
    }


def decision_result_schema(step: DecisionStepPlan) -> dict[str, object]:
    """Derive the validated native result shape; objects stay open for unknown keys."""
    if step.question_mode == "single":
        return _question_schema(step.questions[0])
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {"anyOf": [_question_schema(question) for question in step.questions]},
            }
        },
    }


def _selection_schema(step: CompiledStep) -> dict[str, object]:
    category: dict[str, object] = {"type": "string"}
    if (
        isinstance(step, DecisionStepPlan)
        and step.question_mode == "single"
        and step.questions[0].type == "choice"
    ):
        ids = [option.id for option in step.questions[0].options]
        if step.fallback is not None:
            ids.append(step.fallback.category.id)
        category = {"type": "string", "enum": ids}
    return {
        "type": "object",
        "properties": {
            "origin": {"type": "string", "enum": ["model", "fallback"]},
            "category": {
                "type": "object",
                "properties": {"id": category, "description": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def _record_schema(step: CompiledStep) -> dict[str, object]:
    """Step record fields a later binding can read.

    ``error`` and ``partial_result`` exist only on failure, which stops the flow,
    so no binding can read them. ``kind`` exists for collections only and
    ``selection`` only for single-choice decisions and handlers.
    """
    properties: dict[str, object] = {
        "status": {"type": "string", "enum": _STATUSES},
        "result": {},
    }
    if isinstance(step, FlowCollectionStepPlan):
        properties["kind"] = {"const": "flow_collection"}
    if isinstance(step, HandlerStepPlan) or (
        isinstance(step, DecisionStepPlan)
        and step.question_mode == "single"
        and step.questions[0].type == "choice"
    ):
        properties["selection"] = _selection_schema(step)
    return {"type": "object", "properties": properties, "additionalProperties": False}


def binding_static(binding: BindingPlan, resolve: "PointerResolver") -> Static:
    """Static value of any binding kind, including its authored default."""
    if binding.kind == "literal":
        return of(Const(thaw_json(binding.literal)))
    if binding.kind == "fields":
        return of(Obj({name: binding_static(item, resolve) for name, item in binding.fields}))
    if binding.kind == "first_of":
        members = [resolve(member) for member in binding.members]
        # A null member is skipped like a missing one.
        absent = all(
            member.absent or (kinds := types(member)) is None or "null" in kinds
            for member in members
        )
        selected = union(members, absent=absent)
    else:
        selected = resolve(binding.pointer or "")
    if binding.has_default:
        return Static((*selected.alternatives, Const(thaw_json(binding.default))), False)
    return selected


class PointerResolver(Protocol):
    def __call__(self, pointer: str, /) -> Static: ...


class FlowScope:
    """Pointers inside one flow: ``/payload``, ``/metadata`` and ``/steps/<id>``."""

    def __init__(
        self,
        steps: Mapping[str, CompiledStep],
        input_path: str | None,
        schemas: Mapping[str, dict[str, object]],
        catalogs: Mapping[str, DeclaredToolCatalog],
        handlers: Mapping[str, HandlerSchemas],
    ) -> None:
        self.steps = steps
        self.input_path = input_path
        self.schemas = schemas
        self.catalogs = catalogs
        self.handlers = handlers

    def view(self, schema: dict[str, object], path: str = "") -> SchemaView:
        return SchemaView(schema, schema, path, self.schemas)

    def payload(self) -> Static:
        if self.input_path is not None:
            return of(self.view(self.schemas[self.input_path], self.input_path))
        return of(self.view({}))

    def step_result(self, step: CompiledStep) -> Static:
        if isinstance(step, FlowCollectionStepPlan):
            return of(self.view(collection_result_schema()))
        if isinstance(step, LlmStepPlan):
            if step.output_schema_path is not None:
                return of(self.view(self.schemas[step.output_schema_path], step.output_schema_path))
            return of(self.view({"type": "string"}))
        if isinstance(step, McpStepPlan):
            schema = self.catalogs[step.server].tools[step.tool].output_schema or {}
            return of(self.view(cast(dict[str, object], schema)))
        if isinstance(step, HandlerStepPlan):
            contract = self.handlers.get(step.handler)
            if contract is not None and contract.output_schema is not None:
                return of(self.view(cast(dict[str, object], thaw_json(contract.output_schema))))
            return of(self.view({}))
        if isinstance(step, DecisionStepPlan):
            return of(self.view(decision_result_schema(step)))
        return of(self.view({}))

    def __call__(self, value: str, /) -> Static:
        parts = tokens(value)
        if not parts:
            return of(self.view({"type": "object"}))
        if parts[0] == "payload":
            return pointer(self.payload(), parts[1:])
        if parts[0] == "metadata":
            return pointer(of(self.view({"type": "object"})), parts[1:])
        if parts[0] == "steps":
            if len(parts) == 1:
                return of(self.view({"type": "object"}))
            step = self.steps.get(parts[1])
            if step is None:
                return Static((), True)
            if len(parts) >= 3 and parts[2] == "result":
                return pointer(self.step_result(step), parts[3:])
            return pointer(of(self.view(_record_schema(step))), parts[2:])
        return of(self.view({}))

    def binding(self, binding: BindingPlan) -> Static:
        return binding_static(binding, self)


def completion_binding(binding: BindingPlan, conditional: frozenset[str]) -> BindingPlan:
    """A flow output as projected after the flow completed.

    Every unconditional step ran, so the default of a pointer to one of them
    can never apply; defaults of conditional steps, of other pointers and of
    ``first_of`` candidates stay.
    """
    if binding.kind == "fields":
        return replace(
            binding,
            fields=tuple(
                (name, completion_binding(item, conditional)) for name, item in binding.fields
            ),
        )
    if binding.kind != "pointer" or not binding.has_default or binding.pointer is None:
        return binding
    parts = tokens(binding.pointer)
    if len(parts) >= 2 and parts[0] == "steps" and parts[1] not in conditional:
        return replace(binding, has_default=False, default=None)
    return binding


class WorkflowScope:
    """Boundary pointers: ``/payload``, ``/metadata`` and ``/flows/<id>/result|attempts``.

    ``completed`` names flows known to have completed where the pointer is
    resolved (not stopped for review); their output defaults for unconditional
    steps are then known not to apply.
    """

    def __init__(
        self,
        flows: Mapping[str, FlowPlan],
        flow_scopes: Mapping[str, FlowScope],
        input_path: str | None,
        schemas: Mapping[str, dict[str, object]],
        completed: frozenset[str] = frozenset(),
    ) -> None:
        self.flows = flows
        self.flow_scopes = flow_scopes
        self.input_path = input_path
        self.schemas = schemas
        self.completed = completed

    def completed_at(self, names: Iterable[str]) -> "WorkflowScope":
        """The same scope where ``names`` are known to have completed."""
        return WorkflowScope(
            self.flows, self.flow_scopes, self.input_path, self.schemas, frozenset(names)
        )

    def view(self, schema: dict[str, object], path: str = "") -> SchemaView:
        return SchemaView(schema, schema, path, self.schemas)

    def flow_result(self, name: str) -> Static:
        flow = self.flows[name]
        if flow.output is None:
            if flow.input_schema_path is not None:
                return of(self.view(self.schemas[flow.input_schema_path], flow.input_schema_path))
            return of(self.view({"type": "object"}))
        output = flow.output
        if name in self.completed:
            conditional = frozenset(step.name for step in flow.steps if step.when is not None)
            output = completion_binding(output, conditional)
        return self.flow_scopes[name].binding(output)

    def attempts(self, name: str) -> Static:
        """Bindable attempt entries; step records, usage and time stay in the result only."""
        result = self.flow_result(name)
        record = Obj(
            {
                "attempt": of(self.view({"type": "integer", "minimum": 1})),
                "status": of(self.view({"type": "string", "enum": _STATUSES})),
                "result": Static(result.alternatives, True),
                # Only a failed attempt has an error, and a failure stops the run.
                "error": Static((self.view(_ERROR_SCHEMA),), True, True),
            }
        )
        return of(Arr(of(record)))

    def __call__(self, value: str, /) -> Static:
        parts = tokens(value)
        if not parts:
            return of(self.view({"type": "object"}))
        if parts[0] == "payload":
            if self.input_path is not None:
                base = of(self.view(self.schemas[self.input_path], self.input_path))
            else:
                base = of(self.view({}))
            return pointer(base, parts[1:])
        if parts[0] == "metadata":
            return pointer(of(self.view({"type": "object"})), parts[1:])
        if parts[0] == "flows" and len(parts) >= 3 and parts[1] in self.flows:
            if parts[2] == "result":
                return pointer(self.flow_result(parts[1]), parts[3:])
            if parts[2] == "attempts":
                return pointer(self.attempts(parts[1]), parts[3:])
        return of(self.view({}))

    def binding(self, binding: BindingPlan) -> Static:
        return binding_static(binding, self)


__all__ = [
    "FlowScope",
    "HandlerSchemas",
    "WorkflowScope",
    "binding_static",
    "collection_result_schema",
    "decision_result_schema",
    "tokens",
]
