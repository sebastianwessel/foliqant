"""Conservative static JSON Schema checks, never a general subschema proof."""

import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from .schema_helpers import resolve_schema_fragment


@dataclass(frozen=True)
class SchemaView:
    """A schema node with its local reference context."""

    node: object
    root: dict[str, object]
    path: str
    resources: Mapping[str, dict[str, object]]

    def resolved(self) -> "SchemaView":
        current = self
        seen: set[tuple[str, int]] = set()
        while isinstance(current.node, dict):
            identity = (current.path, id(current.node))
            if identity in seen:
                return current.unknown()
            seen.add(identity)
            reference = current.node.get("$ref")
            if not isinstance(reference, str):
                return current
            relative, _, fragment = reference.partition("#")
            path = str(PurePosixPath(current.path).parent / relative) if relative else current.path
            # File resources have already been confined and normalized by compilation.
            path = posixpath.normpath(path)
            root = current.resources.get(path) if relative else current.root
            if root is None:
                return current.unknown()
            try:
                target = resolve_schema_fragment(root, fragment)
            except ValueError:
                return current.unknown()
            current = SchemaView(target, root, path, current.resources)
        return current

    def unknown(self) -> "SchemaView":
        return SchemaView({}, self.root, self.path, self.resources)

    def child(self, token: str) -> "SchemaView | None":
        """Return None only when a path is statically impossible; {} is unknown."""
        view = self.resolved()
        node = view.node
        if node is False:
            return None
        if not isinstance(node, dict):
            return view.unknown()
        # Applicators can define fields absent from this node. Do not infer closure.
        if any(key in node for key in ("allOf", "anyOf", "oneOf", "if", "then", "else", "not")):
            return view.unknown()
        types = schema_types(view)
        if types is not None and not types.intersection({"object", "array"}):
            return None
        array_index = token.isascii() and token.isdigit() and (len(token) == 1 or token[0] != "0")
        if array_index and types != {"array"} and (types is None or "array" in types):
            # Object-only keywords do not constrain arrays. A numeric pointer may
            # select an array item even when an untyped schema closes properties.
            return view.unknown()
        if types == {"array"}:
            if not token.isascii() or not token.isdigit() or (len(token) > 1 and token[0] == "0"):
                return None
            if len(token) > 18:
                return None
            index = int(token)
            maximum = node.get("maxItems")
            if isinstance(maximum, int) and index >= maximum:
                return None
            prefix = node.get("prefixItems")
            if isinstance(prefix, list) and index < len(prefix):
                child: object = prefix[index]
            else:
                child = node.get("items", {})
        else:
            properties = node.get("properties", {})
            if isinstance(properties, dict) and token in properties:
                child = properties[token]
            elif node.get("patternProperties") or types not in (None, {"object"}):
                return view.unknown()
            else:
                child = node.get("additionalProperties", {})
        if child is False:
            return None
        return SchemaView(child, view.root, view.path, view.resources)

    def pointer(self, tokens: list[str]) -> "SchemaView | None":
        current: SchemaView | None = self
        for token in tokens:
            if current is None:
                return None
            current = current.child(token)
        return current


def json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    return "array"


def schema_types(view: SchemaView) -> set[str] | None:
    node = view.resolved().node
    if not isinstance(node, dict):
        return None
    declared = node.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return set(cast(list[str], declared))
    if "const" in node:
        return {json_type(node["const"])}
    values = node.get("enum")
    if isinstance(values, list):
        return {json_type(value) for value in values}
    return None


def incompatible_types(source: set[str] | None, target: set[str] | None) -> bool:
    if source is None or target is None:
        return False
    # Integer is a JSON Schema subtype of number. Reject only disjoint sets,
    # leaving partial/uncertain compatibility for the runtime validator.
    if "number" in target:
        target = target | {"integer"}
    if "number" in source:
        source = source | {"integer"}
    return not bool(source.intersection(target))
