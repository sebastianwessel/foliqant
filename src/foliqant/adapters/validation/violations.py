"""Explain an invalid output without content, and give the model its correction feedback.

A location is a JSON pointer whose object keys are restricted to the schema's own
vocabulary (declared property names and constant values); any other key becomes
``*``. The correction feedback may quote the model's own output and goes only to
the model that produced it; it never enters a result, log or span.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match

from foliqant.core.errors import ErrorCode, FailureReason, ServiceError

_MAX_FEEDBACK = 800
_MAX_PROBLEMS = 8
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


class InvalidOutput(ServiceError):
    """``invalid_output`` with a safe explanation and private correction feedback."""

    def __init__(
        self,
        reason: FailureReason,
        *,
        location: str | None = None,
        constraint: str | None = None,
        feedback: str,
    ) -> None:
        super().__init__(
            ErrorCode.INVALID_OUTPUT, reason=reason, location=location, constraint=constraint
        )
        self.feedback = feedback[:_MAX_FEEDBACK]


def schema_vocabulary(schema: object) -> frozenset[str]:
    """Property names and string constants declared anywhere in a schema document."""
    names: set[str] = set()
    pending: list[object] = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, Mapping):
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                names.update(key for key in properties if isinstance(key, str))
            for key in ("const", "enum"):
                value = node.get(key)
                values = value if isinstance(value, list) else [value]
                names.update(item for item in values if isinstance(item, str))
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return frozenset(name for name in names if _IDENTIFIER.fullmatch(name))


def safe_pointer(path: Iterable[object], vocabulary: frozenset[str]) -> str:
    """A JSON pointer of indices and schema vocabulary; other keys become ``*``."""
    segments = [
        str(item)
        if (type(item) is int and item >= 0) or (isinstance(item, str) and item in vocabulary)
        else "*"
        for item in path
    ]
    return "/" + "/".join(segments) if segments else "/"


def _keyword(value: object) -> str:
    return re.sub(r"[^a-z0-9_]", "_", str(value).lower())[:64]


def jsonschema_violation(
    validator: Draft202012Validator, value: object, vocabulary: frozenset[str]
) -> InvalidOutput | None:
    """The most relevant schema violation of ``value``, or ``None`` when it is valid."""
    error = best_match(validator.iter_errors(value))
    if error is None:
        return None
    return _schema_violation(error, vocabulary)


def _schema_violation(error: ValidationError, vocabulary: frozenset[str]) -> InvalidOutput:
    location = safe_pointer(error.absolute_path, vocabulary)
    constraint = _keyword(error.validator)
    return InvalidOutput(
        "schema_violation",
        location=location,
        constraint=constraint,
        feedback=f"The value at {location} violates `{constraint}`: {error.message}",
    )


def pydantic_violation(
    errors: Sequence[Mapping[str, Any]], vocabulary: frozenset[str]
) -> InvalidOutput:
    """The first problem of a pydantic validation of a parsed model output."""
    first = errors[0] if errors else {}
    kind = _keyword(first.get("type", "invalid"))
    location = safe_pointer(first.get("loc", ()), vocabulary)
    reason: FailureReason = "json_parse_error" if kind == "json_invalid" else "schema_violation"
    lines = [
        f"{safe_pointer(item.get('loc', ()), vocabulary)}: {item.get('msg', '')}"
        for item in errors[:_MAX_PROBLEMS]
    ]
    return InvalidOutput(
        reason, location=location, constraint=kind, feedback="; ".join(lines) or kind
    )


def contract_violation(problems: Sequence[str], questions: frozenset[str]) -> InvalidOutput:
    """The first decision-contract problem, ``question:<id>:<kind>``, as a violation.

    The location names only a configured question; an ID the model invented is omitted.
    """
    first = problems[0] if problems else "result:invalid"
    prefix, _, kind = first.rpartition(":")
    question = prefix.removeprefix("question:")
    return InvalidOutput(
        "decision_contract",
        location=prefix if question in questions else None,
        constraint=_keyword(kind),
        feedback=(
            "The decision result violates its contract: "
            + "; ".join(problems[:_MAX_PROBLEMS])
            + ". Correct these problems and return the complete result again."
        ),
    )
