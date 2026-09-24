"""Compile authored conditions and check them against static value sets."""

import re
from collections.abc import Callable
from typing import cast

from foliqant.contracts.conditions import (
    AllCondition,
    AnyCondition,
    ConditionFirstOf,
    LeafCondition,
    NotCondition,
)
from foliqant.core.conditions import condition_leaves
from foliqant.core.errors import ServiceError
from foliqant.core.json import freeze_json, thaw_json
from foliqant.core.plan import (
    AllConditionPlan,
    AnyConditionPlan,
    BindingPlan,
    ConditionOperator,
    ConditionPlan,
    LeafConditionPlan,
    NotConditionPlan,
    SourceLocation,
)

from .diagnostics import Diagnostics, safe_text
from .errors import CompilationError
from .patterns import UnsafePatternError, check_pattern
from .static_schema import incompatible_types, json_type
from .static_values import Static, json_key, types, values

type AuthoredCondition = LeafCondition | AllCondition | AnyCondition | NotCondition


def compile_condition(authored: AuthoredCondition, location: SourceLocation) -> ConditionPlan:
    """Freeze one validated authored condition into its standard-library plan.

    A ``matches`` pattern whose backtracking is not bounded fails with
    ``unsafe_pattern``, a specific invalid condition.
    """
    try:
        return _compile(authored)
    except ServiceError:
        raise CompilationError("invalid_condition", location) from None
    except UnsafePatternError:
        raise CompilationError("unsafe_pattern", location, field="matches") from None


def _compile(authored: AuthoredCondition) -> ConditionPlan:
    if isinstance(authored, AllCondition):
        return AllConditionPlan(tuple(_compile(item) for item in authored.all))
    if isinstance(authored, AnyCondition):
        return AnyConditionPlan(tuple(_compile(item) for item in authored.any))
    if isinstance(authored, NotCondition):
        return NotConditionPlan(_compile(authored.not_))
    if "literal" in authored.model_fields_set:
        source = BindingPlan(kind="literal", literal=freeze_json(authored.literal))
    elif isinstance(authored.binding, ConditionFirstOf):
        source = BindingPlan(
            kind="first_of", members=tuple(item.pointer for item in authored.binding.first_of)
        )
    else:
        assert authored.binding is not None
        source = BindingPlan(kind="pointer", pointer=authored.binding.pointer)
    operator = cast(ConditionOperator, authored.operator)
    if operator == "length":
        assert authored.length is not None
        comparison, count = authored.length.comparison
        return LeafConditionPlan(source, operator, count, comparison=comparison)
    operand = freeze_json(authored.operand)
    if operator == "matches":
        assert isinstance(authored.matches, str)
        check_pattern(authored.matches)
        return LeafConditionPlan(source, operator, operand, pattern=re.compile(authored.matches))
    return LeafConditionPlan(source, operator, operand)


def condition_pointers(condition: ConditionPlan) -> tuple[str, ...]:
    """Every pointer a condition reads, in authored order."""
    result: list[str] = []
    for leaf in condition_leaves(condition):
        if leaf.source.kind == "pointer" and leaf.source.pointer is not None:
            result.append(leaf.source.pointer)
        result.extend(leaf.source.members)
    return tuple(result)


def _source(leaf: LeafConditionPlan, resolve: Callable[[str], Static]) -> Static:
    from .static_values import Const, of, union

    if leaf.source.kind == "literal":
        return of(Const(thaw_json(leaf.source.literal)))
    if leaf.source.kind == "first_of":
        return union([resolve(member) for member in leaf.source.members], absent=True)
    return resolve(leaf.source.pointer or "")


def _verdict(leaf: LeafConditionPlan, static: Static) -> tuple[bool | None, bool]:
    """Static truth for equality/membership against enumerable values.

    Returns ``(verdict, strict)``. A strict verdict holds for present and absent
    values alike; a non-strict one holds for every present value only.
    """
    allowed = values(static)
    if (
        allowed is None
        or not allowed
        or leaf.operator
        not in {
            "equals",
            "not_equals",
            "in",
            "not_in",
        }
    ):
        return None, False
    raw = thaw_json(leaf.operand)
    operands = raw if leaf.operator in {"in", "not_in"} else [raw]
    assert isinstance(operands, list)
    if any(item is None for item in operands):
        return None, False
    keys = {json_key(item) for item in operands}
    positive = leaf.operator in {"equals", "in"}
    if not keys & set(allowed):
        # No present value matches, and absence never equals a non-null operand.
        return not positive, True
    if set(allowed) <= keys:
        return positive, False
    return None, False


def _combine(condition: ConditionPlan, verdicts: dict[int, bool | None]) -> bool | None:
    if isinstance(condition, LeafConditionPlan):
        return verdicts.get(id(condition))
    if isinstance(condition, NotConditionPlan):
        inner = _combine(condition.operand, verdicts)
        return None if inner is None else not inner
    results = [_combine(item, verdicts) for item in condition.operands]
    if isinstance(condition, AllConditionPlan):
        if any(result is False for result in results):
            return False
        return True if all(result is True for result in results) else None
    if any(result is True for result in results):
        return True
    return False if all(result is False for result in results) else None


def check_condition(
    condition: ConditionPlan,
    *,
    resolve: Callable[[str], Static],
    location: SourceLocation,
    field: str,
    diagnostics: Diagnostics,
) -> bool | None:
    """Reject impossible pointers and type mismatches; warn about constant leaves.

    Returns the static truth of the whole condition, or None when it depends on data.
    """
    verdicts: dict[int, bool | None] = {}
    for leaf in condition_leaves(condition):
        static = _source(leaf, resolve)
        if leaf.source.kind != "literal" and static.impossible:
            raise CompilationError("dangling_pointer", location, field=field)
        kinds = types(static)
        operand = thaw_json(leaf.operand)
        expected: set[str] | None = None
        if leaf.operator in {"gt", "gte", "lt", "lte"}:
            expected = {"number"}
        elif leaf.operator == "matches":
            expected = {"string"}
        elif leaf.operator == "length":
            expected = {"string", "array", "object"}
        if expected is not None and incompatible_types(kinds, expected):
            raise CompilationError("condition_type_mismatch", location, field=field)
        if leaf.operator in {"equals", "not_equals", "in", "not_in"}:
            items = operand if isinstance(operand, list) else [operand]
            for item in items:
                if item is not None and incompatible_types(kinds, {json_type(item)}):
                    raise CompilationError("condition_type_mismatch", location, field=field)
        verdict, strict = _verdict(leaf, static)
        verdicts[id(leaf)] = verdict if strict else None
        pointer = leaf.source.pointer or ", ".join(leaf.source.members) or "literal"
        text = f"`{safe_text(pointer, 160)} {leaf.operator}`"
        if verdict is not None and strict:
            diagnostics.add(
                "condition_always_true" if verdict else "condition_always_false",
                location,
                f"{text} is {'true' if verdict else 'false'} for every statically allowed "
                "value and for an absent value.",
                field_path=field,
            )
        elif verdict is not None:
            diagnostics.add(
                "condition_always_true" if verdict else "condition_always_false",
                location,
                f"{text} is {'true' if verdict else 'false'} for every statically allowed "
                "value; only an absent value changes the result. Use `present` to test "
                "presence.",
                field_path=field,
            )
    return _combine(condition, verdicts)


__all__ = ["check_condition", "compile_condition", "condition_pointers"]
