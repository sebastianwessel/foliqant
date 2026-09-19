"""Deterministic, label-independent scoring for model evaluation."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import unquote

from jsonschema import Draft202012Validator, SchemaError  # type: ignore[import-untyped]
from referencing import Registry
from referencing.exceptions import NoSuchResource

from .contracts import ChatMessage, CountRate, EvaluationAggregate, Prediction
from .contracts.base import JsonValue
from .errors import ModelError


@dataclass(frozen=True)
class ParsedJson:
    """A strict JSON parse whose flag distinguishes invalid input from JSON null."""

    valid: bool
    value: JsonValue | None


@dataclass(frozen=True)
class ReferenceScore:
    """Prevalidated reference values used after generation."""

    parsed: ParsedJson
    fields: Mapping[str, JsonValue]


@dataclass(frozen=True)
class PredictionScore:
    """All scoring fields derived from a generated string."""

    parsed: ParsedJson
    schema_valid: bool | None
    evidence_valid: bool | None
    field_present: Mapping[str, bool]
    field_valid: Mapping[str, bool]
    applicable_valid: bool
    exact_correct: bool


_MISSING = object()


def parse_strict_json(text: str) -> ParsedJson:
    """Parse RFC-8259 JSON, rejecting duplicate keys and nonfinite numbers."""

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(f"nonfinite number {value}")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
        _reject_nonfinite(value)
        _reject_invalid_unicode(value)
    except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError):
        return ParsedJson(valid=False, value=None)
    return ParsedJson(valid=True, value=cast(JsonValue, value))


def validate_output_schema(value: object) -> Draft202012Validator:
    """Build a retrieval-free Draft 2020-12 validator after checking all references."""

    _reject_external_references(value)
    _validate_local_references(value)

    def deny_retrieval(uri: str) -> object:
        raise NoSuchResource(uri)

    try:
        Draft202012Validator.check_schema(value)
        registry = Registry(retrieve=deny_retrieval)  # type: ignore[call-arg]
        return Draft202012Validator(value, registry=registry)
    except SchemaError as error:
        raise ModelError("CONFIG_INVALID", "Output schema is not valid Draft 2020-12") from error


def prepare_reference(
    expected: str,
    messages: Sequence[ChatMessage],
    *,
    schema: Draft202012Validator | None,
    evidence_pointer: str | None,
    field_pointers: Sequence[str],
) -> ReferenceScore:
    """Validate every configured requirement against a reference before generation."""

    parsed = parse_strict_json(expected)
    json_required = schema is not None or evidence_pointer is not None or bool(field_pointers)
    if json_required and not parsed.valid:
        raise ModelError("DATA_RECORD_INVALID", "Reference output must be valid JSON")
    if schema is not None and not schema.is_valid(parsed.value):
        raise ModelError("DATA_RECORD_INVALID", "Reference output does not satisfy output schema")
    if evidence_pointer is not None and not _evidence_valid(
        parsed.value, evidence_pointer, messages
    ):
        raise ModelError("DATA_RECORD_INVALID", "Reference evidence is invalid")
    fields: dict[str, JsonValue] = {}
    for pointer in field_pointers:
        value = resolve_json_pointer(parsed.value, pointer)
        if value is _MISSING:
            raise ModelError("DATA_RECORD_INVALID", "Reference field pointer does not resolve")
        fields[pointer] = cast(JsonValue, value)
    return ReferenceScore(parsed=parsed, fields=fields)


def score_generated(
    generated: str,
    expected: str,
    reference: ReferenceScore,
    messages: Sequence[ChatMessage],
    *,
    schema: Draft202012Validator | None,
    evidence_pointer: str | None,
    field_pointers: Sequence[str],
) -> PredictionScore:
    """Score one prediction using only deterministic configured validators."""

    parsed = parse_strict_json(generated)
    schema_valid = None if schema is None else parsed.valid and schema.is_valid(parsed.value)
    evidence_valid = (
        None
        if evidence_pointer is None
        else parsed.valid and _evidence_valid(parsed.value, evidence_pointer, messages)
    )
    present: dict[str, bool] = {}
    valid: dict[str, bool] = {}
    for pointer in field_pointers:
        value = resolve_json_pointer(parsed.value, pointer) if parsed.valid else _MISSING
        present[pointer] = value is not _MISSING
        valid[pointer] = value is not _MISSING and structural_equal(
            value, reference.fields[pointer]
        )
    json_required = schema is not None or evidence_pointer is not None or bool(field_pointers)
    applicable = (
        (parsed.valid or not json_required)
        and schema_valid is not False
        and evidence_valid is not False
        and all(present.values())
    )
    if parsed.valid and reference.parsed.valid:
        base_equal = structural_equal(parsed.value, reference.parsed.value)
    elif not parsed.valid and not reference.parsed.valid:
        base_equal = generated.strip() == expected.strip()
    else:
        base_equal = False
    return PredictionScore(
        parsed=parsed,
        schema_valid=schema_valid,
        evidence_valid=evidence_valid,
        field_present=present,
        field_valid=valid,
        applicable_valid=applicable,
        exact_correct=base_equal and applicable,
    )


def resolve_json_pointer(value: object, pointer: str) -> object:
    """Resolve an already validated RFC 6901 pointer, returning a private sentinel if absent."""

    current = value
    if pointer == "":
        return current
    for encoded in pointer.split("/")[1:]:
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            if token == "-" or not token.isascii() or not token.isdigit():
                return _MISSING
            if len(token) > 1 and token.startswith("0"):
                return _MISSING
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def json_pointer_value(value: object, pointer: str) -> tuple[bool, JsonValue | None]:
    """Resolve a pointer while preserving the distinction between absent and JSON null."""

    resolved = resolve_json_pointer(value, pointer)
    if resolved is _MISSING:
        return False, None
    return True, cast(JsonValue, resolved)


def structural_equal(left: object, right: object) -> bool:
    """Compare JSON values with numeric equivalence but distinct booleans."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is bool and type(right) is bool and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        right_mapping = cast(dict[object, object], right)
        return left.keys() == right_mapping.keys() and all(
            structural_equal(left[key], right_mapping[key]) for key in left
        )
    if isinstance(left, list):
        right_list = cast(list[object], right)
        return len(left) == len(right_list) and all(
            structural_equal(a, b) for a, b in zip(left, right_list, strict=True)
        )
    return left == right


def aggregate_predictions(
    predictions: Sequence[Prediction], field_pointers: Sequence[str]
) -> EvaluationAggregate:
    """Aggregate predictions with explicit configured-validator denominators."""

    total = len(predictions)
    schema_configured = any(item.schemaValid is not None for item in predictions)
    evidence_configured = any(item.evidenceValid is not None for item in predictions)
    return EvaluationAggregate(
        total=total,
        exact=_rate(total, sum(item.exactCorrect for item in predictions)),
        json=_rate(total, sum(item.jsonValid for item in predictions)),
        schema=_rate(
            total if schema_configured else 0,
            sum(item.schemaValid is True for item in predictions),
        ),
        evidence=_rate(
            total if evidence_configured else 0,
            sum(item.evidenceValid is True for item in predictions),
        ),
        applicableValidation=_rate(total, sum(item.applicableValid for item in predictions)),
        fields={
            pointer: _rate(total, sum(item.fieldValid[pointer] for item in predictions))
            for pointer in field_pointers
        },
    )


def _rate(eligible: int, passed: int) -> CountRate:
    return CountRate(
        eligibleCount=eligible,
        passedCount=passed,
        rate=None if eligible == 0 else passed / eligible,
    )


def _evidence_valid(value: object, pointer: str, messages: Sequence[ChatMessage]) -> bool:
    evidence = resolve_json_pointer(value, pointer)
    if isinstance(evidence, str):
        quotes = [evidence] if evidence else []
    elif (
        isinstance(evidence, list)
        and evidence
        and all(isinstance(item, str) and item for item in evidence)
    ):
        quotes = cast(list[str], evidence)
    else:
        return False
    return bool(quotes) and all(
        any(quote in message.content for message in messages) for quote in quotes
    )


def _reject_nonfinite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite number")
    if isinstance(value, list):
        for item in value:
            _reject_nonfinite(item)


def _reject_invalid_unicode(value: object) -> None:
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, list):
        for item in value:
            _reject_invalid_unicode(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            key.encode("utf-8", errors="strict")
            _reject_invalid_unicode(item)
    elif isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite(item)


def _reject_external_references(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef"} and (
                not isinstance(item, str) or not item.startswith("#")
            ):
                raise ModelError("CONFIG_INVALID", "Output schema references must be fragments")
            _reject_external_references(item)
    elif isinstance(value, list):
        for item in value:
            _reject_external_references(item)


def _validate_local_references(schema: object) -> None:
    anchors: set[str] = set()
    references: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$anchor", "$dynamicAnchor"} and isinstance(item, str):
                    anchors.add(item)
                elif key in {"$ref", "$dynamicRef"} and isinstance(item, str):
                    references.append(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(schema)
    for reference in references:
        fragment = unquote(reference[1:])
        if not fragment:
            continue
        if fragment.startswith("/"):
            if resolve_json_pointer(schema, fragment) is _MISSING:
                raise ModelError("CONFIG_INVALID", "Output schema fragment does not resolve")
        elif fragment not in anchors:
            raise ModelError("CONFIG_INVALID", "Output schema anchor does not resolve")
