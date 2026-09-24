"""Conservative static value sets for bindings, conditions and route coverage.

A :class:`Static` is a union of alternatives a pointer may resolve to at run
time: schema nodes, authored constants (literals and defaults), projected
objects (``output.fields``) and arrays (repeat attempts). ``absent`` records
that the value may be missing. ``missing`` records that it may be missing for a
structural reason independent of business data: a constant or projected object
without the selected key, or a repeat attempt after the first. Unknown schemas
stay unknown; these checks never claim a general subschema proof.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast

from .static_schema import SchemaView, json_type, schema_types

_MAX_EXPANSION = 8


@dataclass(frozen=True)
class Const:
    """An authored constant: a literal or a binding default."""

    value: object


@dataclass(frozen=True)
class Obj:
    """An object projected with exactly these keys (``output.fields``)."""

    fields: Mapping[str, "Static"]


@dataclass(frozen=True)
class Arr:
    """An array whose items all have the same static value."""

    items: "Static"


type Alternative = SchemaView | Const | Obj | Arr


@dataclass(frozen=True)
class Static:
    alternatives: tuple[Alternative, ...]
    absent: bool = False
    missing: bool = False

    @property
    def impossible(self) -> bool:
        """The path cannot hold any value (it is always absent)."""
        return not self.alternatives


def of(*alternatives: Alternative, absent: bool = False) -> Static:
    return Static(tuple(alternatives), absent)


def union(items: Iterable[Static], *, absent: bool | None = None) -> Static:
    collected: list[Alternative] = []
    may_be_absent = False
    structural = False
    for item in items:
        collected.extend(item.alternatives)
        may_be_absent = may_be_absent or item.absent
        structural = structural or item.missing
    return Static(tuple(collected), may_be_absent if absent is None else absent, structural)


def unknown(resources: Mapping[str, dict[str, object]]) -> Static:
    return of(SchemaView({}, {}, "", resources))


def _expand(view: SchemaView, depth: int = 0) -> list[SchemaView]:
    """Split ``anyOf``/``oneOf`` applicators into their branches."""
    resolved = view.resolved()
    node = resolved.node
    if depth >= _MAX_EXPANSION or not isinstance(node, dict):
        return [resolved]
    branches = node.get("anyOf", node.get("oneOf"))
    if (
        not isinstance(branches, list)
        or not branches
        or any(key in node for key in ("properties", "items", "allOf", "not", "if"))
    ):
        return [resolved]
    result: list[SchemaView] = []
    for branch in branches:
        result.extend(
            _expand(SchemaView(branch, resolved.root, resolved.path, resolved.resources), depth + 1)
        )
    return result


def _alternatives(static: Static) -> list[Alternative]:
    result: list[Alternative] = []
    for item in static.alternatives:
        if isinstance(item, SchemaView):
            result.extend(_expand(item))
        else:
            result.append(item)
    return result


def child(static: Static, token: str) -> Static:
    """Select one pointer token in every alternative; impossible branches become absent."""
    collected: list[Alternative] = []
    absent = static.absent
    missing = static.missing
    for item in _alternatives(static):
        if isinstance(item, SchemaView):
            selected = item.child(token)
            if selected is None:
                absent = True
            else:
                collected.append(selected)
        elif isinstance(item, Const):
            value = item.value
            if isinstance(value, Mapping) and token in value:
                collected.append(Const(value[token]))
            elif (
                isinstance(value, list)
                and token.isascii()
                and token.isdigit()
                and (len(token) == 1 or token[0] != "0")
                and int(token) < len(value)
            ):
                collected.append(Const(value[int(token)]))
            else:
                absent = missing = True
        elif isinstance(item, Obj):
            if token in item.fields:
                selected_static = item.fields[token]
                collected.extend(selected_static.alternatives)
                absent = absent or selected_static.absent
                missing = missing or selected_static.missing
            else:
                absent = missing = True
        elif token.isascii() and token.isdigit() and (len(token) == 1 or token[0] != "0"):
            collected.extend(item.items.alternatives)
            absent = True
            # A flow that ran has its first attempt; later attempts may not have run.
            missing = missing or token != "0" or item.items.missing
        else:
            absent = missing = True
    return Static(tuple(collected), absent, missing)


def pointer(static: Static, tokens: Iterable[str]) -> Static:
    current = static
    for token in tokens:
        current = child(current, token)
    return current


def types(static: Static) -> set[str] | None:
    """JSON types of present values; None when any alternative is unknown."""
    result: set[str] = set()
    for item in _alternatives(static):
        if isinstance(item, SchemaView):
            declared = schema_types(item)
            if declared is None:
                return None
            result.update(declared)
        elif isinstance(item, Const):
            result.add(json_type(item.value))
        elif isinstance(item, Obj):
            result.add("object")
        else:
            result.add("array")
    return result


def _canonical(value: object) -> object:
    """Numbers compare by value at run time (``1`` equals ``1.0``); booleans stay distinct."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {key: _canonical(item) for key, item in cast(Mapping[str, object], value).items()}
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in cast(list[object], value)]
    return value


def _key(value: object) -> str:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def values(static: Static) -> dict[str, object] | None:
    """Every allowed present value keyed by canonical JSON; None when not enumerable."""
    result: dict[str, object] = {}
    for item in _alternatives(static):
        if isinstance(item, Const):
            result[_key(item.value)] = item.value
            continue
        if not isinstance(item, SchemaView):
            return None
        node = item.resolved().node
        if not isinstance(node, dict):
            return None
        if "const" in node:
            result[_key(node["const"])] = node["const"]
        elif isinstance(node.get("enum"), list):
            for value in cast(list[object], node["enum"]):
                result[_key(value)] = value
        elif node.get("type") == "boolean":
            result.update({"true": True, "false": False})
        elif node.get("type") == "null":
            result["null"] = None
        else:
            return None
    return result


def allows_empty_string(static: Static) -> bool:
    """True only when a known string schema admits ``""`` (no enum/const/minLength)."""
    for item in _alternatives(static):
        if isinstance(item, Const):
            if item.value == "":
                return True
            continue
        if not isinstance(item, SchemaView):
            continue
        node = item.resolved().node
        if not isinstance(node, dict):
            continue
        declared = schema_types(item)
        if declared is None or "string" not in declared:
            continue
        if "const" in node or "enum" in node or "pattern" in node or "format" in node:
            continue
        minimum = node.get("minLength")
        if not isinstance(minimum, int) or minimum < 1:
            return True
    return False


def json_key(value: object) -> str:
    """Canonical JSON key used to compare authored values with allowed values."""
    return _key(value)
