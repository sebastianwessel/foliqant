"""Pure evaluation of compiled conditions over a binding context.

Evaluation never raises for data: an absent value (a pointer that does not
resolve, or resolves to JSON null) and a value of an incompatible type have a
defined result for every operator. Only a malformed plan, which compilation
rejects, raises ``ServiceError(INVALID_CONFIGURATION)``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from .bindings import resolve_source
from .errors import ErrorCode, ServiceError
from .json import FrozenJson
from .plan import (
    AllConditionPlan,
    AnyConditionPlan,
    BindingPlan,
    ConditionPlan,
    LeafConditionPlan,
    NotConditionPlan,
)

MAX_DESCRIPTION_LENGTH = 512
MAX_MATCH_LENGTH = 1024
"""Longest string ``matches`` compares; a longer value evaluates to false."""
_COMPARISONS = frozenset({"gt", "gte", "lt", "lte"})

type MismatchReason = Literal["incompatible_type", "value_too_long"]


@dataclass(frozen=True, slots=True)
class ConditionMismatch:
    """A leaf that evaluated to false because the runtime value could not be compared."""

    leaf: LeafConditionPlan
    reason: MismatchReason


@dataclass(slots=True)
class ConditionTrace:
    """Invocation-local record of leaves whose runtime value could not be compared."""

    mismatches: list[ConditionMismatch] = field(default_factory=list)


def json_equal(left: FrozenJson, right: FrozenJson) -> bool:
    """Deep JSON equality without coercion: ``"1" != 1`` and ``true != 1``.

    Numbers compare by value, so ``1`` equals ``1.0`` as in JSON Schema.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, int | float) and isinstance(right, int | float):
        return left == right
    if isinstance(left, str) or isinstance(right, str):
        return type(left) is type(right) and left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(
            json_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return False


def _number(value: FrozenJson) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _compare(operator: str, value: float, operand: float) -> bool:
    if operator == "gt":
        return value > operand
    if operator == "gte":
        return value >= operand
    if operator == "lt":
        return value < operand
    if operator == "lte":
        return value <= operand
    if operator == "equals":
        return value == operand
    raise ServiceError(ErrorCode.INVALID_CONFIGURATION)


def _leaf(condition: LeafConditionPlan, context: FrozenJson, trace: ConditionTrace | None) -> bool:
    present, value = resolve_source(condition.source, context)
    operator, operand = condition.operator, condition.operand

    def mismatch(reason: MismatchReason = "incompatible_type") -> bool:
        if trace is not None:
            trace.mismatches.append(ConditionMismatch(condition, reason))
        return False

    if operator == "present":
        if type(operand) is not bool:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        return present is operand
    if operator == "empty":
        if type(operand) is not bool:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        is_empty = not present or value == "" or (isinstance(value, tuple | Mapping) and not value)
        return is_empty is operand
    if operator in {"equals", "not_equals"}:
        # Absent equals null, so `equals: null` holds for missing values as well.
        equal = json_equal(value if present else None, operand)
        return equal if operator == "equals" else not equal
    if operator in {"in", "not_in"}:
        if not isinstance(operand, tuple) or not operand:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        # Absent equals null here as well, so `in: [null, ...]` holds for missing values.
        member = any(json_equal(value if present else None, item) for item in operand)
        return member if operator == "in" else not member
    if operator in _COMPARISONS:
        if not _number(operand):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if not present:
            return False
        if not _number(value):
            return mismatch()
        assert isinstance(value, int | float) and isinstance(operand, int | float)
        return _compare(operator, value, operand)
    if operator == "matches":
        if condition.pattern is None or type(operand) is not str:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if not present:
            return False
        if not isinstance(value, str):
            return mismatch()
        # Bounded input plus compile-time pattern checks keep matching bounded in time.
        if len(value) > MAX_MATCH_LENGTH:
            return mismatch("value_too_long")
        return condition.pattern.fullmatch(value) is not None
    if operator == "length":
        if condition.comparison is None or type(operand) is not int:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if not present:
            return False
        if not isinstance(value, str | tuple | Mapping):
            return mismatch()
        return _compare(condition.comparison, len(value), operand)
    raise ServiceError(ErrorCode.INVALID_CONFIGURATION)


def evaluate_condition(
    condition: ConditionPlan, context: FrozenJson, *, trace: ConditionTrace | None = None
) -> bool:
    """Evaluate a compiled condition; ``all``/``any`` short-circuit in authored order."""
    if isinstance(condition, LeafConditionPlan):
        return _leaf(condition, context, trace)
    if isinstance(condition, AllConditionPlan):
        if not condition.operands:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        return all(evaluate_condition(item, context, trace=trace) for item in condition.operands)
    if isinstance(condition, AnyConditionPlan):
        if not condition.operands:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        return any(evaluate_condition(item, context, trace=trace) for item in condition.operands)
    if isinstance(condition, NotConditionPlan):
        return not evaluate_condition(condition.operand, context, trace=trace)
    raise ServiceError(ErrorCode.INVALID_CONFIGURATION)


def _source_text(source: BindingPlan) -> str:
    if source.kind == "literal":
        return "literal"
    if source.kind == "first_of":
        return "first_of(" + ", ".join(source.members) + ")"
    return source.pointer or '""'


def describe_condition(condition: ConditionPlan) -> str:
    """Condense a condition to pointers and operators, never compared operand values.

    The boolean of ``present`` / ``empty`` is part of the operator, so it is kept
    (``present=false``). The text is bounded, deterministic configuration and safe
    as a telemetry label.
    """

    def visit(item: ConditionPlan) -> str:
        if isinstance(item, LeafConditionPlan):
            if item.operator == "length":
                operator = f"length {item.comparison}"
            elif item.operator in {"present", "empty"}:
                operator = f"{item.operator}={'true' if item.operand is True else 'false'}"
            else:
                operator = item.operator
            return f"{_source_text(item.source)} {operator}"
        if isinstance(item, AllConditionPlan):
            return "all(" + "; ".join(visit(child) for child in item.operands) + ")"
        if isinstance(item, AnyConditionPlan):
            return "any(" + "; ".join(visit(child) for child in item.operands) + ")"
        return "not(" + visit(item.operand) + ")"

    text = visit(condition)
    if len(text) > MAX_DESCRIPTION_LENGTH:
        return text[: MAX_DESCRIPTION_LENGTH - 3] + "..."
    return text


def condition_leaves(condition: ConditionPlan) -> tuple[LeafConditionPlan, ...]:
    """Return every leaf in authored order."""
    if isinstance(condition, LeafConditionPlan):
        return (condition,)
    if isinstance(condition, NotConditionPlan):
        return condition_leaves(condition.operand)
    return tuple(leaf for item in condition.operands for leaf in condition_leaves(item))


__all__ = [
    "MAX_MATCH_LENGTH",
    "ConditionMismatch",
    "ConditionTrace",
    "MismatchReason",
    "condition_leaves",
    "describe_condition",
    "evaluate_condition",
    "json_equal",
]
