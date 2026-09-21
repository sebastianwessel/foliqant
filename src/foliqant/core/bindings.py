"""Resolve explicit bindings without coercion, mutation or implicit null values."""

import re
from collections.abc import Mapping
from types import MappingProxyType

from .errors import ErrorCode, ServiceError
from .json import MAX_JSON_DEPTH, FrozenJson, FrozenObject, freeze_json
from .plan import BindingPlan

_INVALID_ESCAPE = re.compile(r"~(?:[^01]|$)")
_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z", flags=re.ASCII)


def resolve_binding(binding: BindingPlan, context: FrozenJson) -> FrozenJson:
    """Resolve an RFC 6901 pointer or copy an explicit literal/default.

    Null is present. An optional binding requires its authored default. Invalid
    pointer syntax is configuration failure, never a reason to use a default.
    """
    if binding.kind == "literal":
        return freeze_json(binding.literal)
    pointer = binding.pointer
    if (
        binding.kind != "pointer"
        or pointer is None
        or (pointer and not pointer.startswith("/"))
        or _INVALID_ESCAPE.search(pointer)
        or binding.optional != binding.has_default
    ):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    if not pointer:
        return context
    parts = pointer[1:].split("/")
    if len(parts) > MAX_JSON_DEPTH:
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    value = context
    for part in parts:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, Mapping) and key in value:
            value = value[key]
        elif (
            isinstance(value, tuple)
            and _INDEX.fullmatch(key)
            # Bound integer parsing even for an invalid enormous index.
            and len(key) <= len(str(len(value)))
            and int(key) < len(value)
        ):
            value = value[int(key)]
        else:
            if binding.optional:
                return freeze_json(binding.default)
            raise ServiceError(ErrorCode.MISSING_BINDING)
    return value


def resolve_bindings(
    bindings: tuple[tuple[str, BindingPlan], ...], context: FrozenJson
) -> FrozenObject:
    """Resolve one compiled argument set without silently overwriting names."""
    values: dict[str, FrozenJson] = {}
    for name, binding in bindings:
        if name in values:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        values[name] = resolve_binding(binding, context)
    return MappingProxyType(values)
