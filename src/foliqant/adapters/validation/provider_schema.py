"""Expand frozen schemas for provider structured output without external retrieval."""

from collections.abc import Mapping
from copy import deepcopy
from typing import cast
from urllib.parse import urldefrag, urljoin

from referencing.jsonschema import DRAFT202012, Schema, SchemaRegistry

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import JsonValue

_MAX_EXPANDED_DEPTH = 64
_MAX_EXPANDED_NODES = 10_000
_DEFINITION_KEYWORDS = frozenset({"$defs", "definitions"})


def inline_provider_schema(
    root: Schema, uri: str, registry: SchemaRegistry
) -> dict[str, JsonValue]:
    """Return a bounded, fully inlined schema for an SDK structured-output boundary.

    The registry is already closed over the compiled workflow resources. Only
    draft-recognized schema positions are expanded, so keyword-looking mappings
    in annotations remain literal data. Dynamic and recursive references cannot
    be represented safely by the selected SDK and fail before provider I/O.
    """
    count = 0

    def rewrite(
        schema: Schema,
        base: str,
        active: frozenset[tuple[int, str]],
        depth: int,
    ) -> JsonValue:
        nonlocal count
        count += 1
        if count > _MAX_EXPANDED_NODES or depth > _MAX_EXPANDED_DEPTH:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if isinstance(schema, bool):
            return schema
        if any(key in schema for key in ("$id", "$dynamicRef", "$dynamicAnchor")):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)

        reference = schema.get("$ref")
        if isinstance(reference, str):
            resolved = registry.resolver(base).lookup(reference)
            target = resolved.contents
            if not isinstance(target, (Mapping, bool)):
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            target_base, _fragment = urldefrag(urljoin(base, reference))
            identity = (id(target), target_base)
            if identity in active:
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            expanded_target = rewrite(
                target,
                target_base,
                active | {identity},
                depth + 1,
            )
            siblings = {
                key: value
                for key, value in schema.items()
                if key not in {"$ref", "$anchor", *_DEFINITION_KEYWORDS}
            }
            if not siblings:
                return expanded_target
            expanded_siblings = rewrite(siblings, base, active, depth + 1)
            return {"allOf": [expanded_target, expanded_siblings]}

        children = {id(child) for child in DRAFT202012.subresources_of(schema)}

        def clone(value: object) -> JsonValue:
            if id(value) in children and isinstance(value, (Mapping, bool)):
                return rewrite(cast(Schema, value), base, active, depth + 1)
            if isinstance(value, Mapping):
                return {str(key): clone(item) for key, item in value.items()}
            if isinstance(value, list):
                return [clone(item) for item in value]
            return cast(JsonValue, deepcopy(value))

        return {
            key: clone(value)
            for key, value in schema.items()
            if key not in {"$anchor", *_DEFINITION_KEYWORDS}
        }

    expanded = rewrite(root, uri, frozenset(), 0)
    if isinstance(expanded, bool):
        return {"allOf": [expanded]}
    if not isinstance(expanded, dict):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    return expanded
