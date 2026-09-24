"""Resolve explicit bindings without coercion, mutation or implicit null values."""

import re
from collections.abc import Mapping
from types import MappingProxyType

from .errors import ErrorCode, ServiceError
from .json import MAX_JSON_DEPTH, FrozenJson, FrozenObject, freeze_json
from .plan import BindingPlan

_INVALID_ESCAPE = re.compile(r"~(?:[^01]|$)")
_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z", flags=re.ASCII)


def lookup_pointer(pointer: str, context: FrozenJson) -> tuple[bool, FrozenJson]:
    """Return ``(found, value)`` for an RFC 6901 pointer; null is found.

    Invalid pointer syntax is a configuration failure, never "not found".
    """
    if (pointer and not pointer.startswith("/")) or _INVALID_ESCAPE.search(pointer):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    if not pointer:
        return True, context
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
            return False, None
    return True, value


def resolve_binding(binding: BindingPlan, context: FrozenJson) -> FrozenJson:
    """Resolve a literal, pointer, ``first_of`` or object ``fields`` binding.

    For a pointer, null is present. ``first_of`` selects the first member that
    resolves to a non-null value. A missing value uses the authored default;
    without one the binding fails with ``MISSING_BINDING``. Invalid pointer
    syntax is configuration failure, never a reason to use a default.
    """
    if binding.kind == "literal":
        return freeze_json(binding.literal)
    if binding.kind == "fields":
        if not binding.fields or binding.has_default:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        return resolve_bindings(binding.fields, context)
    if binding.kind == "pointer":
        if binding.pointer is None or binding.members:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        found, value = lookup_pointer(binding.pointer, context)
        if found:
            return value
    elif binding.kind == "first_of":
        if binding.pointer is not None or not binding.members:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        # Validate every member before selecting, so syntax errors never hide.
        candidates = [lookup_pointer(member, context) for member in binding.members]
        for found, value in candidates:
            if found and value is not None:
                return value
    else:
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    if binding.has_default:
        return freeze_json(binding.default)
    raise ServiceError(ErrorCode.MISSING_BINDING)


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


def resolve_source(binding: BindingPlan, context: FrozenJson) -> tuple[bool, FrozenJson]:
    """Resolve a condition source to ``(present, value)``; null counts as absent."""
    if binding.kind == "literal":
        value = freeze_json(binding.literal)
        return value is not None, value
    if binding.has_default or binding.kind not in {"pointer", "first_of"}:
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    pointers = (binding.pointer,) if binding.kind == "pointer" else binding.members
    if any(pointer is None for pointer in pointers) or not pointers:
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    candidates = [lookup_pointer(pointer, context) for pointer in pointers if pointer is not None]
    for found, value in candidates:
        if found and value is not None:
            return True, value
    return False, None


__all__ = ["lookup_pointer", "resolve_binding", "resolve_bindings", "resolve_source"]
