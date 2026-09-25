"""Build inline workflow documents and compile them with declared handler contracts."""

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from foliqant.compiler import compile_workflow
from foliqant.core.json import FrozenObject, freeze_json
from foliqant.core.plan import WorkflowPlan
from foliqant.settings import HandlerContract

LOOKUP_OUTPUT = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["found", "not_found", "ambiguous"]},
        "fund": {"type": ["string", "null"]},
        "count": {"type": "integer"},
    },
    "required": ["status"],
    "additionalProperties": False,
}
IDENTIFIER_INPUT = {
    "type": "object",
    "properties": {"identifier": {"type": "string"}},
    "required": ["identifier"],
    "additionalProperties": False,
}
CORRECTION_OUTPUT = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["accepted", "rejected"]},
        "identifier": {"type": "string"},
    },
    "required": ["status", "identifier"],
    "additionalProperties": False,
}


def _frozen(value: object) -> FrozenObject:
    frozen = freeze_json(value)
    assert isinstance(frozen, Mapping)
    return frozen


CONTRACTS: Mapping[str, HandlerContract] = MappingProxyType(
    {
        "echo": HandlerContract(_frozen({"type": "object"}), _frozen({}), "read"),
        "lookup": HandlerContract(_frozen(IDENTIFIER_INPUT), _frozen(LOOKUP_OUTPUT), "read"),
        "correct": HandlerContract(_frozen({"type": "object"}), _frozen(CORRECTION_OUTPUT), "read"),
        **{
            name: HandlerContract(_frozen({"type": "object"}), _frozen({}), "read")
            for name in ("check", "repair", "recheck", "label", "pick")
        },
        "text": HandlerContract(
            _frozen({"type": "object"}),
            _frozen(
                {
                    "type": "object",
                    "properties": {"body": {"type": "string"}},
                    "required": ["body"],
                }
            ),
            "read",
        ),
    }
)


def handler(name: str = "echo", **inputs: Any) -> dict[str, Any]:
    """A handler step whose inputs are pointers (str) or explicit binding objects."""
    return {
        "type": "handler",
        "handler": name,
        "input": {
            key: {"pointer": value} if isinstance(value, str) else value
            for key, value in inputs.items()
        },
    }


def step(step_id: str, definition: dict[str, Any], when: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": step_id, "definition": definition}
    if when is not None:
        entry["when"] = when
    return entry


def flow(
    *steps: dict[str, Any],
    input: dict[str, Any] | None = None,
    output: Any = None,
    transition: Any = None,
    input_schema: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    definition: dict[str, Any] = {"steps": list(steps)}
    if output is not None:
        definition["output"] = output
    if input_schema is not None:
        definition["input_schema"] = input_schema
    document: dict[str, Any] = {
        "input": input if input is not None else {},
        "definition": definition,
        "transition": transition if transition is not None else {"outcome": "completed"},
    }
    document.update(extra)
    return document


def callable_flow(*steps: dict[str, Any], output: Any = None, **definition: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"steps": list(steps), **definition}
    if output is not None:
        body["output"] = output
    return {"callable": True, "definition": body}


def when(pointer: str, **operator: Any) -> dict[str, Any]:
    return {"binding": {"pointer": pointer}, **operator}


def compile_document(
    tmp_path: Path,
    flows: dict[str, Any],
    *,
    max_steps: int | None = None,
    **workflow: Any,
) -> WorkflowPlan:
    document: dict[str, Any] = {"name": "demo", "flows": flows, **workflow}
    document.setdefault("start", next(iter(flows)))
    document.setdefault("defaults", {}).setdefault("model", "local")
    (tmp_path / "workflow.yaml").write_text(json.dumps(document, indent=2))
    return compile_workflow(
        tmp_path,
        model_aliases={"local": "test-model"},
        tool_catalogs={},
        handler_names=set(CONTRACTS),
        handler_schemas=CONTRACTS,
        max_steps=max_steps,
    )


def codes(plan: WorkflowPlan) -> list[str]:
    return [item.code for item in plan.diagnostics]
