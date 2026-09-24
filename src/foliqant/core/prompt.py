"""Compile exact input placeholders and render selected JSON data once."""

import json
import re
from collections.abc import Collection
from dataclasses import dataclass

from .errors import ErrorCode, ServiceError
from .json import FrozenObject, freeze_json, thaw_json

# Keep names aligned with contracts.identifiers.Id without importing Pydantic.
_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", flags=re.ASCII)
_PLACEHOLDER = re.compile(r"[ \t]*([a-z][a-z0-9]*(?:_[a-z0-9]+)*)[ \t]*", flags=re.ASCII)
_DELIMITER = re.compile(r"\{\{\{\{|\}\}\}\}|\{\{|\}\}")


@dataclass(frozen=True, slots=True)
class _Input:
    name: str


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """Immutable literal and input tokens produced by :func:`compile_prompt`."""

    parts: tuple[str | _Input, ...]


def compile_prompt(template: str, input_names: Collection[str]) -> PromptTemplate:
    """Validate ``{{ name }}`` against exact declared snake_case input names.

    Spaces and tabs around names are optional. Single braces are literal;
    ``{{{{`` and ``}}}}`` emit literal ``{{`` and ``}}``. For example,
    ``{{{{ message }}}}`` renders the literal text ``{{ message }}``.
    Unmatched double braces and all expression syntax are rejected. Compilation
    never reads values, environment variables or external resources.
    """
    if (
        not isinstance(template, str)
        or isinstance(input_names, str)
        or any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in input_names)
        or len(set(input_names)) != len(input_names)
    ):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    declared = frozenset(input_names)
    parts: list[str | _Input] = []
    literal: list[str] = []
    position = 0
    while marker := _DELIMITER.search(template, position):
        literal.append(template[position : marker.start()])
        token = marker.group()
        if token in {"{{{{", "}}}}"}:
            literal.append(token[:2])
            position = marker.end()
            continue
        if token == "}}":
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        end = template.find("}}", marker.end())
        if end == -1:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        placeholder = _PLACEHOLDER.fullmatch(template[marker.end() : end])
        if placeholder is None or placeholder[1] not in declared:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if literal:
            parts.append("".join(literal))
            literal.clear()
        parts.append(_Input(placeholder[1]))
        position = end + 2
    literal.append(template[position:])
    if literal:
        parts.append("".join(literal))
    return PromptTemplate(tuple(parts))


def render_prompt(template: PromptTemplate, inputs: FrozenObject) -> str:
    """Render only referenced inputs as compact JSON, including quoted strings.

    Object keys are sorted and Unicode text is preserved. Missing values fail;
    explicit null renders as ``null``. Bound values are serialized once and
    never interpreted as placeholders, even when they contain template syntax.
    Input binding resolution and defaults remain the caller's responsibility.
    """
    rendered: list[str] = []
    for part in template.parts:
        if isinstance(part, str):
            rendered.append(part)
            continue
        if part.name not in inputs:
            raise ServiceError(ErrorCode.MISSING_BINDING)
        value = thaw_json(freeze_json(inputs[part.name]))
        try:
            rendered.append(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        except (ValueError, TypeError, RecursionError):
            raise ServiceError(ErrorCode.INVALID_INPUT) from None
    return "".join(rendered)


def prompt_inputs(template: PromptTemplate) -> frozenset[str]:
    """Return the input names a compiled template references."""
    return frozenset(part.name for part in template.parts if isinstance(part, _Input))
