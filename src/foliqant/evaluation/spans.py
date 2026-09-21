"""Deterministic source-span gold validation and comparison."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from foliqant.core.bindings import resolve_binding
from foliqant.core.errors import ServiceError
from foliqant.core.json import MAX_JSON_DEPTH, FrozenJson
from foliqant.core.plan import BindingPlan

_INVALID_ESCAPE = re.compile(r"~(?:[^01]|$)")
_FIELDS = {"input_path", "required", "allowed"}


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Validated code-point ranges over one string in the case input."""

    input_path: str
    required: tuple[int, int]
    allowed: tuple[int, int]


def _range(value: FrozenJson, name: str) -> tuple[int, int]:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or any(type(index) is not int for index in value)
    ):
        raise ValueError(f"source_span {name} must be two integer code-point offsets")
    start, end = value
    assert isinstance(start, int) and isinstance(end, int)
    if start < 0 or end <= start:
        raise ValueError(f"source_span {name} must be a nonempty forward range")
    return start, end


def source_span(expected: FrozenJson) -> SourceSpan:
    """Validate the closed source-span gold shape without resolving its input."""
    if not isinstance(expected, Mapping) or set(expected) != _FIELDS:
        raise ValueError("source_span gold requires only input_path, required, and allowed")
    input_path = expected["input_path"]
    if (
        type(input_path) is not str
        or (input_path and not input_path.startswith("/"))
        or _INVALID_ESCAPE.search(input_path)
        or len(input_path.split("/")) > MAX_JSON_DEPTH + 1
    ):
        raise ValueError("source_span input_path must be an RFC 6901 JSON pointer")
    required = _range(expected["required"], "required")
    allowed = _range(expected["allowed"], "allowed")
    if not allowed[0] <= required[0] < required[1] <= allowed[1]:
        raise ValueError("source_span required range must be nested in allowed range")
    return SourceSpan(input_path, required, allowed)


def source_span_source(expected: FrozenJson, case_input: FrozenJson) -> tuple[SourceSpan, str]:
    """Resolve and validate the authored source against the immutable case input."""
    specification = source_span(expected)
    try:
        value = resolve_binding(
            BindingPlan(kind="pointer", pointer=specification.input_path), case_input
        )
    except ServiceError:
        raise ValueError("source_span input_path must resolve in the case input") from None
    if type(value) is not str:
        raise ValueError("source_span input_path must resolve to a string")
    if specification.allowed[1] > len(value):
        raise ValueError("source_span ranges exceed the source string")
    return specification, value


def matches_source_span(actual: FrozenJson, expected: FrozenJson, case_input: FrozenJson) -> bool:
    """Match one exact candidate occurrence within the independently authored ranges."""
    specification, source = source_span_source(expected, case_input)
    if type(actual) is not str or not actual:
        return False
    allowed_start, allowed_end = specification.allowed
    required_start, required_end = specification.required
    occurrence = source.find(actual, allowed_start, allowed_end)
    while occurrence != -1:
        end = occurrence + len(actual)
        if occurrence <= required_start and end >= required_end:
            return True
        occurrence = source.find(actual, occurrence + 1, allowed_end)
    return False
