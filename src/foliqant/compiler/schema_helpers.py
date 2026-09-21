"""Shared, offline checks for JSON Schemas embedded in tool catalogs."""

from collections.abc import Mapping
from typing import cast
from urllib.parse import unquote

from jsonschema import Draft202012Validator

_MAX_SCHEMA_DEPTH = 64
_MAX_SCHEMA_NODES = 10_000

_SCHEMA_MAP_KEYWORDS = {
    "$defs",
    "definitions",
    "properties",
    "patternProperties",
    "dependentSchemas",
}
_SCHEMA_SINGLE_KEYWORDS = {
    "additionalProperties",
    "unevaluatedProperties",
    "propertyNames",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "additionalItems",
    "unevaluatedItems",
    "contentSchema",
}
_SCHEMA_ARRAY_KEYWORDS = {"allOf", "anyOf", "oneOf", "prefixItems"}


def walk_schema_nodes(root: dict[str, object]) -> list[dict[str, object]]:
    """Traverse schema positions without treating annotation JSON as schemas."""

    found: list[dict[str, object]] = []
    pending: list[tuple[dict[str, object], int]] = [(root, 0)]
    while pending:
        schema, depth = pending.pop()
        if depth > _MAX_SCHEMA_DEPTH or len(found) >= _MAX_SCHEMA_NODES:
            raise ValueError("schema structure exceeds bounds")
        found.append(schema)
        children: list[dict[str, object]] = []
        for keyword in _SCHEMA_MAP_KEYWORDS:
            value = schema.get(keyword)
            if isinstance(value, dict):
                children.extend(
                    cast(dict[str, object], child)
                    for child in cast(dict[object, object], value).values()
                    if isinstance(child, dict)
                )
        dependencies = schema.get("dependencies")
        if isinstance(dependencies, dict):
            children.extend(
                cast(dict[str, object], child)
                for child in cast(dict[object, object], dependencies).values()
                if isinstance(child, dict)
            )
        for keyword in _SCHEMA_SINGLE_KEYWORDS:
            child = schema.get(keyword)
            if isinstance(child, dict):
                children.append(cast(dict[str, object], child))
        items = schema.get("items")
        if isinstance(items, dict):
            children.append(cast(dict[str, object], items))
        elif isinstance(items, list):
            children.extend(
                cast(dict[str, object], child)
                for child in cast(list[object], items)
                if isinstance(child, dict)
            )
        for keyword in _SCHEMA_ARRAY_KEYWORDS:
            value = schema.get(keyword)
            if isinstance(value, list):
                children.extend(
                    cast(dict[str, object], child)
                    for child in cast(list[object], value)
                    if isinstance(child, dict)
                )
        pending.extend((child, depth + 1) for child in children)
    return found


def resolve_schema_fragment(resource: dict[str, object], fragment: str) -> object:
    """Resolve one local JSON Pointer or anchor fragment without external retrieval."""

    decoded = unquote(fragment)
    if not decoded:
        return resource
    if decoded.startswith("/"):
        current: object = resource
        for raw_token in decoded[1:].split("/"):
            if "~" in raw_token.replace("~0", "").replace("~1", ""):
                raise ValueError("invalid JSON Pointer escape")
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict) and token in current:
                current = cast(dict[str, object], current)[token]
            elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
                current = cast(list[object], current)[int(token)]
            else:
                raise ValueError("unresolved schema fragment")
        return current
    for node in walk_schema_nodes(resource):
        if node.get("$anchor") == decoded or node.get("$dynamicAnchor") == decoded:
            return node
    raise ValueError("unresolved schema anchor")


def validate_confined_tool_schema(schema: Mapping[str, object]) -> Draft202012Validator:
    """Build a validator for a schema whose references are confined to itself."""

    schema_object = dict(schema)
    Draft202012Validator.check_schema(schema_object)
    pending = [schema_object]
    validated: set[int] = set()
    while pending:
        start = pending.pop()
        for node in walk_schema_nodes(start):
            if id(node) in validated:
                continue
            validated.add(id(node))
            if "$id" in node:
                raise ValueError("tool schemas cannot define identifiers")
            for keyword in ("$ref", "$dynamicRef"):
                reference = node.get(keyword)
                if not isinstance(reference, str):
                    continue
                if not reference.startswith("#"):
                    raise ValueError("tool schema reference is not internal")
                target = resolve_schema_fragment(schema_object, reference[1:])
                if isinstance(target, bool):
                    continue
                if not isinstance(target, dict):
                    raise ValueError("tool schema reference does not resolve to a schema")
                pending.append(cast(dict[str, object], target))
    return Draft202012Validator(schema_object)
