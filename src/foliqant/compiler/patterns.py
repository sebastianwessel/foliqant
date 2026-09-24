"""Reject `matches` patterns whose backtracking is not bounded in time.

The check walks the standard-library regular-expression parse tree; it never
matches a regular expression against another one. Together with the bounded
value length (:data:`foliqant.core.conditions.MAX_MATCH_LENGTH`) it bounds the
work of one ``fullmatch``:

* an unbounded quantifier (``*``, ``+``, ``{m,}``) over a group that contains a
  backtracking unbounded quantifier (``(a+)+``), a variable-length ambiguous
  part (``(a{1,2})*``) or an alternation whose branches may overlap
  (``(a|ab)*``) is rejected; an alternation of equal fixed-width branches with
  distinct first literals (``(?:foo|bar)+``) matches deterministically;
* a bounded quantifier (``?``, ``{m,n}``) over a group costs ``bound × inner``
  work, except that ambiguous bounded iterations multiply (``(a{1,2}){1,4}`` is
  64 choices, ``(a{1,2}){1,40}`` explodes);
* the product of all choices must not exceed the work of three independent
  unbounded quantifiers over a bounded value (``\\d+(?:\\.\\d+)?`` and
  ``.*a.*b.*`` pass, ``a*a*a*a*b`` does not).

Possessive quantifiers (``a++``) never give back what they matched, so they do
not count as backtracking; atomic groups and lookarounds are checked inside but
their contents still count, because they may be re-entered at every position.
"""

import importlib
import re
from dataclasses import dataclass
from typing import Protocol, cast

from foliqant.core.conditions import MAX_MATCH_LENGTH

_PARSER = importlib.import_module("re._parser")
_CONSTANTS = importlib.import_module("re._constants")
_MAXREPEAT = cast(int, _CONSTANTS.MAXREPEAT)
_OPS = {
    name: getattr(_CONSTANTS, name)
    for name in (
        "MAX_REPEAT",
        "MIN_REPEAT",
        "POSSESSIVE_REPEAT",
        "SUBPATTERN",
        "BRANCH",
        "ATOMIC_GROUP",
        "ASSERT",
        "ASSERT_NOT",
        "GROUPREF_EXISTS",
        "LITERAL",
    )
}
MAX_PATTERN_WORK = (MAX_MATCH_LENGTH + 1) ** 3
"""Estimated backtracking choices allowed for one pattern."""


class _Pattern(Protocol):
    def getwidth(self) -> tuple[int, int]: ...


class UnsafePatternError(ValueError):
    """The pattern may backtrack for longer than the bounded evaluation allows."""


@dataclass(frozen=True, slots=True)
class _Shape:
    work: int
    """Estimated choices explored while matching the subpattern at one position."""
    ambiguous: bool
    """More than one way to match: a backtracking variable quantifier or alternation."""
    unbounded: bool
    """Contains a backtracking quantifier without an upper bound."""
    alternation: bool
    """Contains an alternation outside atomic groups."""


_FIXED = _Shape(1, False, False, False)


def _cap(value: int) -> int:
    return min(value, MAX_PATTERN_WORK + 1)


def _power(base: int, exponent: int) -> int:
    result = 1
    for _ in range(exponent):
        result = _cap(result * base)
        if result > MAX_PATTERN_WORK:
            break
    return result


def _sequence(items: object) -> _Shape:
    work, ambiguous, unbounded, alternation = 1, False, False, False
    for op, argument in cast(list[tuple[object, object]], list(cast(list[object], items))):
        shape = _item(op, argument)
        work = _cap(work * shape.work)
        ambiguous = ambiguous or shape.ambiguous
        unbounded = unbounded or shape.unbounded
        alternation = alternation or shape.alternation
    return _Shape(work, ambiguous, unbounded, alternation)


def _deterministic(alternatives: list[object]) -> bool:
    """At most one branch can match at a position: equal fixed widths, distinct first literals.

    Literals are compared case-folded, so ``(?i)`` never makes two branches overlap.
    """
    widths: set[tuple[int, int]] = set()
    firsts: list[str] = []
    for alternative in alternatives:
        width = cast(_Pattern, alternative).getwidth()
        items = cast(list[tuple[object, object]], list(cast(list[object], alternative)))
        if width[0] != width[1] or not items or items[0][0] is not _OPS["LITERAL"]:
            return False
        widths.add(width)
        firsts.append(chr(cast(int, items[0][1])).casefold())
    return len(widths) == 1 and len(set(firsts)) == len(firsts)


def _branches(alternatives: list[object]) -> _Shape:
    shapes = [_sequence(item) for item in alternatives]
    inner = any(shape.ambiguous for shape in shapes)
    overlapping = len(shapes) > 1 and not _deterministic(alternatives)
    return _Shape(
        _cap(sum(shape.work for shape in shapes)),
        overlapping or inner,
        any(shape.unbounded for shape in shapes),
        overlapping or any(shape.alternation for shape in shapes),
    )


def _repeat(low: int, high: int, body: _Shape) -> _Shape:
    unbounded = high == _MAXREPEAT
    if unbounded and body.unbounded:
        # An unbounded quantifier around an unbounded quantifier: `(a+)+`, `(\\s*x)*`.
        raise UnsafePatternError("nested unbounded quantifier")
    if unbounded and (body.alternation or body.ambiguous):
        # Unboundedly many ambiguous iterations: `(a|ab)*`, `(a{1,2})*`.
        raise UnsafePatternError("unbounded ambiguous repetition")
    # A bounded value admits at most MAX_MATCH_LENGTH nonempty iterations.
    iterations = min(high, MAX_MATCH_LENGTH)
    counts = max(iterations - low, 0) + 1
    if body.ambiguous and not body.unbounded:
        # Each of a bounded number of iterations may match in several ways: the
        # choices multiply (`(a{1,2}){1,40}` explodes, `(a{1,2}){1,4}` does not).
        work = _power(body.work, iterations) * counts
    else:
        # A bounded quantifier over an unbounded body costs bound × inner work.
        work = counts * body.work
    return _Shape(
        _cap(work),
        body.ambiguous or counts > 1,
        unbounded or body.unbounded,
        body.alternation,
    )


def _item(op: object, argument: object) -> _Shape:
    if op in (_OPS["MAX_REPEAT"], _OPS["MIN_REPEAT"]):
        low, high, body = cast(tuple[int, int, object], argument)
        return _repeat(low, high, _sequence(body))
    if op is _OPS["POSSESSIVE_REPEAT"]:
        inner = _sequence(cast(tuple[int, int, object], argument)[2])
        # Never gives back matched iterations; only each iteration's own choices remain.
        return _Shape(inner.work, False, False, False)
    if op is _OPS["SUBPATTERN"]:
        return _sequence(cast(tuple[object, ...], argument)[-1])
    if op is _OPS["BRANCH"]:
        return _branches(cast(list[object], cast(tuple[object, ...], argument)[1]))
    if op in (_OPS["ATOMIC_GROUP"], _OPS["ASSERT"], _OPS["ASSERT_NOT"]):
        body = argument if op is _OPS["ATOMIC_GROUP"] else cast(tuple[object, ...], argument)[1]
        inner = _sequence(body)
        # Committed after one match, but re-run at every position it is tried.
        return _Shape(inner.work, False, False, False)
    if op is _OPS["GROUPREF_EXISTS"]:
        _, yes, no = cast(tuple[object, object, object], argument)
        return _branches([yes] if no is None else [yes, no])
    return _FIXED


def check_pattern(pattern: str) -> None:
    """Raise :class:`UnsafePatternError` unless matching has bounded backtracking."""
    try:
        parsed = _PARSER.parse(pattern)
    except (re.error, RecursionError, OverflowError):
        raise UnsafePatternError("invalid pattern") from None
    if _sequence(parsed).work > MAX_PATTERN_WORK:
        raise UnsafePatternError("backtracking work exceeds the bound")


__all__ = ["MAX_PATTERN_WORK", "UnsafePatternError", "check_pattern"]
