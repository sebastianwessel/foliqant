"""Closed condition language for routes, step guards and repeat stop rules.

A condition reads bound data through pointers and compares it with authored
operands. It never calls code, reads the environment or evaluates expressions.
"""

import re
from collections.abc import Mapping
from typing import Annotated, Literal, Self, cast

from pydantic import Discriminator, Field, StrictBool, Tag, model_validator

from foliqant.core.json import JsonValue

from .base import BoundaryModel

MAX_CONDITION_DEPTH = 8
"""Maximum nesting of combinators and leaves, counting the leaf itself."""

MAX_CONDITION_OPERANDS = 32
MAX_IN_VALUES = 64
MAX_PATTERN_LENGTH = 256

JsonPointer = Annotated[str, Field(pattern=r"^(?:/(?:[^~/]|~[01])*)*$")]
_Number = int | float
_Length = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]

CONDITION_OPERATORS = (
    "present",
    "empty",
    "equals",
    "not_equals",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "matches",
    "length",
)
_OPERATOR_FIELDS = {
    "present": "present",
    "empty": "empty",
    "equals": "equals",
    "not_equals": "not_equals",
    "in_": "in",
    "not_in": "not_in",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    "matches": "matches",
    "length": "length",
}


class ConditionPointer(BoundaryModel):
    """A pointer source; absence is part of every operator's semantics."""

    pointer: JsonPointer


class ConditionFirstOf(BoundaryModel):
    """The first present member (resolves and is not null), otherwise absent."""

    first_of: Annotated[list[ConditionPointer], Field(min_length=1, max_length=16)]


def _source_kind(value: object) -> str | None:
    if isinstance(value, Mapping):
        if "pointer" in value and "first_of" not in value:
            return "pointer"
        if "first_of" in value and "pointer" not in value:
            return "first_of"
    if isinstance(value, ConditionPointer):
        return "pointer"
    if isinstance(value, ConditionFirstOf):
        return "first_of"
    return None


ConditionSource = Annotated[
    Annotated[ConditionPointer, Tag("pointer")] | Annotated[ConditionFirstOf, Tag("first_of")],
    Discriminator(_source_kind),
]


class LengthComparison(BoundaryModel):
    """Exactly one integer comparison applied to a string, array or object length."""

    gt: _Length | None = None
    gte: _Length | None = None
    lt: _Length | None = None
    lte: _Length | None = None
    equals: _Length | None = None

    @model_validator(mode="after")
    def exactly_one_comparison(self) -> Self:
        selected = [name for name in self.model_fields_set if getattr(self, name) is not None]
        if len(selected) != 1 or len(self.model_fields_set) != 1:
            raise ValueError("length requires exactly one comparison")
        return self

    @property
    def comparison(self) -> tuple[Literal["gt", "gte", "lt", "lte", "equals"], int]:
        """Return the single authored comparison and its integer operand."""
        for name in ("gt", "gte", "lt", "lte", "equals"):
            value = getattr(self, name)
            if value is not None:
                return name, value
        raise AssertionError("validated length comparison")


class LeafCondition(BoundaryModel):
    """One source (`binding` or `literal`) and exactly one operator."""

    binding: ConditionSource | None = None
    literal: JsonValue = None
    present: StrictBool | None = None
    empty: StrictBool | None = None
    equals: JsonValue = None
    not_equals: JsonValue = None
    in_: Annotated[list[JsonValue], Field(min_length=1, max_length=MAX_IN_VALUES)] | None = Field(
        default=None, alias="in"
    )
    not_in: Annotated[list[JsonValue], Field(min_length=1, max_length=MAX_IN_VALUES)] | None = None
    gt: _Number | None = None
    gte: _Number | None = None
    lt: _Number | None = None
    lte: _Number | None = None
    matches: Annotated[str, Field(min_length=1, max_length=MAX_PATTERN_LENGTH)] | None = None
    length: LengthComparison | None = None

    @model_validator(mode="after")
    def one_source_and_one_operator(self) -> Self:
        sources = {"binding", "literal"} & self.model_fields_set
        if len(sources) != 1 or ("binding" in sources and self.binding is None):
            raise ValueError("a condition requires exactly one source")
        operators = [name for name in _OPERATOR_FIELDS if name in self.model_fields_set]
        if len(operators) != 1:
            raise ValueError("a condition requires exactly one operator")
        name = operators[0]
        if name not in {"equals", "not_equals"} and getattr(self, name) is None:
            raise ValueError("the operator operand cannot be null")
        if self.matches is not None:
            try:
                re.compile(self.matches)
            except (re.error, RecursionError, OverflowError):
                raise ValueError("the pattern is not a valid regular expression") from None
        return self

    @property
    def operator(self) -> str:
        """Return the public operator name, independent of Python field aliases."""
        for name, public in _OPERATOR_FIELDS.items():
            if name in self.model_fields_set:
                return public
        raise AssertionError("validated condition")

    @property
    def operand(self) -> JsonValue:
        """Return the authored operand; `length` exposes its comparison separately."""
        name = next(name for name, public in _OPERATOR_FIELDS.items() if public == self.operator)
        value: object = getattr(self, name)
        if isinstance(value, LengthComparison):
            return value.comparison[1]
        return cast(JsonValue, value)


class AllCondition(BoundaryModel):
    """Logical and over 1..32 conditions."""

    all: Annotated[list["Condition"], Field(min_length=1, max_length=MAX_CONDITION_OPERANDS)]

    @model_validator(mode="after")
    def bounded_depth(self) -> Self:
        _check_depth(self)
        return self


class AnyCondition(BoundaryModel):
    """Logical or over 1..32 conditions."""

    any: Annotated[list["Condition"], Field(min_length=1, max_length=MAX_CONDITION_OPERANDS)]

    @model_validator(mode="after")
    def bounded_depth(self) -> Self:
        _check_depth(self)
        return self


class NotCondition(BoundaryModel):
    """Logical negation of one condition."""

    not_: "Condition" = Field(alias="not")

    @model_validator(mode="after")
    def bounded_depth(self) -> Self:
        _check_depth(self)
        return self


def _condition_kind(value: object) -> str:
    if isinstance(value, Mapping):
        for key in ("all", "any", "not"):
            if key in value:
                return key
        return "leaf"
    if isinstance(value, AllCondition):
        return "all"
    if isinstance(value, AnyCondition):
        return "any"
    if isinstance(value, NotCondition):
        return "not"
    return "leaf"


Condition = Annotated[
    Annotated[LeafCondition, Tag("leaf")]
    | Annotated[AllCondition, Tag("all")]
    | Annotated[AnyCondition, Tag("any")]
    | Annotated[NotCondition, Tag("not")],
    Discriminator(_condition_kind),
]


def condition_depth(condition: "LeafCondition | AllCondition | AnyCondition | NotCondition") -> int:
    """Return the nesting depth; a leaf has depth one."""
    if isinstance(condition, AllCondition):
        return 1 + max(condition_depth(item) for item in condition.all)
    if isinstance(condition, AnyCondition):
        return 1 + max(condition_depth(item) for item in condition.any)
    if isinstance(condition, NotCondition):
        return 1 + condition_depth(condition.not_)
    return 1


def _check_depth(condition: AllCondition | AnyCondition | NotCondition) -> None:
    if condition_depth(condition) > MAX_CONDITION_DEPTH:
        raise ValueError("conditions nest at most eight levels")


AllCondition.model_rebuild()
AnyCondition.model_rebuild()
NotCondition.model_rebuild()
