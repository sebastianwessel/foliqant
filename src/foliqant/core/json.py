"""Lossless immutable JSON values at the engine boundary."""

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from .errors import ErrorCode, ServiceError

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type FrozenJson = None | bool | int | float | str | tuple[FrozenJson, ...] | FrozenObject
type FrozenObject = Mapping[str, FrozenJson]

# Bound recursive input processing independently of each transport's byte limit.
MAX_JSON_DEPTH = 64


def freeze_json(value: object, *, max_depth: int = MAX_JSON_DEPTH) -> FrozenJson:
    """Validate and copy JSON into immutable values without coercion or aliasing."""

    def visit(item: object, depth: int) -> FrozenJson:
        if depth > max_depth:
            raise ServiceError(ErrorCode.INVALID_INPUT)
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ServiceError(ErrorCode.INVALID_INPUT)
            return item
        if isinstance(item, (list, tuple)):
            return tuple(
                visit(child, depth + 1) for child in cast(list[object] | tuple[object, ...], item)
            )
        if isinstance(item, Mapping):
            result: dict[str, FrozenJson] = {}
            for key, child in cast(Mapping[object, object], item).items():
                if not isinstance(key, str):
                    raise ServiceError(ErrorCode.INVALID_INPUT)
                result[key] = visit(child, depth + 1)
            return MappingProxyType(result)
        raise ServiceError(ErrorCode.INVALID_INPUT)

    return visit(value, 0)


def thaw_json(value: FrozenJson) -> JsonValue:
    """Return a new mutable JSON representation for an I/O boundary."""
    if isinstance(value, Mapping):
        return {key: thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(child) for child in value]
    return value
