from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import ValidationError

from foliqant.decisions import (
    CategoryCatalog,
    ChoiceQuestion,
    DecisionInput,
    DecisionOutput,
    DecisionSource,
    DecisionState,
    normalize_category_key,
    validate_decision_output,
)


def _catalog() -> CategoryCatalog:
    return CategoryCatalog.model_validate(
        {
            "categories": [
                {
                    "id": "fee_refund",
                    "description": "Refund a fee charged to the account.",
                },
                {
                    "id": "statement_request",
                    "description": "Kontoauszug für den genannten Zeitraum senden.",
                },
            ]
        },
        strict=True,
    )


def test_category_catalog_preserves_stable_keys_and_bilingual_descriptions() -> None:
    catalog = _catalog()

    assert [option.model_dump(mode="json") for option in catalog.decision_options()] == [
        {
            "id": "fee_refund",
            "description": "Refund a fee charged to the account.",
        },
        {
            "id": "statement_request",
            "description": "Kontoauszug für den genannten Zeitraum senden.",
        },
    ]


def test_category_description_allows_multiline_unicode_prose() -> None:
    description = "Erste Zeile\nZweite Zeile: Gebühren zurückerstatten."
    catalog = CategoryCatalog.model_validate(
        {"categories": [{"id": "fee_refund", "description": description}]},
        strict=True,
    )

    assert catalog.categories[0].description == description


def test_category_catalog_schema_accepts_raw_key_and_multiline_description() -> None:
    schema = CategoryCatalog.model_json_schema(mode="validation")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(
        {
            "categories": [
                {
                    "id": "Request Info",
                    "description": "First line\nZweite Zeile.",
                }
            ]
        }
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Request Info", "request_info"),
        ("request-info", "request_info"),
        ("  FEE / Refund  ", "fee_refund"),
        ("account__status", "account_status"),
    ],
)
def test_category_keys_are_normalized_deterministically(raw: str, expected: str) -> None:
    catalog = CategoryCatalog.model_validate(
        {"categories": [{"id": raw, "description": "Detailed category semantics."}]},
        strict=True,
    )

    assert catalog.categories[0].id == expected
    assert catalog.model_dump(mode="json")["categories"][0]["id"] == expected


@pytest.mark.parametrize("category_id", ["1 refund", "---", "Überweisung", "fee\trefund"])
def test_category_catalog_rejects_unsupported_keys(category_id: str) -> None:
    with pytest.raises(ValidationError, match="category IDs"):
        CategoryCatalog.model_validate(
            {"categories": [{"id": category_id, "description": "Refund a fee."}]},
            strict=True,
        )


@pytest.mark.parametrize("description", ["", " ", "\n\t"])
def test_category_catalog_rejects_blank_descriptions(description: str) -> None:
    with pytest.raises(ValidationError, match="string_pattern_mismatch|string_too_short"):
        CategoryCatalog.model_validate(
            {"categories": [{"id": "fee_refund", "description": description}]},
            strict=True,
        )


def test_category_catalog_rejects_duplicate_keys() -> None:
    with pytest.raises(ValidationError, match="category IDs must be unique"):
        CategoryCatalog.model_validate(
            {
                "categories": [
                    {"id": "Request Info", "description": "Request information."},
                    {"id": "request-info", "description": "Ask for information."},
                ]
            },
            strict=True,
        )


def test_category_resolution_normalizes_formatting_but_never_fuzzy_matches() -> None:
    catalog = _catalog()

    assert catalog.resolve_id("Fee-Refund") == "fee_refund"
    assert normalize_category_key("Statement Request") == "statement_request"
    with pytest.raises(ValueError, match="unknown category ID: refund_fee"):
        catalog.resolve_id("Refund Fee")


def test_decision_validation_rejects_key_outside_authored_catalog() -> None:
    source = DecisionSource(id="request-source", kind="message", text="Please refund the fee.")
    task = DecisionInput(
        state=DecisionState(sources=[source]),
        questions=[
            ChoiceQuestion(
                id="route",
                type="choice",
                prompt="Choose the matching category.",
                criteria=["Select the supported request."],
                allowedSourceIds=[source.id],
                options=_catalog().decision_options(),
            )
        ],
    )
    output = DecisionOutput.model_validate(
        {
            "schemaVersion": 1,
            "results": [
                {
                    "questionId": "route",
                    "type": "choice",
                    "answerability": {"status": "answerable", "issues": []},
                    "answer": {"optionId": "invented_category"},
                    "explanation": {
                        "summary": "The fee refund request is explicit.",
                        "evidence": [
                            {"sourceId": "request-source", "quote": "Please refund the fee."}
                        ],
                        "contraryEvidence": [],
                        "missingFacts": [],
                    },
                }
            ],
        },
        strict=True,
    )

    assert validate_decision_output(task, output) == ["question:route:unknown-option"]
