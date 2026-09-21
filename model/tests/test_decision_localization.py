"""Localization must preserve machine contracts and exact German evidence."""

from __future__ import annotations

import pytest
from foliqant_decisions import (
    DecisionInput,
    DecisionOutput,
    RequestUnitsResult,
    validate_decision_output,
)

from foliqant_model.curation.decision_localization import localize_case


def _case() -> tuple[DecisionInput, DecisionOutput, dict[str, str]]:
    source = "Please send my January statement."
    task = DecisionInput.model_validate(
        {
            "state": {"sources": [{"id": "message-1", "kind": "message", "text": source}]},
            "questions": [
                {
                    "id": "requests",
                    "type": "request_units",
                    "prompt": "Identify the request.",
                    "criteria": ["Use only the message."],
                    "allowedSourceIds": ["message-1"],
                    "catalog": [{"id": "statement_request", "description": "Send a statement"}],
                    "allowNoMatch": False,
                }
            ],
        }
    )
    oracle = DecisionOutput.model_validate(
        {
            "results": [
                {
                    "questionId": "requests",
                    "type": "request_units",
                    "answerability": {"status": "answerable", "issues": []},
                    "answer": {
                        "units": [
                            {
                                "id": "r1",
                                "categoryId": "statement_request",
                                "status": "active",
                                "subject": "January",
                                "description": "Send the January statement",
                                "evidence": [
                                    {"sourceId": "message-1", "quote": "January statement"}
                                ],
                            }
                        ],
                        "relations": [],
                    },
                    "explanation": {
                        "summary": "The January statement is requested.",
                        "evidence": [{"sourceId": "message-1", "quote": source}],
                        "contraryEvidence": [],
                        "missingFacts": [],
                    },
                }
            ]
        }
    )
    translations = {
        source: "Bitte senden Sie mir meinen Kontoauszug für Januar.",
        "Identify the request.": "Erkennen Sie die Anfrage.",
        "Use only the message.": "Verwenden Sie nur die Nachricht.",
        "Send a statement": "Einen Kontoauszug senden",
        "January": "Januar",
        "Send the January statement": "Den Kontoauszug für Januar senden",
        "The January statement is requested.": "Der Kontoauszug für Januar wird angefordert.",
    }
    return task, oracle, translations


def test_localization_preserves_ids_and_grounds_translated_subject() -> None:
    task, oracle, translations = _case()
    original_task, original_oracle = task.model_dump(), oracle.model_dump()
    localized_task, localized_oracle = localize_case(task, oracle, translations)
    result = localized_oracle.results[0]
    assert isinstance(result, RequestUnitsResult) and result.answer is not None
    unit = result.answer.units[0]
    assert (result.questionId, unit.id, unit.categoryId, unit.status) == (
        "requests",
        "r1",
        "statement_request",
        "active",
    )
    assert unit.subject == "Januar"
    assert unit.description == "Den Kontoauszug für Januar senden"
    assert unit.evidence[0].quote == localized_task.state.sources[0].text
    assert result.explanation.summary == "Der Kontoauszug für Januar wird angefordert."
    assert validate_decision_output(localized_task, localized_oracle) == []
    assert task.model_dump() == original_task and oracle.model_dump() == original_oracle


def test_explicit_quote_translation_keeps_narrow_exact_evidence() -> None:
    task, oracle, translations = _case()
    translations["January statement"] = "Kontoauszug für Januar"
    _, localized_oracle = localize_case(task, oracle, translations)
    result = localized_oracle.results[0]
    assert isinstance(result, RequestUnitsResult) and result.answer is not None
    assert result.answer.units[0].evidence[0].quote == "Kontoauszug für Januar"


@pytest.mark.parametrize(
    "missing", ["Identify the request.", "Send a statement", "The January statement is requested."]
)
def test_missing_prose_translation_cannot_silently_publish_english(missing: str) -> None:
    task, oracle, translations = _case()
    del translations[missing]
    with pytest.raises(ValueError, match="complete localization"):
        localize_case(task, oracle, translations)


def test_untranslated_subject_must_still_occur_in_localized_evidence() -> None:
    task, oracle, translations = _case()
    del translations["January"]
    with pytest.raises(ValueError, match="invalid evidence"):
        localize_case(task, oracle, translations)


def test_localization_cannot_bypass_explanation_length_limit() -> None:
    task, oracle, translations = _case()
    translations["The January statement is requested."] = "A" * 401
    with pytest.raises(ValueError):
        localize_case(task, oracle, translations)


@pytest.mark.parametrize("invalid", ["quote", "source", "disallowed", "subject"])
def test_invalid_original_evidence_is_not_repaired_by_localization(invalid: str) -> None:
    task, oracle, translations = _case()
    result = oracle.results[0]
    assert isinstance(result, RequestUnitsResult) and result.answer is not None
    unit = result.answer.units[0]
    if invalid == "quote":
        unit.evidence[0].quote = "Invented evidence"
    elif invalid == "source":
        unit.evidence[0].sourceId = "missing-source"
    elif invalid == "subject":
        unit.subject = "Unsupported subject"
    else:
        task.questions[0].allowedSourceIds = []
    with pytest.raises(ValueError, match="Authored decision has invalid evidence"):
        localize_case(task, oracle, translations)


@pytest.mark.parametrize("quote", ["", " ", "Erfundener Nachweis"])
def test_invalid_explicit_quote_translation_cannot_widen_to_full_source(quote: str) -> None:
    task, oracle, translations = _case()
    translations["January statement"] = quote
    with pytest.raises(ValueError, match="Explicit localized quote is not grounded"):
        localize_case(task, oracle, translations)
