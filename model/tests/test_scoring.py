"""Deterministic scoring tests for strict JSON, evidence, and configured fields."""

from __future__ import annotations

import pytest

from foliqant_model.contracts import ChatMessage, Prediction
from foliqant_model.errors import ModelError
from foliqant_model.scoring import (
    aggregate_predictions,
    parse_strict_json,
    prepare_reference,
    score_generated,
    structural_equal,
    validate_output_schema,
)


def test_strict_json_rejects_duplicates_and_nonfinite_and_distinguishes_null() -> None:
    assert parse_strict_json("null").valid is True
    assert parse_strict_json("null").value is None
    assert parse_strict_json('{"a":1,"a":2}').valid is False
    assert parse_strict_json("NaN").valid is False
    assert parse_strict_json("1e999").valid is False
    assert parse_strict_json('"\ud800"').valid is False
    assert structural_equal(1, 1.0)
    assert not structural_equal(True, 1)


def test_configured_scoring_separates_field_correctness_from_applicability() -> None:
    messages = [ChatMessage(role="user", content="The source says alpha exactly.")]
    schema = validate_output_schema(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["answer", "evidence"],
            "properties": {
                "answer": {"type": "string"},
                "evidence": {"type": "string"},
            },
        }
    )
    expected = '{"answer":"yes","evidence":"alpha"}'
    reference = prepare_reference(
        expected,
        messages,
        schema=schema,
        evidence_pointer="/evidence",
        field_pointers=["/answer"],
    )
    score = score_generated(
        '{"answer":"no","evidence":"alpha"}',
        expected,
        reference,
        messages,
        schema=schema,
        evidence_pointer="/evidence",
        field_pointers=["/answer"],
    )

    assert score.applicable_valid is True
    assert score.field_present == {"/answer": True}
    assert score.field_valid == {"/answer": False}
    assert score.exact_correct is False


def test_aggregate_zero_denominators_are_null() -> None:
    prediction = Prediction.model_validate(
        {
            "id": "record",
            "sourceId": "source",
            "language": "en",
            "tags": [],
            "componentId": "a" * 64,
            "groupIds": ["b" * 64],
            "representative": True,
            "expected": "plain text",
            "generated": "plain text",
            "expectedJson": None,
            "generatedJson": None,
            "elapsedSeconds": 0.1,
            "generatedTokens": 1,
            "meanTokenLogprob": -0.1,
            "jsonValid": False,
            "finishReason": "stop",
            "schemaValid": None,
            "evidenceValid": None,
            "fieldPresent": {},
            "fieldValid": {},
            "applicableValid": True,
            "exactCorrect": True,
        },
        strict=True,
    )

    aggregate = aggregate_predictions([prediction], [])
    assert aggregate.schemaMetric.eligibleCount == 0
    assert aggregate.schemaMetric.rate is None
    assert aggregate.evidence.eligibleCount == 0
    assert aggregate.exact.rate == 1.0


@pytest.mark.parametrize(
    "reference",
    ["https://example.invalid/schema.json", "#/missing", "#missing-anchor"],
)
def test_schema_references_cannot_retrieve_or_target_missing_fragments(reference: str) -> None:
    with pytest.raises(ModelError) as captured:
        validate_output_schema({"$ref": reference})
    assert captured.value.code == "CONFIG_INVALID"


def test_same_document_schema_reference_is_supported_without_retrieval() -> None:
    validator = validate_output_schema(
        {"$defs": {"answer": {"type": "string"}}, "$ref": "#/$defs/answer"}
    )
    assert validator.is_valid("yes")
    assert not validator.is_valid(1)
