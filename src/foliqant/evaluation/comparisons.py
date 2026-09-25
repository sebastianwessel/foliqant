"""Built-in comparisons and array projections shared by checks and metrics."""

import json
from typing import TYPE_CHECKING, cast

from foliqant.core.bindings import resolve_binding
from foliqant.core.errors import ServiceError
from foliqant.core.json import FrozenJson, thaw_json
from foliqant.core.plan import BindingPlan

from .spans import matches_source_span

if TYPE_CHECKING:
    from .contracts import Expectation

#: Owner statuses whose absent nested value may count as JSON null (``absent_as_null``).
EXECUTED_STATUSES = frozenset({"completed", "needs_review"})


def canonical(value: FrozenJson) -> str:
    """Type-sensitive canonical JSON text of a frozen value."""
    return json.dumps(thaw_json(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalized_text(value: str) -> str:
    """Unicode case folding with every whitespace run collapsed to one space, trimmed."""
    return " ".join(value.casefold().split())


class ProjectionError(ValueError):
    """The observed value is not an array whose items all contain the projected member."""


def project(actual: FrozenJson, each: str) -> FrozenJson:
    """Return the value at relative pointer ``each`` of every item of an array."""
    if not isinstance(actual, tuple):
        raise ProjectionError("projection requires an array")
    binding = BindingPlan(kind="pointer", pointer=each)
    try:
        return tuple(resolve_binding(binding, item) for item in actual)
    except ServiceError:
        raise ProjectionError("projected member is missing") from None


def matches(expectation: "Expectation", actual: FrozenJson, case_input: FrozenJson) -> bool:
    """Compare an observed value with gold for every comparison except ``custom``."""
    expected = expectation.expected
    if expectation.comparison == "source_span":
        return matches_source_span(actual, expected, case_input)
    if expectation.comparison == "set":
        return isinstance(actual, tuple) and {canonical(item) for item in actual} == {
            canonical(item) for item in cast(tuple[FrozenJson, ...], expected)
        }
    if expectation.comparison == "one_of":
        return canonical(actual) in {
            canonical(item) for item in cast(tuple[FrozenJson, ...], expected)
        }
    if expectation.comparison == "text":
        return isinstance(actual, str) and normalized_text(actual) == normalized_text(
            cast(str, expected)
        )
    if expectation.comparison == "contains":
        return isinstance(actual, str) and normalized_text(cast(str, expected)) in normalized_text(
            actual
        )
    if expectation.comparison == "custom":
        raise ValueError("custom comparisons require their registered scorer")
    return canonical(actual) == canonical(expected)
